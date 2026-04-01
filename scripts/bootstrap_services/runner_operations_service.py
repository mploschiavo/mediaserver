"""Named operation registry used by the bootstrap runner."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable

from .enums import RunnerEvent, RunnerOperation

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


def _coerce_event_key(raw_event: str) -> str:
    return RunnerEvent.from_value(raw_event).value


def _normalize_event_handler_specs(specs: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for key, value in specs.items():
        event_key_raw = str(key or "").strip()
        if not event_key_raw:
            continue
        if isinstance(value, dict):
            event_key = _coerce_event_key(event_key_raw)
            normalized[event_key] = dict(value)
            continue

        # Backward-compat: treat flat maps as RUN event handlers.
        handler_key = event_key_raw
        normalized.setdefault(RunnerEvent.RUN.value, {})[handler_key] = value
    return normalized


@dataclass
class RunnerOperationRegistry:
    handlers: dict[str, OperationHandler] = field(default_factory=dict)
    event_handlers: dict[str, dict[str, OperationHandler]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.handlers:
            run_handlers = self.event_handlers.setdefault(RunnerEvent.RUN.value, {})
            run_handlers.update(self.handlers)

    @classmethod
    def from_maps(
        cls,
        *,
        handlers: dict[str, OperationHandler] | None = None,
        event_handlers: dict[str, dict[str, OperationHandler]] | None = None,
        handler_specs: dict[str, Any] | None = None,
        event_handler_specs: dict[str, Any] | None = None,
    ) -> "RunnerOperationRegistry":
        merged_events: dict[str, dict[str, OperationHandler]] = {}

        base_flat = dict(handlers or {})
        if base_flat:
            merged_events.setdefault(RunnerEvent.RUN.value, {}).update(base_flat)

        base_nested = event_handlers or {}
        if base_nested:
            if not isinstance(base_nested, dict):
                raise ValueError("adapter_hooks.event_handlers must be an object/map.")
            for event_name, event_map in base_nested.items():
                if not isinstance(event_map, dict):
                    raise ValueError(
                        "adapter_hooks.event_handlers values must be objects/maps "
                        f"(invalid event '{event_name}')."
                    )
                event_key = _coerce_event_key(str(event_name or ""))
                merged_events.setdefault(event_key, {}).update(
                    {
                        str(handler_name or "").strip(): handler
                        for handler_name, handler in event_map.items()
                        if str(handler_name or "").strip() and callable(handler)
                    }
                )

        # Backward-compat: flat handler_specs map.
        if handler_specs is not None:
            if not isinstance(handler_specs, dict):
                raise ValueError("adapter_hooks.operation_handlers must be an object/map.")
            event_handler_specs = dict(event_handler_specs or {})
            event_handler_specs.setdefault(RunnerEvent.RUN.value, {}).update(handler_specs)

        if event_handler_specs is None:
            run_handlers = dict(merged_events.get(RunnerEvent.RUN.value, {}))
            return cls(handlers=run_handlers, event_handlers=merged_events)

        if not isinstance(event_handler_specs, dict):
            raise ValueError("adapter_hooks.event_handlers must be an object/map.")

        normalized_specs = _normalize_event_handler_specs(event_handler_specs)
        for event_name, specs in normalized_specs.items():
            merged = merged_events.setdefault(event_name, {})
            for handler_name, spec in specs.items():
                key = str(handler_name or "").strip()
                if not key:
                    continue
                if spec is None or str(spec).strip() == "":
                    merged.pop(key, None)
                    continue
                merged[key] = _load_handler_from_spec(str(spec))

        run_handlers = dict(merged_events.get(RunnerEvent.RUN.value, {}))
        return cls(handlers=run_handlers, event_handlers=merged_events)

    def invoke_event(
        self,
        event: RunnerEvent | str,
        handler: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        event_key = event.value if isinstance(event, RunnerEvent) else _coerce_event_key(str(event))
        handler_key = str(handler or "").strip()
        event_map = self.event_handlers.get(event_key, {})
        fn = event_map.get(handler_key)
        if fn is None:
            raise KeyError(
                f"Runner event handler not registered: event={event_key} handler={handler_key}"
            )
        return fn(*args, **kwargs)

    def invoke(self, operation: RunnerOperation | str, *args: Any, **kwargs: Any) -> Any:
        key = operation.value if isinstance(operation, RunnerOperation) else str(operation or "")
        if not key:
            raise KeyError("Runner operation not registered: <empty>")
        handler = self.handlers.get(key)
        if handler is None:
            # Allow direct invocation by fully qualified "EVENT:handler" token.
            if ":" in key:
                event_name, handler_key = key.split(":", 1)
                return self.invoke_event(event_name, handler_key, *args, **kwargs)
            raise KeyError(f"Runner operation not registered: {key}")
        return handler(*args, **kwargs)

    def has_event_handler(self, event: RunnerEvent | str, handler: str) -> bool:
        event_key = event.value if isinstance(event, RunnerEvent) else _coerce_event_key(str(event))
        handler_key = str(handler or "").strip()
        return handler_key in self.event_handlers.get(event_key, {})

    def handlers_for_event(self, event: RunnerEvent | str) -> dict[str, OperationHandler]:
        event_key = event.value if isinstance(event, RunnerEvent) else _coerce_event_key(str(event))
        return dict(self.event_handlers.get(event_key, {}))


RunnerEventRegistry = RunnerOperationRegistry
