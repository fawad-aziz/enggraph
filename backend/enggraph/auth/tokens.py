"""Signed bearer session tokens.

A session token is `<public>.<hmac>`: the public part is random, the signature
is HMAC-SHA256 over it using `ENGGRAPH_SESSION_SECRET`. Postgres stores only
the SHA-256 digest of the full token, so a database leak does not hand out
live sessions, and the signature check rejects forged tokens before any DB
lookup.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

PUBLIC_BYTES = 32


def mint_token(secret: str, public: str | None = None) -> str:
    if not secret:
        raise ValueError("session signing secret is required")
    public = public or secrets.token_urlsafe(PUBLIC_BYTES)
    signature = hmac.new(secret.encode("utf-8"), public.encode("utf-8"), hashlib.sha256)
    return f"{public}.{signature.hexdigest()}"


def verify_token(secret: str, token: str) -> str | None:
    """Return the public part when the signature is valid, else None."""
    if not secret:
        return None
    public, sep, signature = token.rpartition(".")
    if not public or not sep:
        return None
    expected = hmac.new(secret.encode("utf-8"), public.encode("utf-8"), hashlib.sha256)
    return public if hmac.compare_digest(expected.hexdigest(), signature) else None


def token_hash(token: str) -> str:
    """The only form of the token that is persisted."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
