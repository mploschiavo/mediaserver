"""BootstrapHookDispatcher — Strategy for importing + invoking declarative hooks.

ADR-0015 Phase 7c. Pre-Phase-7c five hook-dispatch methods
(``_import_hook``, ``_hook_context``, ``_invoke_hook_with_context``,
``_invoke_hook``, ``_apply_runtime_config_policy``) lived on
:class:`RunBootstrapJobRunner` in commands/.

The dispatcher handles two cases:

1. ``call_handler`` operations in the phase plan — invoke a
   resolved hook with the standard ``hook_context`` (kube,
   namespace, deployment helpers, secret reader, log probe).
2. ``runtime_config_policy_handler`` — invoke with a wider
   context that includes the operator-tunable cfg fields the
   policy needs.

Strategy pattern: the runner hands this class the context-
provider callable; the dispatcher binds it at invoke time so
the runner can change context fields between phase calls.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Callable

from media_stack.core.exceptions import ConfigError


class BootstrapHookDispatcher:
    """Strategy: import a hook spec + invoke with a filtered context."""

    def __init__(
        self, context_provider: Callable[[], dict[str, object]],
    ) -> None:
        self._context_provider = context_provider

    def import_hook(self, spec: str) -> Callable[..., None]:
        module_name, symbol_name = spec.split(":", 1)
        module = importlib.import_module(module_name)
        hook = getattr(module, symbol_name, None)
        if not callable(hook):
            raise ConfigError(f"Hook '{spec}' did not resolve to a callable")
        return hook

    def invoke_hook(self, hook: Callable[..., None], *, hook_name: str) -> None:
        self.invoke_hook_with_context(
            hook, hook_name=hook_name, context=self._context_provider(),
        )

    def invoke_hook_with_context(
        self,
        hook: Callable[..., None],
        *,
        hook_name: str,
        context: dict[str, object],
    ) -> None:
        signature = inspect.signature(hook)
        accepts_kwargs = any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )
        if accepts_kwargs:
            hook(**context)
            return

        accepted = {
            key: value for key, value in context.items() if key in signature.parameters
        }
        required_missing = [
            name
            for name, param in signature.parameters.items()
            if param.default is inspect.Parameter.empty
            and param.kind
            in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
            and name not in accepted
        ]
        if required_missing:
            raise ConfigError(
                f"Hook '{hook_name}' requires unsupported parameters: "
                f"{', '.join(required_missing)}"
            )
        hook(**accepted)


__all__ = ["BootstrapHookDispatcher"]
