"""Profile YAML management — the single source of truth for stack configuration."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .. import _resolve as _resolve_mod


class ProfileService:
    """Manages the bootstrap profile YAML with short-TTL cache."""

    _cache: tuple[dict[str, Any], Path | None, float] = ({}, None, 0.0)
    _CACHE_TTL = 5.0

    def load(self) -> tuple[dict[str, Any], Path | None]:
        """Load profile YAML. Returns (data, path) or ({}, None)."""
        import time as _t
        if _t.time() - self._cache[2] < self._CACHE_TTL and self._cache[1] is not None:
            return self._cache[0], self._cache[1]
        resolved = _resolve_mod.resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
        if not resolved:
            return {}, None
        path = Path(resolved)
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            ProfileService._cache = (data, path, _t.time())
            return data, path
        except Exception:
            return {}, path

    def invalidate_cache(self) -> None:
        ProfileService._cache = ({}, None, 0.0)

    def validate(self, data: dict[str, Any]) -> str | None:
        """Returns error string or None if valid."""
        if not isinstance(data, dict):
            return "Profile must be a YAML mapping"
        meta = data.get("metadata")
        if not isinstance(meta, dict) or not meta.get("name"):
            return "Profile metadata.name is required — save would corrupt the profile"
        return None

    def save(self, data: dict[str, Any], path: Path) -> dict[str, Any]:
        """Write profile YAML to disk with backup and validation."""
        err = self.validate(data)
        if err:
            return {"error": err}
        try:
            backup = path.with_suffix(".yaml.bak")
            if path.is_file():
                backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            with open(path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            self.invalidate_cache()
            return {"status": "saved", "file": str(path)}
        except Exception as exc:
            return {"error": str(exc)[:120]}

    def update_section(self, section: str, value: Any) -> dict[str, Any]:
        """Update a top-level section in the profile YAML."""
        data, path = self.load()
        if path is None:
            return {"error": "Profile file not found"}
        data[section] = value
        return self.save(data, path)

    def media_server_id(self) -> str:
        """Resolve the configured media server ID from technology bindings."""
        data, _ = self.load()
        return str(data.get("technology_bindings", {}).get("media_server", "")).strip()

    def technology_bindings(self) -> dict[str, str]:
        data, _ = self.load()
        return data.get("technology_bindings", {})

    def resolve_path(self) -> str | None:
        """Return the resolved profile file path, or None."""
        return _resolve_mod.resolve_profile_path(os.environ.get("BOOTSTRAP_PROFILE_FILE", ""))
