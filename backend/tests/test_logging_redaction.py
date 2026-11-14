from __future__ import annotations

import logging

from enggraph.logging_setup import RedactionFilter, redact


def test_redact_authorization_header_pair():
    msg = "GET failed Authorization: Basic dGhpcy1pcy1hLXBhdA"
    out = redact(msg)
    assert "dGhpcy1pcy1hLXBhdA" not in out
    assert "[REDACTED]" in out


def test_redact_pat_and_token_key_value_pairs():
    secrets = ["abc123def456", "4f3c9a0b", "S3cr3t!", "abcdef"]
    texts = [
        "config pat=abc123def456 loaded",
        "using token: 4f3c9a0b",
        "password=S3cr3t!",
        "api_key = abcdef",
    ]
    for text, secret in zip(texts, secrets, strict=True):
        out = redact(text)
        assert "[REDACTED]" in out
        assert secret not in out


def test_filter_redacts_sensitive_record_fields():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="ok",
        args=(),
        exc_info=None,
    )
    record.__dict__["pat"] = "supersecret"
    record.__dict__["authorization"] = "Bearer xyz"
    RedactionFilter().filter(record)
    assert record.__dict__["pat"] == "[REDACTED]"
    assert record.__dict__["authorization"] == "[REDACTED]"


def test_filter_redacts_message():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="sync failed pat=SECRET999",
        args=(),
        exc_info=None,
    )
    RedactionFilter().filter(record)
    assert "SECRET999" not in record.getMessage()
