"""Structured logging helpers for scripts."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

DEFAULT_LEVEL = os.environ.get("MEDIA_STACK_LOG_LEVEL", "INFO").upper()


def configure_logging(level: str = DEFAULT_LEVEL) -> logging.Logger:
    """Configure root logging once and return a script logger."""
    logger = logging.getLogger("media_stack")
    if logger.handlers:
        return logger

    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Emit a structured JSON log event."""
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": logging.getLevelName(level),
        "event": event,
    }
    for key, value in fields.items():
        if value is not None:
            payload[key] = value
    logger.log(level, json.dumps(payload, sort_keys=True))
