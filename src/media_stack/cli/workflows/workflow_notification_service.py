"""Default workflow notifier."""

from __future__ import annotations

from media_stack.core.cli_common import err, info, warn


class WorkflowNotificationService:
    """Forwards workflow messages to the existing CLI logger."""

    def info(self, message: str) -> None:
        info(message)

    def warn(self, message: str) -> None:
        warn(message)

    def error(self, message: str) -> None:
        err(message)
