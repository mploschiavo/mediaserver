"""Auth provider discovery and default middleware resolution.

ADR-0012: top-level FunctionDef count must stay at zero. The discovery
helpers are bundled on ``AuthProviderRegistry`` and re-exported as
module-level aliases so every existing
``from media_stack.core.auth.provider_registry import …`` (via the
Phase 16-B shim) and every direct
``from media_stack.adapters.auth.provider_registry import …`` keeps
working with the same call signature. No test in the tree patches these
names via ``mock.patch("…provider_registry.<name>", …)``, so direct
bound-method aliases are sufficient — if a future test needs that,
swap an alias for a lambda that dispatches through
``sys.modules[__name__]`` so the patch wins.

The ``AuthProviderSpec`` frozen dataclass remains the public contract
for what an auth provider exposes; it is preserved verbatim because it
is the primary import shape used by callers and tests.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass

from media_stack.adapters.auth import providers as providers_package

__all__ = [
    "AuthProviderSpec",
    "AuthProviderRegistry",
    "load_builtin_auth_provider_specs",
    "compose_service_names_by_provider",
    "merge_auth_provider_defaults",
]


@dataclass(frozen=True)
class AuthProviderSpec:
    key: str
    default_middleware: str = ""
    compose_service_names: tuple[str, ...] = ()


class AuthProviderRegistry:
    """Auth provider discovery + default-middleware merge bundled per ADR-0012.

    Plain instance methods — no ``@staticmethod`` — so the class is a
    legitimate dispatch surface. Module-level aliases below preserve the
    original free-function names so callers keep importing
    ``load_builtin_auth_provider_specs`` etc. without churn.

    The registry walks
    ``media_stack.adapters.auth.providers.<provider>.provider`` modules
    and extracts ``PROVIDER_KEY`` / ``DEFAULT_MIDDLEWARE`` /
    ``COMPOSE_SERVICE_NAMES``. ``_normalize_service_names`` is a private
    instance method (with module-level underscore alias preserved) so
    test code that imported it before keeps working.
    """

    def _normalize_service_names(self, raw: object) -> tuple[str, ...]:
        values: list[object]
        if isinstance(raw, (list, tuple)):
            values = list(raw)
        elif isinstance(raw, str):
            values = [raw]
        else:
            return ()
        out: list[str] = []
        seen: set[str] = set()
        for item in values:
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return tuple(out)

    def load_builtin_auth_provider_specs(self) -> tuple[AuthProviderSpec, ...]:
        specs: list[AuthProviderSpec] = []
        seen: set[str] = set()
        for module_info in pkgutil.iter_modules(providers_package.__path__):
            if not module_info.ispkg:
                continue
            module_name = f"{providers_package.__name__}.{module_info.name}.provider"
            module = importlib.import_module(module_name)
            key = str(getattr(module, "PROVIDER_KEY", module_info.name) or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            default_middleware = str(getattr(module, "DEFAULT_MIDDLEWARE", "") or "").strip()
            specs.append(
                AuthProviderSpec(
                    key=key,
                    default_middleware=default_middleware,
                    compose_service_names=self._normalize_service_names(
                        getattr(module, "COMPOSE_SERVICE_NAMES", ())
                    ),
                )
            )
        return tuple(specs)

    def compose_service_names_by_provider(self) -> dict[str, tuple[str, ...]]:
        return {
            spec.key: tuple(spec.compose_service_names or ())
            for spec in self.load_builtin_auth_provider_specs()
        }

    def merge_auth_provider_defaults(
        self,
        *,
        provider_keys: tuple[str, ...],
        catalog_defaults: dict[str, str] | None = None,
        override_defaults: dict[str, str] | None = None,
    ) -> dict[str, str]:
        normalized_provider_keys: list[str] = []
        seen: set[str] = set()
        for raw in provider_keys:
            key = str(raw or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            normalized_provider_keys.append(key)

        builtin = {
            spec.key: spec.default_middleware
            for spec in self.load_builtin_auth_provider_specs()
        }
        merged: dict[str, str] = {}
        for key in normalized_provider_keys:
            merged[key] = str(builtin.get(key) or "").strip()

        for source in (catalog_defaults or {}, override_defaults or {}):
            for raw_key, raw_value in source.items():
                key = str(raw_key or "").strip().lower()
                if not key or key not in merged:
                    continue
                merged[key] = str(raw_value or "").strip()

        for key in normalized_provider_keys:
            merged.setdefault(key, "")
        return merged


_INSTANCE = AuthProviderRegistry()


# Module-level aliases. These exist so callers can keep writing
# ``from media_stack.adapters.auth.provider_registry import
# load_builtin_auth_provider_specs`` (and the Phase 16-B
# ``media_stack.core.auth.provider_registry`` shim re-export) with the
# same call signature as the legacy free functions. Direct bound-method
# capture is fine here because no test in the tree patches these names
# via ``mock.patch("…provider_registry.<name>", …)`` — if a future
# test needs that, swap an alias for a lambda that dispatches through
# ``sys.modules[__name__]`` so the patch wins.
_normalize_service_names = _INSTANCE._normalize_service_names
load_builtin_auth_provider_specs = _INSTANCE.load_builtin_auth_provider_specs
compose_service_names_by_provider = _INSTANCE.compose_service_names_by_provider
merge_auth_provider_defaults = _INSTANCE.merge_auth_provider_defaults
