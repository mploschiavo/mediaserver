"""Plan summary builder for bootstrap runtime."""

from __future__ import annotations

from ..runtime_models import ControllerRuntime
from .models import ControllerPlanSummary


def build_plan_summary(runtime: ControllerRuntime) -> ControllerPlanSummary:
    return ControllerPlanSummary(
        mode=runtime.mode,
        arr_apps=len(runtime.arr_apps),
        prowlarr_indexers=len(runtime.prowlarr_indexers),
        auto_indexers=runtime.auto_indexers,
        configure_arr_clients=(
            runtime.configure_torrent_arr_clients or runtime.configure_sab_arr_clients
        ),
        configure_torrent_arr_clients=runtime.configure_torrent_arr_clients,
        configure_sab_arr_clients=runtime.configure_sab_arr_clients,
        sab_remote_path_mappings=len(runtime.sab_remote_path_mappings),
        configure_arr_media_management=runtime.configure_arr_media_management,
        configure_arr_quality_upgrade=runtime.configure_arr_quality_upgrade,
        configure_arr_download_handling=runtime.configure_arr_download_handling,
        configure_arr_discovery_lists=runtime.configure_arr_discovery_lists,
        set_torrent_categories=runtime.set_torrent_categories,
        torrent_client_login_required=runtime.torrent_client_login_required,
        refresh_health_after_setup=runtime.refresh_health_after_setup,
        app_auth_enabled=bool((runtime.app_auth_cfg or {}).get("enabled", False)),
        configure_homepage=runtime.configure_homepage_services,
        configure_bazarr=runtime.configure_bazarr_integration,
        configure_jellyseerr=runtime.configure_jellyseerr_services,
        configure_jellyfin_libraries=runtime.configure_jellyfin_libraries,
        configure_jellyfin_livetv=runtime.configure_jellyfin_livetv,
        configure_jellyfin_plugins=runtime.configure_jellyfin_plugins,
        configure_jellyfin_playback=runtime.configure_jellyfin_playback,
        configure_jellyfin_home_rails=runtime.configure_jellyfin_home_rails,
        configure_auto_collections=runtime.configure_auto_collections,
        configure_disk_guardrails=runtime.configure_disk_guardrails,
        configure_jellyfin_prewarm=runtime.configure_jellyfin_prewarm,
        configure_media_hygiene=runtime.configure_media_hygiene,
        configure_maintainerr_policy=runtime.configure_maintainerr_policy,
        configure_maintainerr_integrations=runtime.configure_maintainerr_integrations,
        jellyfin_livetv_tuners=len((runtime.cfg.get("jellyfin_livetv") or {}).get("tuners") or []),
        jellyfin_livetv_guides=len((runtime.cfg.get("jellyfin_livetv") or {}).get("guides") or []),
        fully_preconfigured=runtime.fully_preconfigured,
        trigger_sync=runtime.trigger_sync,
    )
