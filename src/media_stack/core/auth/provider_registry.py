"""Auth provider discovery and default middleware resolution."""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass

from media_stack.core.auth import providers as providers_package


@dataclass(frozen=True)
class AuthProviderSpec:
    key: str
    default_middleware: str = ""
    compose_service_names: tuple[str, ...] = ()


def _normalize_service_names(raw: object) -> tuple[str, ...]:
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


def load_builtin_auth_provider_specs() -> tuple[AuthProviderSpec, ...]:
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
                compose_service_names=_normalize_service_names(
                    getattr(module, "COMPOSE_SERVICE_NAMES", ())
                ),
            )
        )
    return tuple(specs)


def compose_service_names_by_provider() -> dict[str, tuple[str, ...]]:
    return {
        spec.key: tuple(spec.compose_service_names or ())
        for spec in load_builtin_auth_provider_specs()
    }


def merge_auth_provider_defaults(
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

    builtin = {spec.key: spec.default_middleware for spec in load_builtin_auth_provider_specs()}
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
