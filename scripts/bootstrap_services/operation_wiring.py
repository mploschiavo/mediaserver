"""Runner operation wiring extracted from bootstrap entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .enums import RunnerOperation
from .runner_operations_service import RunnerOperationRegistry

OperationFn = Callable[..., Any]


@dataclass(frozen=True)
class RunnerOperationHandlers:
    ensure_app_auth_settings: OperationFn
    qbit_login: OperationFn
    read_sabnzbd_api_key: OperationFn
    ensure_sabnzbd_defaults: OperationFn
    ensure_sabnzbd_categories: OperationFn
    setup_qbit_categories: OperationFn
    run_servarr_pipeline: OperationFn
    ensure_bazarr_arr_integration: OperationFn
    configure_jellyseerr: OperationFn
    ensure_jellyfin_livetv: OperationFn
    ensure_jellyfin_libraries: OperationFn
    ensure_jellyfin_plugins: OperationFn
    ensure_jellyfin_playback_defaults: OperationFn
    ensure_jellyfin_home_rails: OperationFn
    ensure_jellyfin_auto_collections_config: OperationFn
    enforce_disk_guardrails: OperationFn
    run_media_hygiene: OperationFn
    ensure_jellyfin_prewarm: OperationFn
    ensure_maintainerr_policy: OperationFn
    ensure_homepage_services_config: OperationFn
    ensure_prowlarr_indexer: OperationFn
    auto_add_tested_indexers: OperationFn
    trigger_prowlarr_sync: OperationFn


def build_runner_operation_registry(
    handlers: RunnerOperationHandlers,
    *,
    operation_handler_specs: dict[str, Any] | None = None,
) -> RunnerOperationRegistry:
    base_handlers = {
        RunnerOperation.ENSURE_APP_AUTH_SETTINGS.value: handlers.ensure_app_auth_settings,
        RunnerOperation.QBIT_LOGIN.value: handlers.qbit_login,
        RunnerOperation.READ_SABNZBD_API_KEY.value: handlers.read_sabnzbd_api_key,
        RunnerOperation.ENSURE_SABNZBD_DEFAULTS.value: handlers.ensure_sabnzbd_defaults,
        RunnerOperation.ENSURE_SABNZBD_CATEGORIES.value: handlers.ensure_sabnzbd_categories,
        RunnerOperation.SETUP_QBIT_CATEGORIES.value: handlers.setup_qbit_categories,
        RunnerOperation.RUN_SERVARR_PIPELINE.value: handlers.run_servarr_pipeline,
        RunnerOperation.ENSURE_BAZARR_INTEGRATION.value: handlers.ensure_bazarr_arr_integration,
        RunnerOperation.CONFIGURE_JELLYSEERR.value: handlers.configure_jellyseerr,
        RunnerOperation.ENSURE_JELLYFIN_LIVETV.value: handlers.ensure_jellyfin_livetv,
        RunnerOperation.ENSURE_JELLYFIN_LIBRARIES.value: handlers.ensure_jellyfin_libraries,
        RunnerOperation.ENSURE_JELLYFIN_PLUGINS.value: handlers.ensure_jellyfin_plugins,
        RunnerOperation.ENSURE_JELLYFIN_PLAYBACK.value: handlers.ensure_jellyfin_playback_defaults,
        RunnerOperation.ENSURE_JELLYFIN_HOME_RAILS.value: handlers.ensure_jellyfin_home_rails,
        RunnerOperation.ENSURE_JELLYFIN_AUTO_COLLECTIONS.value: (
            handlers.ensure_jellyfin_auto_collections_config
        ),
        RunnerOperation.ENFORCE_DISK_GUARDRAILS.value: handlers.enforce_disk_guardrails,
        RunnerOperation.RUN_MEDIA_HYGIENE.value: handlers.run_media_hygiene,
        RunnerOperation.ENSURE_JELLYFIN_PREWARM.value: handlers.ensure_jellyfin_prewarm,
        RunnerOperation.ENSURE_MAINTAINERR_POLICY.value: handlers.ensure_maintainerr_policy,
        RunnerOperation.ENSURE_HOMEPAGE_SERVICES.value: handlers.ensure_homepage_services_config,
        RunnerOperation.ENSURE_PROWLARR_INDEXER.value: handlers.ensure_prowlarr_indexer,
        RunnerOperation.AUTO_ADD_TESTED_INDEXERS.value: handlers.auto_add_tested_indexers,
        RunnerOperation.TRIGGER_PROWLARR_SYNC.value: handlers.trigger_prowlarr_sync,
    }
    return RunnerOperationRegistry.from_maps(
        handlers=base_handlers,
        handler_specs=operation_handler_specs,
    )
