"""Runner event registry wiring.

Per ADR-0012: a single ``RunnerEventRegistryWiring`` class owns the
operation-handler coercion + event-registry build helpers. Module-level
aliases preserve the existing import-as-function call sites
(``build_runner_event_registry``, ``build_runner_operation_registry``,
``_coerce_base_handlers``) so callers and tests do not have to change.
"""

from __future__ import annotations

from typing import Any, Callable

from .runner_operations_service import RunnerEventRegistry

OperationFn = Callable[..., Any]

# Aliases: when a handler is registered under one name, also register it
# under the alias so callers can use either.
_ALIASES: dict[str, str] = {
    "qbit_login": "torrent_client_login",
    "setup_qbit_categories": "setup_torrent_categories",
}


class RunnerOperationHandlers:
    """Dict-backed container for operation handler wiring.

    Accepts arbitrary ``**kwargs`` mapping operation names to callables.
    No hardcoded field list -- adding a new service just means passing an
    extra keyword argument.
    """

    def __init__(self, **kwargs: OperationFn | None) -> None:
        self._handlers: dict[str, OperationFn] = {
            k: v for k, v in kwargs.items() if callable(v)
        }

    def to_handler_map(self) -> dict[str, OperationFn]:
        out = dict(self._handlers)
        for source, alias in _ALIASES.items():
            fn = out.get(source)
            if fn is not None:
                out.setdefault(alias, fn)
        return out


class RunnerEventRegistryWiring:
    """Builders that coerce handler containers into a ``RunnerEventRegistry``.

    Per ADR-0012, all helpers are plain instance methods; module-level
    aliases below preserve the import-as-function call sites.
    """

    def coerce_base_handlers(
        self,
        handlers: dict[str, OperationFn] | RunnerOperationHandlers | None,
    ) -> dict[str, OperationFn] | None:
        if handlers is None:
            return None
        if isinstance(handlers, RunnerOperationHandlers):
            return handlers.to_handler_map()
        return dict(handlers)

    def build_runner_event_registry(
        self,
        *,
        base_handlers: (
            dict[str, OperationFn] | RunnerOperationHandlers | None
        ) = None,
        base_event_handlers: (
            dict[str, dict[str, OperationFn]] | None
        ) = None,
        event_handler_specs: dict[str, Any] | None = None,
        operation_handler_specs: dict[str, Any] | None = None,
    ) -> RunnerEventRegistry:
        return RunnerEventRegistry.from_maps(
            handlers=self.coerce_base_handlers(base_handlers),
            event_handlers=base_event_handlers,
            event_handler_specs=event_handler_specs,
            handler_specs=operation_handler_specs,
        )

    def build_runner_operation_registry(
        self,
        handlers: (
            dict[str, OperationFn] | RunnerOperationHandlers | None
        ) = None,
        *,
        operation_handler_specs: dict[str, Any] | None = None,
        event_handler_specs: dict[str, Any] | None = None,
    ) -> RunnerEventRegistry:
        """Compatibility wrapper for older callsites.

        Prefer ``build_runner_event_registry`` for new code.
        """
        return self.build_runner_event_registry(
            base_handlers=handlers,
            event_handler_specs=event_handler_specs,
            operation_handler_specs=operation_handler_specs,
        )


_INSTANCE = RunnerEventRegistryWiring()

# Module-level aliases for every public + underscore name (ADR-0012).
_coerce_base_handlers = _INSTANCE.coerce_base_handlers
build_runner_event_registry = _INSTANCE.build_runner_event_registry
build_runner_operation_registry = _INSTANCE.build_runner_operation_registry
