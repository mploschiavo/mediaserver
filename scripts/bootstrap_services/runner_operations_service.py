"""Named operation registry used by the bootstrap runner."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable

from .enums import RunnerOperation

OperationHandler = Callable[..., Any]


def _load_handler_from_spec(spec: str) -> OperationHandler:
    raw = str(spec or "").strip()
    if ":" not in raw:
        raise ValueError(
            f"Invalid operation handler spec '{raw}'. Expected 'module.submodule:callable_name'."
        )
    module_name, attr_name = raw.rsplit(":", 1)
    module_name = module_name.strip()
    attr_name = attr_name.strip()
    if not module_name or not attr_name:
        raise ValueError(
            f"Invalid operation handler spec '{raw}'. Expected 'module.submodule:callable_name'."
        )
    module = importlib.import_module(module_name)
    handler = getattr(module, attr_name, None)
    if handler is None or not callable(handler):
        raise TypeError(f"Operation handler spec '{raw}' does not resolve to a callable.")
    return handler


@dataclass
class RunnerOperationRegistry:
    handlers: dict[str, OperationHandler] = field(default_factory=dict)

    @classmethod
    def from_maps(
        cls,
        *,
        handlers: dict[str, OperationHandler] | None = None,
        handler_specs: dict[str, Any] | None = None,
    ) -> "RunnerOperationRegistry":
        merged: dict[str, OperationHandler] = dict(handlers or {})
        if handler_specs is None:
            return cls(handlers=merged)
        if not isinstance(handler_specs, dict):
            raise ValueError("adapter_hooks.operation_handlers must be an object/map.")
        for operation_name, spec in handler_specs.items():
            key = str(operation_name or "").strip()
            if not key:
                continue
            if spec is None or str(spec).strip() == "":
                merged.pop(key, None)
                continue
            merged[key] = _load_handler_from_spec(str(spec))
        return cls(handlers=merged)

    def invoke(self, operation: RunnerOperation | str, *args: Any, **kwargs: Any) -> Any:
        key = operation.value if isinstance(operation, RunnerOperation) else str(operation or "")
        handler = self.handlers.get(key)
        if handler is None:
            raise KeyError(f"Runner operation not registered: {key}")
        return handler(*args, **kwargs)
