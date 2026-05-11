"""AuthProviderResolver — Strategy for auth-provider middleware + valid set.

Owns the two questions the deploy needs answered about auth:

* For each known auth provider (Authelia / Authentik / none),
  what middleware name should the edge router emit?
* Which auth provider strings is the operator allowed to pass?

Both answers are computed by merging three sources:

1. The catalog YAML's built-in defaults
   (``catalog.auth_provider_middleware_defaults``).
2. The auth provider registry's built-in specs
   (``load_builtin_auth_provider_specs()``).
3. The profile-driven ``adapter_hooks.edge.auth_provider_middleware_defaults``
   override map.

Strategy pattern: the resolver is the strategy for "how do we
combine these three sources?". Callers don't see the merging
logic — they call :meth:`middleware_defaults` or
:meth:`valid_providers` and get the resolved answer.
"""

from __future__ import annotations

from media_stack.core.auth.provider_registry import (
    load_builtin_auth_provider_specs,
    merge_auth_provider_defaults,
)
from media_stack.core.controller_profile import load_bootstrap_profile_catalog
from media_stack.cli.workflows.deploy_config.bootstrap_config_loader import (
    BootstrapConfigLoader,
)


class AuthProviderResolver:
    """Strategy: auth-provider middleware defaults + the valid set."""

    def __init__(self, loader: BootstrapConfigLoader) -> None:
        self._loader = loader

    def middleware_defaults(self) -> dict[str, str]:
        """Resolved per-provider middleware name map.

        Combines the catalog default + registry built-ins + hook
        override. Returned dict keys are normalised lowercase
        provider names; values are middleware identifiers the edge
        router emits.
        """
        catalog = load_bootstrap_profile_catalog()
        hooks = self._loader.edge_hooks()
        hook_defaults: dict[str, str] = {}
        raw = hooks.get("auth_provider_middleware_defaults")
        if isinstance(raw, dict):
            for raw_key, raw_value in raw.items():
                key = str(raw_key or "").strip().lower()
                if not key:
                    continue
                hook_defaults[key] = str(raw_value or "").strip()

        provider_keys: list[str] = []
        seen: set[str] = set()
        for raw_key in (
            *tuple(catalog.auth_providers),
            str(catalog.auth_disabled_provider or "").strip().lower(),
            *(spec.key for spec in load_builtin_auth_provider_specs()),
            *tuple(hook_defaults.keys()),
        ):
            key = str(raw_key or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            provider_keys.append(key)
        return merge_auth_provider_defaults(
            provider_keys=tuple(provider_keys),
            catalog_defaults=dict(catalog.auth_provider_middleware_defaults or {}),
            override_defaults=hook_defaults,
        )

    def valid_providers(self) -> tuple[str, ...]:
        """Sorted-by-insertion list of acceptable auth-provider strings.

        Validation reads from this — if the operator passes
        ``--auth-provider`` (or ``AUTH_PROVIDER`` env), the value
        must appear in this tuple.
        """
        catalog = load_bootstrap_profile_catalog()
        values: list[str] = []
        seen: set[str] = set()
        for token in (
            *tuple(catalog.auth_providers),
            str(catalog.auth_disabled_provider or "").strip().lower(),
            *tuple(self.middleware_defaults().keys()),
        ):
            normalized = str(token or "").strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            values.append(normalized)
        return tuple(values)


__all__ = ["AuthProviderResolver"]
