"""Plan summary builder for bootstrap runtime."""

from __future__ import annotations

from ..runtime_models import ControllerRuntime
from .models import ControllerPlanSummary


def build_plan_summary(runtime: ControllerRuntime) -> ControllerPlanSummary:
    """Build a plan summary from runtime state using dict-based storage.

    Features and counts are assembled as dicts so that adding new services
    only requires appending to these dicts rather than editing the
    ControllerPlanSummary class and this builder in lockstep.
    """
    counts: dict[str, int] = {
        "arr_apps": len(runtime.arr_apps),
        "indexer_entries": len(runtime.indexer_entries),
        "sab_remote_path_mappings": len(runtime.sab_remote_path_mappings),
        "media_server_livetv_tuners": len(
            (runtime.cfg.get("jellyfin_livetv") or {}).get("tuners") or []
        ),
        "media_server_livetv_guides": len(
            (runtime.cfg.get("jellyfin_livetv") or {}).get("guides") or []
        ),
    }

    features: dict[str, bool] = {
        "auto_indexers": runtime.auto_indexers,
        "configure_arr_clients": (
            runtime.configure_torrent_arr_clients or runtime.configure_sab_arr_clients
        ),
        "configure_torrent_arr_clients": runtime.configure_torrent_arr_clients,
        "configure_sab_arr_clients": runtime.configure_sab_arr_clients,
        "configure_arr_media_management": runtime.configure_arr_media_management,
        "configure_arr_quality_upgrade": runtime.configure_arr_quality_upgrade,
        "configure_arr_download_handling": runtime.configure_arr_download_handling,
        "configure_arr_discovery_lists": runtime.configure_arr_discovery_lists,
        "set_torrent_categories": runtime.set_torrent_categories,
        "torrent_client_login_required": runtime.torrent_client_login_required,
        "refresh_health_after_setup": runtime.refresh_health_after_setup,
        "app_auth_enabled": bool((runtime.app_auth_cfg or {}).get("enabled", False)),
        "configure_dashboard": runtime.configure_dashboard,
        "configure_subtitles": runtime.configure_subtitles,
        "configure_request_manager": runtime.configure_request_manager,
        "configure_media_server_libraries": runtime.configure_media_server_libraries,
        "configure_media_server_livetv": runtime.configure_media_server_livetv,
        "configure_media_server_plugins": runtime.configure_media_server_plugins,
        "configure_media_server_playback": runtime.configure_media_server_playback,
        "configure_media_server_home_rails": runtime.configure_media_server_home_rails,
        "configure_auto_collections": runtime.configure_auto_collections,
        "configure_disk_guardrails": runtime.configure_disk_guardrails,
        "configure_media_server_prewarm": runtime.configure_media_server_prewarm,
        "configure_media_hygiene": runtime.configure_media_hygiene,
        "configure_media_policy": runtime.configure_media_policy,
        "configure_media_policy_integrations": runtime.configure_media_policy_integrations,
        "fully_preconfigured": runtime.fully_preconfigured,
        "trigger_sync": runtime.trigger_sync,
    }

    return ControllerPlanSummary(
        mode=runtime.mode,
        features=features,
        counts=counts,
    )
