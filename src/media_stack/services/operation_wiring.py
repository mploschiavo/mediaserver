"""Runner event registry wiring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .runner_operations_service import RunnerEventRegistry

OperationFn = Callable[..., Any]


@dataclass(frozen=True)
class RunnerOperationHandlers:
    """Legacy compatibility container for flat handler wiring."""

    ensure_app_auth_settings: OperationFn | None = None
    torrent_client_login: OperationFn | None = None
    read_sabnzbd_api_key: OperationFn | None = None
    ensure_sabnzbd_defaults: OperationFn | None = None
    ensure_sabnzbd_categories: OperationFn | None = None
    setup_torrent_categories: OperationFn | None = None
    run_servarr_pipeline: OperationFn | None = None
    ensure_bazarr_arr_integration: OperationFn | None = None
    configure_jellyseerr: OperationFn | None = None
    ensure_jellyfin_livetv: OperationFn | None = None
    ensure_jellyfin_libraries: OperationFn | None = None
    ensure_jellyfin_plugins: OperationFn | None = None
    ensure_jellyfin_playback_defaults: OperationFn | None = None
    ensure_jellyfin_home_rails: OperationFn | None = None
    ensure_jellyfin_auto_collections_config: OperationFn | None = None
    enforce_disk_guardrails: OperationFn | None = None
    run_media_hygiene: OperationFn | None = None
    ensure_jellyfin_prewarm: OperationFn | None = None
    ensure_maintainerr_policy: OperationFn | None = None
    ensure_maintainerr_integrations: OperationFn | None = None
    ensure_homepage_services_config: OperationFn | None = None
    ensure_prowlarr_ready: OperationFn | None = None
    ensure_prowlarr_flaresolverr_proxy: OperationFn | None = None
    ensure_prowlarr_indexer: OperationFn | None = None
    auto_add_tested_indexers: OperationFn | None = None
    trigger_prowlarr_sync: OperationFn | None = None
    sync_arr_indexers_from_prowlarr: OperationFn | None = None
    run_prowlarr_indexer_pipeline: OperationFn | None = None
    qbit_login: OperationFn | None = None
    setup_qbit_categories: OperationFn | None = None

    def to_handler_map(self) -> dict[str, OperationFn]:
        out: dict[str, OperationFn] = {}
        for key, value in self.__dict__.items():
            if callable(value):
                out[key] = value
        if callable(self.qbit_login):
            out.setdefault("torrent_client_login", self.qbit_login)
            out.setdefault("qbit_login", self.qbit_login)
        if callable(self.setup_qbit_categories):
            out.setdefault("setup_torrent_categories", self.setup_qbit_categories)
            out.setdefault("setup_qbit_categories", self.setup_qbit_categories)
        return out


def _coerce_base_handlers(
    handlers: dict[str, OperationFn] | RunnerOperationHandlers | None,
) -> dict[str, OperationFn] | None:
    if handlers is None:
        return None
    if isinstance(handlers, RunnerOperationHandlers):
        return handlers.to_handler_map()
    return dict(handlers)


def build_runner_event_registry(
    *,
    base_handlers: dict[str, OperationFn] | RunnerOperationHandlers | None = None,
    base_event_handlers: dict[str, dict[str, OperationFn]] | None = None,
    event_handler_specs: dict[str, Any] | None = None,
    operation_handler_specs: dict[str, Any] | None = None,
) -> RunnerEventRegistry:
    return RunnerEventRegistry.from_maps(
        handlers=_coerce_base_handlers(base_handlers),
        event_handlers=base_event_handlers,
        event_handler_specs=event_handler_specs,
        handler_specs=operation_handler_specs,
    )


def build_runner_operation_registry(
    handlers: dict[str, OperationFn] | RunnerOperationHandlers | None = None,
    *,
    operation_handler_specs: dict[str, Any] | None = None,
    event_handler_specs: dict[str, Any] | None = None,
) -> RunnerEventRegistry:
    """Compatibility wrapper for older callsites.

    Prefer `build_runner_event_registry` for new code.
    """

    return build_runner_event_registry(
        base_handlers=handlers,
        event_handler_specs=event_handler_specs,
        operation_handler_specs=operation_handler_specs,
    )
