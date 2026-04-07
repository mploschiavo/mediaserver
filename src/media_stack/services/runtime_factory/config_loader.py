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

    def load_config(self, config_path: str, runtime_env: str = "prod") -> dict[str, object]:
        config_file = Path(config_path).resolve()
        loaded = json.loads(config_file.read_text(encoding="utf-8"))
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
