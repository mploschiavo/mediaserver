# Technology Swaps (Manifest-First, Config-Driven)

This stack supports technology replacement without editing the bootstrap entrypoint.

Design model reference:
- [Technology adapter model diagram](diagrams/technology-adapter-model.svg)

![Technology adapter model](diagrams/technology-adapter-model.png)

## Source of Truth for Swaps

Swaps are controlled by:

1. `technology_bindings` in `bootstrap/media-stack.bootstrap.json`
2. Per-technology plugin manifests in `scripts/bootstrap_defaults/plugins/<technology>/manifest.json`
3. Per-technology adapter/service modules under `scripts/bootstrap_services/...`

## Manifest Contract Surface

Per-technology manifests support these keys:
- `technology`
- `aliases`
- `adapter_classes`
- `before_common_steps`
- `app_service_classes`
- `service_technology_map`
- `operation_handlers`
- `capability_defaults`

Role-specific class contracts:
- `adapter_classes.servarr`
- `adapter_classes.download_client`
- `adapter_classes.media_server`

Shared runtime operation contracts are generic and technology-neutral:
- `torrent_client_login`
- `setup_torrent_categories`
- `read_sabnzbd_api_key`
- `ensure_sabnzbd_defaults`
- `ensure_sabnzbd_categories`

`adapter_hooks` is no longer used for adapter/service registration overrides.  
Registration is manifest-only. Runtime-only hooks still supported:
- `adapter_hooks.operation_handlers`
- `adapter_hooks.runner_operation_plans`
- `adapter_hooks.media_server_operation_plans`

Disallowed runtime registration overrides:
- `adapter_hooks.adapter_classes`
- `adapter_hooks.download_client_adapter_classes`
- `adapter_hooks.media_server_adapter_classes`
- `adapter_hooks.app_service_classes`
- `adapter_hooks.service_technology_map`

## Active Bindings

`technology_bindings` currently supports:
- `torrent_client`
- `usenet_client`
- `media_server`
- `request_manager`

Example:

```json
{
  "technology_bindings": {
    "torrent_client": "qbittorrent",
    "usenet_client": "nzbget",
    "media_server": "plex",
    "request_manager": "openseerr"
  }
}
```

## Built-In Swap Families

Media server adapters:
- `jellyfin`
- `emby`
- `plex`
- `mythtv`

Request manager adapters:
- `jellyseerr`
- `openseerr` (alias: `openseer`)

Download client adapters:
- Torrent: `qbittorrent`, `transmission`
- Usenet: `sabnzbd`, `nzbget`, `jdownloader`, `grabit`

Servarr app adapters:
- `sonarr`, `radarr`, `lidarr`, `readarr` (plus capability-driven behavior)

## App Boundary Layout (No Shared App Logic)

App-specific logic should live under one app package.  
For Jellyfin, the implementation boundary is now:

- `scripts/bootstrap_services/apps/jellyfin/runtime_ops.py`
- `scripts/bootstrap_services/apps/jellyfin/livetv_service.py`
- `scripts/bootstrap_services/apps/jellyfin/livetv_source_service.py`
- `scripts/bootstrap_services/apps/jellyfin/livetv_state_service.py`
- `scripts/bootstrap_services/apps/jellyfin/libraries_service.py`
- `scripts/bootstrap_services/apps/jellyfin/home_rails_service.py`
- `scripts/bootstrap_services/apps/jellyfin/playback_service.py`
- `scripts/bootstrap_services/apps/jellyfin/plugins_service.py`
- `scripts/bootstrap_services/apps/jellyfin/prewarm_service.py`
- `scripts/bootstrap_services/apps/jellyfin/config_models.py`

Root-level `scripts/bootstrap_services/jellyfin_*` modules are retired.
Bootstrap handler wiring now calls Jellyfin operations from this app-local boundary directly.

## Swap Process

1. Add/update runtime config blocks (`download_clients`, app config, etc.).
2. Add or update one technology manifest.
3. Set the active key in `technology_bindings`.
4. Validate config:

```bash
bash scripts/validate-bootstrap-config.sh --config bootstrap/media-stack.bootstrap.json --schema bootstrap/media-stack.bootstrap.schema.json
```

5. Verify manifest contracts and swap matrix:

```bash
python3 -m unittest tests.unit.test_technology_pluggability_contracts
python3 -m unittest tests.unit.test_technology_swap_matrix_e2e
```

6. Reconcile:

```bash
bash scripts/bootstrap-all.sh
```

### Example: Jellyfin -> Plex

1. Set binding:

```json
{
  "technology_bindings": {
    "media_server": "plex"
  }
}
```

2. Keep `scripts/bootstrap_defaults/plugins/plex/manifest.json` present.
3. Add/update `adapter_hooks.media_server_operation_plans.plex` only if you want Plex-specific runtime operations.
4. Reconcile; Jellyfin-specific operations are not executed when `media_server=plex`.

## Prove Isolation and Removability

To test that one technology is truly self-contained:

1. Rebind the role to an alternative technology.
2. Reconcile once.
3. Temporarily remove the original technology manifest (or remove only its role keys).
4. Run contract and bootstrap tests:

```bash
python3 -m unittest tests.unit.test_technology_pluggability_contracts
python3 -m unittest tests.unit.test_bootstrap_services_runtime_factory
python3 -m unittest tests.unit.test_bootstrap_services_bootstrap_runner
```

Expected result:
- Unrelated technologies continue to load and run.
- Missing removed technology fails only when actively invoked.

## Add a New Technology

1. Create manifest:
   - `scripts/bootstrap_defaults/plugins/<your-tech>/manifest.json`
2. Add adapter/service module:
   - download client: `scripts/bootstrap_services/download_client_adapters/<your-tech>.py`
   - media server: `scripts/bootstrap_services/media_server_adapters/<your-tech>.py`
   - request manager app: `scripts/bootstrap_services/apps/<your-tech>/service.py`
3. Ensure manifest points to your class path (`module:ClassName`).
4. Add config block and switch binding key.
5. Validate + run bootstrap.

If you need custom behavior hooks, add only operation hooks/plans in `adapter_hooks` (not class registration).

## Compatibility Notes

- Non-Jellyfin media servers currently run through plan-driven adapters.  
  If a backend has no operation plan, media-server phases are skipped with warnings instead of hard-failing.
- Request manager defaults to `jellyseerr` when `technology_bindings.request_manager` is omitted.
- Legacy operation names `qbit_login` and `setup_qbit_categories` remain supported for compatibility, but new manifests should use generic names.

---

**Project Steward**  
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
