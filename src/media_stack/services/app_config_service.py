"""Per-app configuration service.

Each app has its own config file at {CONFIG_ROOT}/{service_id}/controller.yaml.
This holds user selections (which countries for Live TV, which libraries, etc.)
separate from the profile (which is just metadata + bindings).

The service contract defines defaults. The controller.yaml holds overrides.
Merged result = defaults + overrides.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def _config_root() -> str:
    return os.environ.get("CONFIG_ROOT", "/srv-config")


def _app_config_path(service_id: str) -> Path:
    return Path(_config_root()) / service_id / "controller.yaml"


def load_app_config(service_id: str) -> dict[str, Any]:
    """Load per-app controller config. Returns empty dict if not found."""
    path = _app_config_path(service_id)
    if not path.is_file():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def save_app_config(service_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Save per-app controller config."""
    path = _app_config_path(service_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True))
        return {"status": "saved", "file": str(path)}
    except Exception as exc:
        return {"error": str(exc)[:120]}


def update_app_config_section(service_id: str, section: str, value: Any) -> dict[str, Any]:
    """Update a single section in the app's controller config."""
    data = load_app_config(service_id)
    data[section] = value
    return save_app_config(service_id, data)


def get_merged_app_config(service_id: str, contract_defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge contract defaults with user overrides. User wins."""
    defaults = dict(contract_defaults or {})
    overrides = load_app_config(service_id)
    defaults.update(overrides)
    return defaults
