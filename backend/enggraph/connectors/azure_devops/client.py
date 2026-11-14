"""Real Azure DevOps Services REST client (dev.azure.com, PAT auth).

- Every request carries `api-version` (pinned, never user-controlled).
- Pagination: continuation-token endpoints and searchCriteria[skip] paging.
- Throttling: 429 honoured via Retry-After, bounded retry budget.
- Transient 5xx/transport errors retried with exponential backoff.
- 401/403 raise typed errors immediately (auth issues never retried).
- The PAT lives only in the Authorization header; log redaction is enforced
  by logging_setup (authorization/pat values are stripped from all records).
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Iterator
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import httpx

from enggraph.connectors.errors import (
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorError,
    ConnectorPermissionError,
    ConnectorThrottled,
    ConnectorUnavailable,
)
from enggraph.logging_setup import get_logger

log = get_logger("enggraph.connectors.ado")

API_VERSION = "7.2"
# Paging bounds: a provider response that re-offers the same continuation
# token (or keeps returning full pages forever) must not spin the ingest loop.
MAX_COLLECTION_PAGES = 200


def services_base_url(organization: str) -> str:
    """Organization name is validated (no colons/slashes/whitespace)."""
    if not organization.strip():
        raise ConnectorConfigError("organization must be non-empty")
    if any(c in organization for c in ":/? \t\n"):
        raise ConnectorConfigError(f"invalid organization name: {organization!r}")
    return f"https://dev.azure.com/{organization.strip()}"


def _continuation_token(body: dict[str, Any]) -> str | None:
    """Continuation tokens appear in body and/or @nextLink depending on API."""
    raw = body.get("continuationToken")
    if raw:
        return str(raw)
    link = body.get("@nextLink")
    if link:
        query = parse_qs(urlparse(link).query)
        for key in ("$continuationToken", "continuationToken"):
            if key in query:
                return str(query[key][0])
    return None


class AdoClient:
    def __init__(
        self,
        organization: str,
        pat: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        page_size: int = 100,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        throttle_default: float = 1.0,
        backoff_base: float = 1.0,
    ) -> None:
        if not pat or not pat.strip():
            raise ConnectorConfigError("Azure DevOps PAT is required")
        credentials = base64.b64encode(f":{pat.strip()}".encode()).decode()
        headers = {
            "Authorization": f"Basic {credentials}",
            "Accept": "application/json",
            "User-Agent": "enggraph/0.1",
        }
        self.page_size = page_size
        self.max_retries = max_retries
        self._sleep = sleep
        self._throttle_default = throttle_default
        self._backoff_base = backoff_base
        self._http = httpx.Client(
            base_url=services_base_url(organization),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> AdoClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ core

    def _backoff(self, attempt: int) -> float:
        # `min` of float/int unifies to Any in mypy; the arithmetic is float.
        return float(min(30.0, self._backoff_base * (2**attempt)))

    def _retry_after(self, response: httpx.Response) -> float:
        raw = response.headers.get("Retry-After", "")
        if raw.isdigit():
            return max(0.0, float(raw))
        return self._throttle_default

    def request(
        self, path: str, params: dict[str, Any] | None = None, method: str = "GET"
    ) -> dict[str, Any] | None:
        """Single request with api-version injection and retry handling.

        Returns parsed JSON body, or None for empty/204 responses.
        Raises typed ConnectorError subclasses; never retries auth failures.
        """
        merged = dict(params or {})
        merged.setdefault("api-version", API_VERSION)
        attempt = 0
        while True:
            try:
                response = self._http.request(method, path, params=merged)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= self.max_retries:
                    raise ConnectorUnavailable(
                        f"transport failure after {attempt} retries: {type(exc).__name__}"
                    ) from exc
                log.warning("transient transport error; retry %d for %s", attempt + 1, path)
                self._sleep(self._backoff(attempt))
                attempt += 1
                continue
            status = response.status_code
            if status == 429:
                if attempt >= self.max_retries:
                    raise ConnectorThrottled(f"throttled past retry budget for {path}")
                delay = self._retry_after(response)
                log.warning("429 throttled for %s; waiting %.1fs", path, delay)
                self._sleep(delay)
                attempt += 1
                continue
            if status == 401:
                raise ConnectorAuthError("authentication failed (401): PAT invalid or expired")
            if status == 403:
                raise ConnectorPermissionError(
                    "access denied (403): PAT lacks required scope for this resource"
                )
            if status >= 500:
                if attempt >= self.max_retries:
                    raise ConnectorUnavailable(f"server error {status} past retry budget")
                log.warning("ADO 5xx (%d) for %s; retry %d", status, path, attempt + 1)
                self._sleep(self._backoff(attempt))
                attempt += 1
                continue
            if status == 204:
                return None
            if not response.is_success:
                raise ConnectorError(f"unexpected status {status} for {path}")
            if not response.content:
                return None
            # httpx types `.json()` as Any; the payload shape is asserted here.
            return cast(dict[str, Any], response.json())

    # ------------------------------------------------------------------ paging

    def iter_collection(
        self, path: str, params: dict[str, Any] | None = None, value_key: str = "value"
    ) -> Iterator[dict[str, Any]]:
        """Continuation-token paging (count/value responses). Bounded by data."""
        merged = dict(params or {})
        token = merged.pop("continuationToken", None)
        seen = 0
        pages = 0
        last_token = None
        while True:
            pages += 1
            if pages > MAX_COLLECTION_PAGES:
                raise ConnectorError(f"paging exceeded {MAX_COLLECTION_PAGES} pages for {path}")
            if token is not None and token == last_token:
                raise ConnectorError(f"continuation token repeated for {path}")
            last_token = token
            if token:
                merged["continuationToken"] = token
            body = self.request(path, merged)
            if not isinstance(body, dict):
                return
            items = body.get(value_key)
            if not isinstance(items, list):
                return
            yield from items
            seen += len(items)
            token = _continuation_token(body)
            if not token or not items:
                return

    def iter_skip_paged(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        value_key: str = "value",
        page_size: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """searchCriteria[skip]/[pageSize] paging (git commits)."""
        size = page_size or self.page_size
        skip = 0
        pages = 0
        while True:
            pages += 1
            if pages > MAX_COLLECTION_PAGES:
                raise ConnectorError(
                    f"skip paging exceeded {MAX_COLLECTION_PAGES} pages for {path}"
                )
            merged = dict(params or {})
            merged["searchCriteria[skip]"] = skip
            merged["searchCriteria[pageSize]"] = size
            body = self.request(path, merged)
            items = body.get(value_key) if isinstance(body, dict) else None
            if not isinstance(items, list):
                return
            yield from items
            if len(items) < size:
                return
            skip += size

    def post_json(
        self, path: str, payload: dict[str, Any], params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """POST with the same api-version injection; used by the WIQL query API."""
        merged = dict(params or {})
        merged.setdefault("api-version", API_VERSION)
        response = self._http.request("POST", path, json=payload, params=merged)
        status = response.status_code
        if status == 401:
            raise ConnectorAuthError("authentication failed (401): PAT invalid or expired")
        if status == 403:
            raise ConnectorPermissionError(
                "access denied (403): PAT lacks required scope for this resource"
            )
        if not response.is_success:
            raise ConnectorError(f"unexpected status {status} for {path}")
        return response.json() if response.content else None


def build_client(
    organization: str, pat: str, transport: httpx.BaseTransport | None = None
) -> AdoClient:
    """Production entry point; transport hook exists for contract tests only."""
    return AdoClient(organization, pat, transport=transport)
