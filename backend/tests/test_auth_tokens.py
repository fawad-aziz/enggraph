"""Session token contract tests (auth/tokens.py).

The token is `<public>.<hmac_hex>`, and only its SHA-256 digest is persisted.
These tests lock the format and the verification path, including the cases that
must fail closed: foreign secrets, tampered public parts, non-token strings, and
an unset signing secret.
"""

from __future__ import annotations

import hashlib

import pytest

from enggraph.auth.tokens import mint_token, token_hash, verify_token

SECRET = "test-secret-32-bytes-long-aaaaaaa"
OTHER_SECRET = "another-secret-32-bytes-long-bbbbbbb"


def test_token_is_public_plus_a_hex_signature():
    token = mint_token(SECRET)
    public, sep, signature = token.partition(".")
    assert sep
    assert len(public) >= 32
    assert len(signature) == 64
    int(signature, 16)  # raises if not hex


def test_round_trip_recovers_the_public_part():
    token = mint_token(SECRET)
    public = token.partition(".")[0]
    assert verify_token(SECRET, token) == public


def test_signature_is_deterministic_for_the_same_public_part():
    """Verification depends only on the public part, so minting is reproducible."""
    public = "fixed-public-part"
    assert mint_token(SECRET, public) == mint_token(SECRET, public)
    assert verify_token(SECRET, mint_token(SECRET, public)) == public


def test_each_minted_token_is_unique():
    tokens = {mint_token(SECRET) for _ in range(50)}
    assert len(tokens) == 50


def test_a_token_signed_with_another_secret_is_rejected():
    forged = mint_token(OTHER_SECRET)
    assert verify_token(SECRET, forged) is None


def test_tampering_the_public_part_invalidates_the_token():
    token = mint_token(SECRET)
    public, signature = token.split(".")
    tampered = f"{public}x.{signature}"
    assert verify_token(SECRET, tampered) is None


def test_truncated_signature_is_rejected():
    token = mint_token(SECRET)
    public, signature = token.split(".")
    assert verify_token(SECRET, f"{public}.{signature[:20]}") is None


@pytest.mark.parametrize("token", ["", "nodot", ".abc", "public.", "a.b.c"])
def test_non_token_strings_are_rejected(token):
    assert verify_token(SECRET, token) is None


def test_no_secret_means_no_session():
    """An unset signing secret fails closed, not "accept everything"."""
    token = mint_token(SECRET)
    assert verify_token("", token) is None
    with pytest.raises(ValueError):
        mint_token("")


def test_token_hash_is_a_sha256_digest_of_the_whole_token():
    token = mint_token(SECRET)
    digest = token_hash(token)
    assert digest == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert len(digest) == 64
    # Nothing recognizable from the token survives in the stored form.
    assert token.partition(".")[0] not in digest
    # Two tokens hash to different digests; the digest is stable for one token.
    assert token_hash(token) == digest
    assert token_hash(mint_token(SECRET)) != digest
