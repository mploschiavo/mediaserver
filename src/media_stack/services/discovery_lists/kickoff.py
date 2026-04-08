"""Discovery-list kickoff command orchestration."""

from __future__ import annotations

from typing import Any

from ..apps.servarr.config_models import ArrDiscoveryListsConfig
from ..apps.servarr.technologies.traits import get_discovery_kickoff_traits


def trigger_arr_discovery_kickoff(
    service,
    cfg: dict[str, Any],
    app_cfg: dict[str, Any],
    app_url: str,
    api_base: str,
    api_key: str,
) -> None:
    arr_discovery_cfg = ArrDiscoveryListsConfig.from_dict(cfg.get("arr_discovery_lists") or {})
    if not arr_discovery_cfg.trigger_initial_sync:
        return

    impl = str(app_cfg.get("implementation") or "").strip()
    app_name = str(app_cfg.get("name") or impl or "Arr")

    traits = get_discovery_kickoff_traits(impl)
    if traits is None or not traits.kickoff_commands:
        return

    commands: list[str] = list(traits.kickoff_commands)

    force_import_sync = service.env_truthy("ARR_FORCE_IMPORTLIST_SYNC", False)
    if force_import_sync:
        commands.insert(0, "ImportListSync")
    else:
        should_seed = True
        if traits.seed_check_endpoint:
            seed_endpoint = f"{api_base}{traits.seed_check_endpoint}"
            status, existing, _ = service.http_request(app_url, seed_endpoint, api_key=api_key)
            if status == 200 and isinstance(existing, list) and len(existing) > 0:
                should_seed = False
        if should_seed:
            commands.insert(0, "ImportListSync")
        else:
            service.log(
                f"[OK] {app_name}: skipping ImportListSync during bootstrap "
                "(library already has managed entries)"
            )

    for command_name in commands:
        service.trigger_arr_command(
            app_name,
            app_url,
            api_base,
            api_key,
            command_name,
        )
