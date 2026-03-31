# Technology Swaps (No `bootstrap-apps.py` Edits)

This stack supports technology replacement through config binding + reflection.

The intent is:
- keep `scripts/bootstrap-apps.py` as composition/wiring
- move behavior into technology adapters
- switch active technologies in `bootstrap/media-stack.bootstrap.json`

## How Binding Works

Two sections control swaps:

1. `technology_bindings`
- selects active backend by role
- keys:
  - `torrent_client`
  - `usenet_client`
  - `media_server`

2. `adapter_hooks`
- maps technology key -> Python adapter class path (`module:ClassName`)
- resolved dynamically at runtime
- can also register custom runner operations via `operation_handlers`:
  - `operation_name -> module.submodule:callable_name`
- can override media-server operation plans via:
  - `adapter_hooks.media_server_operation_plans.<backend>.*`
- optional for custom keys: if omitted, the runner tries convention discovery:
  - download clients: `bootstrap_services.download_client_adapters.<key_as_module>`
  - media server: `bootstrap_services.media_server_adapters.<key_as_module>`
  - servarr app impl: `bootstrap_services.servarr_technologies.<impl_as_module>`
- set a mapping value to empty (`""`) to intentionally disable an adapter key and force generic no-op fallback.

Example:

```json
{
  "technology_bindings": {
    "torrent_client": "transmission",
    "usenet_client": "sabnzbd",
    "media_server": "jellyfin"
  },
  "adapter_hooks": {
    "operation_handlers": {
      "ensure_transmission_queue": "bootstrap_services.custom_ops:ensure_transmission_queue"
    },
    "download_client_adapter_classes": {
      "transmission": "bootstrap_services.download_client_adapters.transmission:TransmissionDownloadClientAdapter"
    },
    "media_server_adapter_classes": {
      "jellyfin": "bootstrap_services.media_server_adapters.jellyfin:JellyfinMediaServerAdapter"
    }
  }
}
```

Convention-based example (no explicit `adapter_hooks` entry):
- key: `my-media`
- module: `bootstrap_services/media_server_adapters/my_media.py`
- class (preferred): `MyMediaMediaServerAdapter`
- fallback: if module exports exactly one subclass of base adapter, that class is used.

## Replace qBittorrent With Another Torrent Client

1. Add a download client config block under `download_clients.<your_key>`.
2. Add an adapter class module under:
   - `scripts/bootstrap_services/download_client_adapters/<your_client>.py`
3. Register it in `adapter_hooks.download_client_adapter_classes.<your_key>`.
4. Set `technology_bindings.torrent_client=<your_key>`.
5. Run:

```bash
bash scripts/bootstrap-all.sh
```

## Replace Media Server Backend

1. Add media-server adapter module under:
   - `scripts/bootstrap_services/media_server_adapters/<your_backend>.py`
2. Register it in:
   - `adapter_hooks.media_server_adapter_classes.<your_backend>`
3. Set:
   - `technology_bindings.media_server=<your_backend>`

The runner will route media-server phases through that adapter.

Default Jellyfin media-server steps are loaded from:
- `scripts/bootstrap_defaults/media_server_operation_plans.json`

You can override these in config:

```json
{
  "media_server": {
    "backend": "jellyfin",
    "operation_plans": {
      "jellyfin": {
        "prewarm_mode": {
          "steps": [
            {
              "operation": "ensure_jellyfin_prewarm",
              "args": ["cfg", "config_root", "wait_timeout"]
            }
          ]
        }
      }
    }
  }
}
```

## Replace/Swap a Servarr App

Servarr app behavior is adapter-based. To swap per app:

1. Update `arr_apps[]` entry (`name`, `implementation`, `url`, `root_folder`).
2. Add/update adapter class for that implementation:
   - `scripts/bootstrap_services/servarr_technologies/<impl>.py`
3. Bind adapter in:
   - `adapter_hooks.adapter_classes.<implementation>`
4. Set app capabilities in:
   - `app_capability_defaults` (or per-app `capabilities`)

## Design Rule

If a technology can be swapped by:
- adding a technology-specific adapter file and
- changing bootstrap JSON bindings (and optional custom `operation_handlers`)

then the design goal is met.

If a swap needs edits in `bootstrap-apps.py`, treat that as a refactor gap.
