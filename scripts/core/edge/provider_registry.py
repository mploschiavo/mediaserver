"""Edge router provider discovery and defaults registry."""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from functools import lru_cache

from core.edge import providers as providers_package


@dataclass(frozen=True)
class EdgeRouterProviderSpec:
    key: str
    router_service_names: tuple[str, ...] = ()
    compose_label_spec: dict[str, str] | None = None


def _normalize_router_service_names(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        token = str(item or "").strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return tuple(out)


def _normalize_compose_label_spec(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for raw_key, raw_value in raw.items():
        key = str(raw_key or "").strip()
        value = str(raw_value or "").strip()
        if key and value:
            out[key] = value
    return out


@lru_cache(maxsize=1)
def load_builtin_edge_router_provider_specs() -> tuple[EdgeRouterProviderSpec, ...]:
    specs: list[EdgeRouterProviderSpec] = []
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
        specs.append(
            EdgeRouterProviderSpec(
                key=key,
                router_service_names=_normalize_router_service_names(
                    getattr(module, "ROUTER_SERVICE_NAMES", ())
                ),
                compose_label_spec=_normalize_compose_label_spec(
                    getattr(module, "COMPOSE_LABEL_SPEC", {})
                ),
            )
        )
    return tuple(specs)


def compose_label_specs_by_provider() -> dict[str, dict[str, str]]:
    return {
        spec.key: dict(spec.compose_label_spec or {})
        for spec in load_builtin_edge_router_provider_specs()
    }


def router_service_names_by_provider() -> dict[str, tuple[str, ...]]:
    return {
        spec.key: tuple(spec.router_service_names or ())
        for spec in load_builtin_edge_router_provider_specs()
    }
