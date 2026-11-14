from __future__ import annotations


class ConnectorError(Exception):
    """Base class for connector failures. Never carries credentials."""


class ConnectorConfigError(ConnectorError):
    """Connector is misconfigured (missing PAT/organization)."""


class ConnectorAuthError(ConnectorError):
    """401 — PAT invalid/expired. Not retried."""


class ConnectorPermissionError(ConnectorError):
    """403 — PAT is valid but lacks access to this resource. Not retried."""


class ConnectorThrottled(ConnectorError):
    """429 persisted past the retry budget."""


class ConnectorUnavailable(ConnectorError):
    """Persistent 5xx / transport failure past the retry budget."""
