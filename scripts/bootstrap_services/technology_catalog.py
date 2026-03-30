"""Technology catalog helpers for swappable component configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
            for value in (raw, raw.lower()):
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


def default_servarr_catalog() -> ServarrTechnologyCatalog:
    return ServarrTechnologyCatalog(
        definitions=(
            TechnologyDefinition(key="sonarr", aliases=("Sonarr",)),
            TechnologyDefinition(key="radarr", aliases=("Radarr",)),
            TechnologyDefinition(key="lidarr", aliases=("Lidarr",)),
            TechnologyDefinition(key="readarr", aliases=("Readarr",)),
        )
    )
