"""PBKDF2 password hashing.

Raw passwords never reach storage, logs, API responses, or commits. The stored
form is `pbkdf2_sha256$<iterations>$<salt_hex>$<digest_hex>` (per-user random
salt, constant-time comparison).
"""

from __future__ import annotations

import hashlib
import hmac
import os

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 210_000
SALT_BYTES = 16


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password must be non-empty")
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"{ALGORITHM}${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification; malformed hashes are a mismatch, not a crash."""
    parts = stored.split("$")
    if len(parts) != 4:
        return False
    algorithm, iterations, salt_hex, digest_hex = parts
    if algorithm != ALGORITHM:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        iters = int(iterations)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    return hmac.compare_digest(digest.hex(), digest_hex)
