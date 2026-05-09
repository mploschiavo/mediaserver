"""Structured logging helpers for scripts.

ADR-0012: top-level FunctionDef count must stay at zero. All helpers
are bundled on ``LoggingUtils`` as plain instance methods (no
``@staticmethod``) and re-exported as module-level aliases so every
existing
``from media_stack.core.logging_utils import configure_logging,
log_event, log_swallowed`` keeps working with the same signature.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "DEFAULT_LEVEL",
    "LoggingUtils",
    "configure_logging",
    "log_event",
    "log_swallowed",
]


DEFAULT_LEVEL = os.environ.get("MEDIA_STACK_LOG_LEVEL", "INFO").upper()


# Cached logger for swallowed-exception traces. Module-level so
# ``log_swallowed`` doesn't allocate a fresh ``Logger`` per call.
_SWALLOW_LOG = logging.getLogger("media_stack")


class LoggingUtils:
    """Helpers for configuring and emitting structured log events.

    Plain instance methods — no ``@staticmethod`` — so the class is a
    legitimate dispatch surface. Module-level aliases below preserve
    the original free-function names so callers keep importing
    ``configure_logging`` / ``log_event`` / ``log_swallowed`` without
    churn.
    """

    def configure_logging(self, level: str = DEFAULT_LEVEL) -> logging.Logger:
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

    def log_event(
        self, logger: logging.Logger, level: int, event: str, **fields: Any
    ) -> None:
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

    def log_swallowed(self, exc: BaseException, context: str = "") -> None:
        """Debug-log an intentionally-swallowed exception.

        Use in narrow ``except`` handlers where the exception is
        deliberately non-fatal but worth tracing at DEBUG level. The
        indirection (vs ``logging.getLogger("media_stack").debug(...)``
        inline) keeps the duplicate-string ratchet honest and lets the
        project change the format in one place.
        """
        if context:
            _SWALLOW_LOG.debug("[DEBUG] Swallowed (%s): %s", context, exc)
        else:
            _SWALLOW_LOG.debug("[DEBUG] Swallowed: %s", exc)


_INSTANCE = LoggingUtils()


# Module-level aliases. These exist so callers can keep writing
# ``from media_stack.core.logging_utils import configure_logging`` with
# the same call signature as the legacy free function.
configure_logging = _INSTANCE.configure_logging
log_event = _INSTANCE.log_event
log_swallowed = _INSTANCE.log_swallowed
