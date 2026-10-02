"""Structured JSON logging with secret redaction."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import UTC, datetime

LOG_FIELDS = (
    "request_id",
    "session_id",
    "agent_node",
    "tool_name",
    "tool_arguments",
    "tool_duration",
    "tool_result_status",
    "risk",
    "model",
    "token_usage",
    "error",
    "event",
)
SECRET_VALUE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[bpas]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16})")
MAX_FIELD_CHARS = 2000


def redact(value: object) -> object:
    if isinstance(value, str):
        return SECRET_VALUE.sub("[REDACTED]", value)[:MAX_FIELD_CHARS]
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        for name in LOG_FIELDS:
            if hasattr(record, name):
                payload[name] = redact(getattr(record, name))
        if record.exc_info:
            payload["error"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str | None = None) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("coding_agent")
    root.handlers[:] = [handler]
    root.setLevel((level or os.environ.get("LOG_LEVEL") or "INFO").upper())
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"coding_agent.{name}")
