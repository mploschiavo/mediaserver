"""Shared runtime models for bootstrap orchestration."""

from __future__ import annotations

from typing import Any

from .apps.download_clients.runtime_compat import (
    LEGACY_KWARG_MAP as _DL_CLIENT_COMPAT,
    DownloadClientRuntimeCompatMixin,
)
import importlib as _importlib

def _load_indexer_compat():
    from media_stack.api.services.registry import SERVICES
    for svc in SERVICES:
        if not svc.indexer_path:
            continue
        try:
            return _importlib.import_module(f"media_stack.services.apps.{svc.id}.runtime_compat")
        except (ImportError, ModuleNotFoundError):
            continue
    # Fallback: empty compat
    class _EmptyCompat:
        LEGACY_KWARG_MAP = {}
    return _EmptyCompat()

_indexer_compat = _load_indexer_compat()
_PROWLARR_COMPAT = _indexer_compat.LEGACY_KWARG_MAP
ProwlarrRuntimeCompatMixin = _indexer_compat.ProwlarrRuntimeCompatMixin
from .apps.servarr.config_models import (
    ArrDownloadHandlingPolicy,
    ArrMediaManagementPolicy,
    ArrQualityUpgradePolicy,
    ServarrAppConfig,
)
from .enums import BootstrapMode


class ControllerRuntime(
    ProwlarrRuntimeCompatMixin,
    DownloadClientRuntimeCompatMixin,
):
    """Runtime state bag for bootstrap orchestration.

    Core runtime identity and wiring fields are explicit.
    Service-specific URLs, keys, configs, credentials, and data are stored
    in generic dicts so that adding new services does not require editing
    this model's constructor signature.

    App/feature-specific toggles and optional runtime values are accepted
    dynamically so feature growth does not require editing this model.
    """

    # Maps old named-param keywords to the (dict_attr, key) pairs they
    # populate, used by _accept_legacy_kwargs for backward compatibility.
    # Each app-layer compat module contributes its own entries.
    _LEGACY_KWARG_MAP: dict[str, tuple[str, str, str]] = {
        **_PROWLARR_COMPAT,
        **_DL_CLIENT_COMPAT,
    }

    def __init__(
        self,
        *,
        mode: BootstrapMode,
        cfg: dict[str, Any],
        config_root: str,
        wait_timeout: int,
        arr_apps_raw: list[dict[str, Any]],
        arr_apps: list[ServarrAppConfig],
        app_keys: dict[str, str],
        torrent_client_key: str,
        usenet_client_key: str,
        arr_media_management_cfg: ArrMediaManagementPolicy,
        arr_download_handling_cfg: ArrDownloadHandlingPolicy,
        arr_quality_upgrade_cfg: ArrQualityUpgradePolicy,
        app_auth_cfg: dict[str, Any],
        adapter_hooks_cfg: dict[str, Any],
        auto_indexers: bool,
        trigger_sync: bool,
        fully_preconfigured: bool,
        service_urls: dict[str, str] | None = None,
        service_keys: dict[str, str] | None = None,
        service_configs: dict[str, dict] | None = None,
        service_credentials: dict[str, dict] | None = None,
        service_data: dict[str, Any] | None = None,
        media_server_backend: str = "",
        request_manager_backend: str = "",
        feature_flags: dict[str, bool] | None = None,
        runtime_values: dict[str, Any] | None = None,
        **dynamic_values: Any,
    ) -> None:
        self.mode = mode
        self.cfg = cfg
        self.config_root = config_root
        self.wait_timeout = wait_timeout
        self.arr_apps_raw = arr_apps_raw
        self.arr_apps = arr_apps
        self.app_keys = app_keys
        self.torrent_client_key = torrent_client_key
        self.usenet_client_key = usenet_client_key
        self.arr_media_management_cfg = arr_media_management_cfg
        self.arr_download_handling_cfg = arr_download_handling_cfg
        self.arr_quality_upgrade_cfg = arr_quality_upgrade_cfg
        self.app_auth_cfg = app_auth_cfg
        self.adapter_hooks_cfg = adapter_hooks_cfg
        self.auto_indexers = bool(auto_indexers)
        self.trigger_sync = bool(trigger_sync)
        self.fully_preconfigured = bool(fully_preconfigured)
        self.media_server_backend = str(media_server_backend or "").strip()
        self.request_manager_backend = str(request_manager_backend or "").strip()

        # Generic service dicts
        self.service_urls: dict[str, str] = dict(service_urls or {})
        self.service_keys: dict[str, str] = dict(service_keys or {})
        self.service_configs: dict[str, dict] = {
            k: dict(v) for k, v in (service_configs or {}).items()
        }
        self.service_credentials: dict[str, dict] = {
            k: dict(v) for k, v in (service_credentials or {}).items()
        }
        self.service_data: dict[str, Any] = dict(service_data or {})

        self.feature_flags: dict[str, bool] = {
            str(key): bool(value) for key, value in (feature_flags or {}).items()
        }
        self.runtime_values: dict[str, Any] = dict(runtime_values or {})

        # Accept legacy named kwargs for backward compatibility and route
        # them into the generic dicts before processing dynamic_values.
        self._accept_legacy_kwargs(dynamic_values)

        for key, value in dynamic_values.items():
            token = str(key or "").strip()
            if not token:
                continue
            if isinstance(value, bool):
                self.feature_flags[token] = bool(value)
            else:
                self.runtime_values[token] = value

    def _accept_legacy_kwargs(self, dynamic_values: dict[str, Any]) -> None:
        """Move legacy named-param kwargs from dynamic_values into generic dicts."""
        for kwarg, (dest_attr, dict_key, _) in self._LEGACY_KWARG_MAP.items():
            if kwarg not in dynamic_values:
                continue
            value = dynamic_values.pop(kwarg)
            dest = getattr(self, dest_attr)
            if "." in dict_key:
                # Nested credential: "qbittorrent.user" -> service_credentials["qbittorrent"]["user"]
                outer, inner = dict_key.split(".", 1)
                dest.setdefault(outer, {})[inner] = value
            else:
                dest[dict_key] = value

    def __getattr__(self, name: str) -> Any:
        # Avoid infinite recursion during init before dicts are set
        for attr in ("feature_flags", "runtime_values"):
            if attr not in self.__dict__:
                raise AttributeError(name)

        if name in self.feature_flags:
            return self.feature_flags[name]
        if name in self.runtime_values:
            return self.runtime_values[name]
        raise AttributeError(name)

    # ------------------------------------------------------------------
    # Backward-compatible property accessors are provided by mixins:
    #   - ProwlarrRuntimeCompatMixin (prowlarr_url, prowlarr_key, prowlarr_indexers)
    #   - DownloadClientRuntimeCompatMixin (qbit_cfg, sab_cfg, qb_user, etc.)
    # Generic torrent-client aliases (torrent_client_cfg, etc.) are also
    # in DownloadClientRuntimeCompatMixin.
    # ------------------------------------------------------------------

    @property
    def configure_torrent_arr_clients(self) -> bool:
        return bool(self.feature_flags.get("configure_qbit_arr_clients", False))

    @property
    def set_torrent_categories(self) -> bool:
        return bool(self.feature_flags.get("set_qbit_categories", False))

    @property
    def torrent_client_login_required(self) -> bool:
        return bool(self.feature_flags.get("qbit_login_required", False))
