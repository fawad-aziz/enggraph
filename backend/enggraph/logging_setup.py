"""Structured logging with secret redaction.

All log output passes through `redact()` so credentials, PATs,
authorization headers, passwords, and tokens never reach logs
(architecture doc §7: "Redact authorization headers and tokens from logs").
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
from typing import Any

SENSITIVE_KEY_NAMES = {
    "authorization",
    "pat",
    "password",
    "token",
    "secret",
    "api_key",
    "access_token",
    "session_secret",
    "neo4j_password",
    "session_token",
}

# Redact key=value / key: value pairs whose key is sensitive.
_PAIR_RE = re.compile(
    r"(?i)\b(authorization|pat|password|token|secret|api_key|access_token)\b\s*[:=]\s*(\S+)"
)
_HEADER_RE = re.compile(r"(?i)\bauthorization\b\s*:?\s*(?:basic|bearer)\s+\S+")

correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")


def redact(text: str) -> str:
    text = _HEADER_RE.sub("authorization: [REDACTED]", text)
    text = _PAIR_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    return text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.getMessage()))
        for key, value in list(record.__dict__.items()):
            if key in ("msg", "args", "exc_info", "exc_text", "stack_info"):
                continue
            if key in SENSITIVE_KEY_NAMES:
                record.__dict__[key] = "[REDACTED]"
            elif isinstance(value, str):
                record.__dict__[key] = redact(value)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id.get(),
        }
        reserved = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadid",
            "processName", "process", "taskName", "message", "correlation_id",
        }
        for key, value in record.__dict__.items():
            if key not in reserved and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root.handlers.clear()
    root.addHandler(handler)
    root.addFilter(RedactionFilter())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
