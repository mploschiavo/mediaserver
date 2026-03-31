"""Base contract for media-server bootstrap adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..enums import RunnerOperation

InvokeOperationFn = Callable[..., Any]
RunOptionalStepFn = Callable[..., None]
LogFn = Callable[[str], None]


@dataclass
class MediaServerAdapterContext:
    backend: str
    runtime: Any
    invoke_operation: InvokeOperationFn
    run_optional_step: RunOptionalStepFn
    log: LogFn

    def invoke(self, operation: RunnerOperation | str, *args: Any, **kwargs: Any) -> Any:
        return self.invoke_operation(operation, *args, **kwargs)

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
