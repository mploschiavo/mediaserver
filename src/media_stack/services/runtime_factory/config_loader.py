"""Bootstrap config loader with base+overlay merging."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..top_level_config_model import TopLevelBootstrapConfig
from .models import DeepMergeFn


class ControllerConfigLoader:
    def __init__(self, *, deep_merge_objects: DeepMergeFn):
        self._deep_merge_objects = deep_merge_objects

    def _find_repo_root(self, start_path: Path) -> Path:
        for candidate in [start_path, *start_path.parents]:
            if (candidate / "bootstrap").is_dir() and (candidate / "scripts").is_dir():
                return candidate
        return start_path.parent

    def _resolve_path(self, root_dir: Path, raw_path: str) -> Path:
        candidate = Path(str(raw_path or "").strip())
        if candidate.is_absolute():
            return candidate
        return (root_dir / candidate).resolve()

    def _load_yaml_defaults(self, config_dir: Path) -> dict[str, object]:
        """Load default settings from contracts/defaults/*.yaml and contracts/services/*.yaml."""
        import yaml

        defaults: dict[str, object] = {}

        # 1. Load from contracts/defaults/*.yaml (stack-level defaults)
        for defaults_dir in [config_dir / "defaults", Path("/opt/media-stack/contracts/defaults")]:
            if defaults_dir.is_dir():
                for yaml_file in sorted(defaults_dir.glob("*.yaml")):
                    try:
                        with open(yaml_file, encoding="utf-8") as f:
                            data = yaml.safe_load(f) or {}
                        if isinstance(data, dict):
                            defaults.update(data)
                    except Exception:
                        pass
                break

        # 2. Load from contracts/services/*.yaml (per-service defaults)
        # Key naming convention:
        #   - Jellyfin: defaults keys become "jellyfin_{key}" (e.g., jellyfin_libraries)
        #   - Other services: defaults become the service-name key (e.g., "bazarr": {entire dict})
        #   - Shared defaults (arr_*, download_*): kept in contracts/defaults/*.yaml
        # Services whose defaults: section maps to a top-level config key of the same name
        _SERVICE_KEY_SERVICES = {"bazarr", "jellyseerr", "homepage", "maintainerr", "flaresolverr", "jellyfin"}

        for svc_dir in [config_dir / "services", Path("/opt/media-stack/contracts/services")]:
            if svc_dir.is_dir() and any(svc_dir.glob("*.yaml")):
                for yaml_file in sorted(svc_dir.glob("*.yaml")):
                    if yaml_file.name.startswith("_"):
                        continue
                    try:
                        with open(yaml_file, encoding="utf-8") as f:
                            data = yaml.safe_load(f) or {}
                        svc_id = str((data.get("service") or {}).get("id", yaml_file.stem))
                        svc_defaults = data.get("defaults")
                        if not isinstance(svc_defaults, dict):
                            continue

                        if svc_id in _SERVICE_KEY_SERVICES:
                            # Integration pattern: full dict under service name
                            if svc_id not in defaults:
                                defaults[svc_id] = svc_defaults
                        # Other services (sonarr, radarr, etc.): defaults are
                        # handled by arr.yaml/downloads.yaml shared defaults,
                        # not per-service keys
                    except Exception:
                        pass
                break

        return defaults

    def load_config(self, config_path: str, runtime_env: str = "prod") -> dict[str, object]:
        config_file = Path(config_path).resolve()
        loaded = json.loads(config_file.read_text(encoding="utf-8"))

        # Merge YAML defaults as the base, then overlay config.json on top.
        # This means config.json values take precedence over defaults.
        yaml_defaults = self._load_yaml_defaults(config_file.parent)
        if yaml_defaults:
            merged_loaded = dict(yaml_defaults)
            merged_loaded.update(loaded)
            loaded = merged_loaded

        model = TopLevelBootstrapConfig.from_dict(loaded)

        overlay_cfg = model.config_overlays
        selected_env = (
            str(runtime_env or "").strip().lower()
            or str(overlay_cfg.env or "").strip().lower()
            or str(os.environ.get("MEDIA_STACK_ENV", "")).strip().lower()
            or "prod"
        )

        root_dir = self._find_repo_root(config_file.parent)

        if not overlay_cfg.enabled:
            return model.to_dict()

        merged: dict[str, object] = {}
        base_path = self._resolve_path(root_dir, overlay_cfg.base_path)
        if base_path.exists():
            base_cfg = json.loads(base_path.read_text(encoding="utf-8"))
            merged = self._deep_merge_objects(merged, dict(base_cfg))

        overlay_filename = overlay_cfg.env_overlays.get(selected_env, f"{selected_env}.json")
        overlay_path = self._resolve_path(
            root_dir,
            str(Path(overlay_cfg.overlay_dir) / overlay_filename),
        )
        if overlay_path.exists():
            overlay_cfg_data = json.loads(overlay_path.read_text(encoding="utf-8"))
            merged = self._deep_merge_objects(merged, dict(overlay_cfg_data))

        merged = self._deep_merge_objects(merged, model.to_dict())
        return TopLevelBootstrapConfig.from_dict(merged).to_dict()
