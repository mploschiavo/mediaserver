"""Load role definitions from a YAML catalog file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from media_stack.core.auth.users.models import Role


class RoleCatalog:

    def __init__(self, catalog_path: Path) -> None:
        self._catalog_path = Path(catalog_path)
        self._roles: dict[str, Role] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        data: dict[str, Any] = {}
        if self._catalog_path.is_file():
            data = yaml.safe_load(self._catalog_path.read_text(encoding="utf-8")) or {}
        roles_cfg = data.get("roles", {}) or {}
        self._roles = {
            slug: Role.from_dict(slug, cfg or {})
            for slug, cfg in roles_cfg.items()
            if isinstance(slug, str) and slug
        }
        self._loaded = True

    def reload(self) -> None:
        self._loaded = False
        self._load()

    def get(self, slug: str) -> Role | None:
        self._load()
        return self._roles.get(slug)

    def require(self, slug: str) -> Role:
        role = self.get(slug)
        if not role:
            raise KeyError(f"Unknown role: {slug!r}")
        return role

    def list_all(self) -> list[Role]:
        self._load()
        return list(self._roles.values())

    def slugs(self) -> list[str]:
        self._load()
        return list(self._roles.keys())
