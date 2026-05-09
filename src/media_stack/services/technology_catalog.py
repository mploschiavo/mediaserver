"""Technology catalog helpers for swappable component configuration."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from .plugin_manifest_loader import PluginManifest, load_plugin_manifests


@dataclass(frozen=True)
class TechnologyDefinition:
    key: str
    aliases: tuple[str, ...]

    def all_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for candidate in (self.key, *self.aliases):
            raw = str(candidate).strip()
            if not raw:
                continue
            for value in (raw, raw.lower(), raw.capitalize()):
                if value not in names:
                    names.append(value)
        return tuple(names)


@dataclass(frozen=True)
class ServarrTechnologyCatalog:
    definitions: tuple[TechnologyDefinition, ...]

    def canonicalize(self, implementation: str) -> str:
        token = str(implementation or "").strip().lower()
        if not token:
            return ""
        for definition in self.definitions:
            if token in {name.lower() for name in definition.all_names()}:
                return definition.key
        return token

    def expand_capability_defaults(
        self,
        capability_defaults: dict[str, Any] | None,
    ) -> dict[str, dict[str, Any]]:
        src = dict(capability_defaults or {})
        by_key: dict[str, dict[str, Any]] = {}

        for impl, values in src.items():
            if not isinstance(values, dict):
                continue
            canonical = self.canonicalize(str(impl))
            if not canonical:
                continue
            merged = dict(by_key.get(canonical) or {})
            merged.update(dict(values))
            by_key[canonical] = merged

        expanded: dict[str, dict[str, Any]] = {}
        for definition in self.definitions:
            canonical = definition.key
            values = dict(by_key.get(canonical) or {})
            if not values:
                continue
            for name in definition.all_names():
                expanded[name] = dict(values)

        for key, values in by_key.items():
            if key not in expanded:
                expanded[key] = dict(values)

        return expanded


class ServarrTechnologyCatalogBuilder:
    """Builds :class:`ServarrTechnologyCatalog` instances from plugin manifests."""

    def default_servarr_catalog(self) -> ServarrTechnologyCatalog:
        manifests = load_plugin_manifests()
        return sys.modules[__name__].build_servarr_catalog_from_manifests(manifests)

    def build_servarr_catalog_from_manifests(
        self,
        manifests: list[PluginManifest],
    ) -> ServarrTechnologyCatalog:
        definitions: list[TechnologyDefinition] = []
        for manifest in manifests:
            role_map = manifest.adapter_classes
            if not isinstance(role_map, dict):
                continue
            if str(role_map.get("servarr") or "").strip() == "":
                continue
            key = str(manifest.technology or "").strip().lower()
            if not key:
                continue
            definitions.append(
                TechnologyDefinition(
                    key=key,
                    aliases=tuple(alias for alias in manifest.aliases if alias and alias != key),
                )
            )

        if not definitions:
            raise ValueError(
                "No Servarr technology manifests discovered. "
                "Expected at least one plugin manifest with adapter_classes.servarr."
            )

        return ServarrTechnologyCatalog(definitions=tuple(definitions))


_BUILDER_INSTANCE = ServarrTechnologyCatalogBuilder()

default_servarr_catalog = _BUILDER_INSTANCE.default_servarr_catalog
build_servarr_catalog_from_manifests = _BUILDER_INSTANCE.build_servarr_catalog_from_manifests
