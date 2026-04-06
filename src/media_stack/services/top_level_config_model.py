"""Strict top-level bootstrap config model with schema-driven key validation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .apps.download_clients.config_models import DownloadClientsConfig, TechnologyBindingsConfig
from .apps.servarr.config_models import ServarrAppConfig

SUPPORTED_BOOTSTRAP_CONFIG_VERSION = 2
_TOP_LEVEL_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "contracts" / "top_level_config_schema.json"
)


def _expect_int(data: dict[str, Any], key: str) -> int:
    if key not in data:
        raise ValueError(f"$.{key} is required")
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"$.{key} must be an integer")
    return int(value)


@dataclass(frozen=True)
class ConfigOverlaySettings:
    enabled: bool = False
    env: str = "prod"
    base_path: str = "config/runtime/base.json"
    overlay_dir: str = "config/runtime/overlays"
    env_overlays: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "ConfigOverlaySettings":
        src = dict(value or {})
        env_overlays = src.get("env_overlays") or {}
        if env_overlays is None:
            env_overlays = {}
        if not isinstance(env_overlays, dict):
            raise ValueError("$.config_overlays.env_overlays must be an object")
        return cls(
            enabled=bool(src.get("enabled", False)),
            env=str(src.get("env", "prod")).strip() or "prod",
            base_path=str(src.get("base_path", "config/runtime/base.json")).strip()
            or "config/runtime/base.json",
            overlay_dir=str(src.get("overlay_dir", "config/runtime/overlays")).strip()
            or "config/runtime/overlays",
            env_overlays={
                str(key).strip().lower(): str(item).strip()
                for key, item in env_overlays.items()
                if str(key).strip() and str(item).strip()
            },
            raw=src,
        )


def _load_top_level_schema() -> tuple[dict[str, str], set[str]]:
    payload = json.loads(_TOP_LEVEL_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Top-level config schema must be an object")

    allowed_raw = payload.get("allowed_keys") or {}
    if not isinstance(allowed_raw, dict):
        raise ValueError("top_level_config_schema.allowed_keys must be an object")

    allowed: dict[str, str] = {}
    for key, value in allowed_raw.items():
        token = str(key or "").strip()
        expected = str(value or "").strip().lower()
        if token and expected:
            allowed[token] = expected

    required_raw = payload.get("required_keys") or []
    if not isinstance(required_raw, list):
        raise ValueError("top_level_config_schema.required_keys must be an array")
    required = {str(item or "").strip() for item in required_raw if str(item or "").strip()}
    return allowed, required


def _validate_expected_type(key: str, value: Any, expected: str) -> None:
    if expected == "object":
        if not isinstance(value, dict):
            raise ValueError(f"$.{key} must be an object")
        return
    if expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"$.{key} must be an array")
        return
    if expected == "string":
        if not isinstance(value, str):
            raise ValueError(f"$.{key} must be a string")
        return
    if expected == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"$.{key} must be a boolean")
        return
    if expected == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"$.{key} must be an integer")
        return
    if expected == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"$.{key} must be a number")


@dataclass(frozen=True)
class TopLevelBootstrapConfig:
    config_version: int
    config_overlays: ConfigOverlaySettings
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, cfg: dict[str, Any]) -> "TopLevelBootstrapConfig":
        if not isinstance(cfg, dict):
            raise ValueError("Bootstrap config root must be an object")

        src = dict(cfg)
        config_version = _expect_int(src, "config_version")
        if config_version != SUPPORTED_BOOTSTRAP_CONFIG_VERSION:
            raise ValueError(
                "$.config_version "
                f"{config_version} is not supported. "
                f"Expected {SUPPORTED_BOOTSTRAP_CONFIG_VERSION}. "
                "Migrate your bootstrap config before running."
            )

        allowed_keys, required_keys = _load_top_level_schema()
        for required in required_keys:
            if required not in src:
                raise ValueError(f"$.{required} is required")

        unknown = sorted(key for key in src.keys() if key not in allowed_keys)
        if unknown:
            unknown_keys = ", ".join(unknown)
            raise ValueError(
                "Unsupported top-level bootstrap config keys: "
                f"{unknown_keys}. "
                "Migrate config_version to the current schema before running."
            )

        for key, value in src.items():
            expected = allowed_keys.get(key)
            if not expected:
                continue
            _validate_expected_type(key, value, expected)

        arr_apps_raw = src.get("arr_apps") or []
        if not isinstance(arr_apps_raw, list):
            raise ValueError("$.arr_apps must be an array")
        if any(not isinstance(item, dict) for item in arr_apps_raw):
            raise ValueError("$.arr_apps must contain only objects")
        ServarrAppConfig.from_list(arr_apps_raw)

        download_clients_raw = src.get("download_clients") or {}
        if not isinstance(download_clients_raw, dict):
            raise ValueError("$.download_clients must be an object")
        DownloadClientsConfig.from_dict(download_clients_raw)

        technology_bindings_raw = src.get("technology_bindings") or {}
        if not isinstance(technology_bindings_raw, dict):
            raise ValueError("$.technology_bindings must be an object")
        request_manager_value = technology_bindings_raw.get("request_manager")
        if request_manager_value is not None and not isinstance(request_manager_value, str):
            raise ValueError("$.technology_bindings.request_manager must be a string")
        technology_bindings = TechnologyBindingsConfig.from_dict(technology_bindings_raw)
        if not technology_bindings.media_server:
            raise ValueError("$.technology_bindings.media_server must be a non-empty string")

        prowlarr_indexers_raw = src.get("prowlarr_indexers") or []
        if not isinstance(prowlarr_indexers_raw, list):
            raise ValueError("$.prowlarr_indexers must be an array")
        if any(not isinstance(item, dict) for item in prowlarr_indexers_raw):
            raise ValueError("$.prowlarr_indexers must contain only objects")

        normalized = dict(src)
        exclude_tokens_raw = src.get("prowlarr_auto_indexer_exclude_name_tokens") or []
        if not isinstance(exclude_tokens_raw, list):
            raise ValueError("$.prowlarr_auto_indexer_exclude_name_tokens must be an array")
        normalized["prowlarr_auto_indexer_exclude_name_tokens"] = [
            str(item).strip() for item in exclude_tokens_raw if str(item).strip()
        ]

        return cls(
            config_version=config_version,
            config_overlays=ConfigOverlaySettings.from_dict(src.get("config_overlays") or {}),
            raw=normalized,
        )

    def __getattr__(self, name: str) -> Any:
        if name in self.raw:
            return self.raw[name]
        raise AttributeError(name)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.raw)
