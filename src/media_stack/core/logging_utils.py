"""Structured logging helpers for scripts."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

DEFAULT_LEVEL = os.environ.get("MEDIA_STACK_LOG_LEVEL", "INFO").upper()


class LoggingUtils:
    """Helpers for configuring and emitting structured log events."""

    @staticmethod
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

    @staticmethod
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


# ---------------------------------------------------------------------------
# Singleton + backward-compat module-level references
# ---------------------------------------------------------------------------

_instance = LoggingUtils()
configure_logging = _instance.configure_logging
log_event = _instance.log_event


_swallow_log = logging.getLogger("media_stack")


def log_swallowed(exc: BaseException, context: str = "") -> None:
    """Debug-log an intentionally-swallowed exception.

    Use in narrow ``except`` handlers where the exception is
    deliberately non-fatal but worth tracing at DEBUG level. The
    indirection (vs ``logging.getLogger("media_stack").debug(...)``
    inline) keeps the duplicate-string ratchet honest and lets the
    project change the format in one place.
    """
    if context:
        _swallow_log.debug("[DEBUG] Swallowed (%s): %s", context, exc)
    else:
        _swallow_log.debug("[DEBUG] Swallowed: %s", exc)
