"""Named operation registry used by the bootstrap runner."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from .adapter_factory import load_adapter_class
from .enums import RunnerEvent

OperationHandler = Callable[..., Any]


class RunnerOperationHelpers:
    """Helper surface for ``RunnerOperationRegistry``.

    Plain instance methods only (ADR-0012). Module-level ``_INSTANCE``
    plus aliases preserve the underscore-prefixed import + ``mock.patch``
    surface (``_load_handler_from_spec``, ``_coerce_event_key``,
    ``_normalize_event_handler_specs``) so existing call sites keep
    working unchanged.
    """

    def load_handler_from_spec(self, spec: str) -> OperationHandler:
        handler = load_adapter_class(spec, base_class=None, role="operation_handler")
        if not callable(handler):
            raise TypeError(f"Operation handler spec '{spec}' does not resolve to a callable.")
        return handler

    def coerce_event_key(self, raw_event: str) -> str:
        return RunnerEvent.from_value(raw_event).value

    def normalize_event_handler_specs(
        self, specs: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        # Dispatch through the module so ``mock.patch`` on the alias keeps
        # intercepting (ADR-0012 design principle 3).
        _module = sys.modules[__name__]
        normalized: dict[str, dict[str, Any]] = {}
        for key, value in specs.items():
            event_key_raw = str(key or "").strip()
            if not event_key_raw:
                continue
            if isinstance(value, dict):
                event_key = _module._coerce_event_key(event_key_raw)
                normalized[event_key] = dict(value)
                continue

            # Backward-compat: treat flat maps as RUN event handlers.
            handler_key = event_key_raw
            normalized.setdefault(RunnerEvent.RUN.value, {})[handler_key] = value
        return normalized


# Module-level singleton + aliases (ADR-0012 pattern).
_INSTANCE = RunnerOperationHelpers()

_load_handler_from_spec = _INSTANCE.load_handler_from_spec
_coerce_event_key = _INSTANCE.coerce_event_key
_normalize_event_handler_specs = _INSTANCE.normalize_event_handler_specs


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
        # Dispatch through the module so ``mock.patch`` on the helper
        # aliases keeps intercepting (ADR-0012 design principle 3).
        _module = sys.modules[__name__]
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
                event_key = _module._coerce_event_key(str(event_name or ""))
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

        normalized_specs = _module._normalize_event_handler_specs(event_handler_specs)
        for event_name, specs in normalized_specs.items():
            merged = merged_events.setdefault(event_name, {})
            for handler_name, spec in specs.items():
                key = str(handler_name or "").strip()
                if not key:
                    continue
                if spec is None or str(spec).strip() == "":
                    merged.pop(key, None)
                    continue
                merged[key] = _module._load_handler_from_spec(str(spec))

        run_handlers = dict(merged_events.get(RunnerEvent.RUN.value, {}))
        return cls(handlers=run_handlers, event_handlers=merged_events)

    def invoke_event(
        self,
        event: RunnerEvent | str,
        handler: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        _module = sys.modules[__name__]
        event_key = (
            event.value
            if isinstance(event, RunnerEvent)
            else _module._coerce_event_key(str(event))
        )
        handler_key = str(handler or "").strip()
        event_map = self.event_handlers.get(event_key, {})
        fn = event_map.get(handler_key)
        if fn is None:
            raise KeyError(
                f"Runner event handler not registered: event={event_key} handler={handler_key}"
            )
        return fn(*args, **kwargs)

    def invoke(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        key = str(operation or "")
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
        _module = sys.modules[__name__]
        event_key = (
            event.value
            if isinstance(event, RunnerEvent)
            else _module._coerce_event_key(str(event))
        )
        handler_key = str(handler or "").strip()
        return handler_key in self.event_handlers.get(event_key, {})

    def handlers_for_event(self, event: RunnerEvent | str) -> dict[str, OperationHandler]:
        _module = sys.modules[__name__]
        event_key = (
            event.value
            if isinstance(event, RunnerEvent)
            else _module._coerce_event_key(str(event))
        )
        return dict(self.event_handlers.get(event_key, {}))


RunnerEventRegistry = RunnerOperationRegistry
