"""Base contract for media-server bootstrap adapters.

Pure-domain port: ``MediaServerAdapterBase`` is the protocol every
concrete media-server adapter satisfies; ``MediaServerAdapterContext``
is the value object the orchestration layer hands each adapter at
construction time. No I/O, no framework deps — only the standard
library + the service-layer ``RunnerEvent`` enum (which is itself a
pure ``StrEnum``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from media_stack.services.enums import RunnerEvent

InvokeOperationFn = Callable[..., Any]
RunOptionalStepFn = Callable[..., None]
LogFn = Callable[[str], None]


@dataclass
class MediaServerAdapterContext:
    backend: str
    runtime: Any
    run_optional_step: RunOptionalStepFn
    log: LogFn
    invoke_event: InvokeOperationFn | None = None
    invoke_operation: InvokeOperationFn | None = None

    def invoke(
        self,
        event: RunnerEvent | str,
        handler: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if callable(self.invoke_event):
            return self.invoke_event(event, handler, *args, **kwargs)
        if callable(self.invoke_operation):
            return self.invoke_operation(handler, *args, **kwargs)
        raise ValueError(
            "MediaServerAdapterContext requires invoke_event (or legacy invoke_operation)."
        )

    def run_optional(
        self,
        *,
        enabled: bool,
        required: bool,
        action: Callable[[], None],
        warning_message: str,
    ) -> None:
        self.run_optional_step(
            enabled=enabled,
            required=required,
            action=action,
            warning_message=warning_message,
        )


@dataclass
class MediaServerAdapterBase:
    context: MediaServerAdapterContext

    def run_prewarm_mode(self) -> None:
        return

    def run_home_rails_mode(self) -> None:
        return

    def run_post_servarr_pre_hygiene_steps(self) -> None:
        return

    def run_post_servarr_post_hygiene_steps(self) -> None:
        return
