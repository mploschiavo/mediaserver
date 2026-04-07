# Service Registry

The service registry (`src/media_stack/api/services/registry.py`) is the **single source of truth** for all managed services. Every service operation — health probes, auth validation, key discovery, key rotation, password reset — derives from this registry.

## Adding a Service

Add a `ServiceDef` to the `SERVICES` list in `registry.py`:

```python
ServiceDef(
    id="myapp",               # Unique identifier (used in URLs, config keys, etc.)
    name="MyApp",             # Display name
    desc="My cool app",       # Short description
    category="automation",    # automation | media | downloads | management
    host="myapp",             # Container/pod hostname
    port=8080,                # Primary port
    health_path="/ping",      # HTTP health check path
    auth_path="/api/v1/status",    # Authenticated API probe (empty = skip)
    auth_mode="X-Api-Key",         # Auth header or "query:paramname"
    api_key_env="MYAPP_API_KEY",   # Env var for the API key
    api_key_config="myapp/config.xml",  # Config file path (relative to config root)
    api_key_format="xml",          # xml | ini | yaml | json | sqlite
    password_api_path="/api/v1/config/host",  # Password change API (empty = none)
)
```

That's it. These operations now work automatically:
- Health probes (dashboard services panel)
- Auth validation (config drift detection)
- API key discovery at controller startup
- API key rotation (`POST /api/rotate-keys`)
- Password reset (`POST /api/reset-password`)

## Removing a Service

1. Delete the `ServiceDef` from `registry.py`
2. Optionally remove the app directory from `services/apps/`
3. The controller handles missing handlers gracefully — no crash

If config JSON still references handlers for the removed app, the controller logs a warning and skips: `[WARN] Handler not found: media_stack.services.apps.myapp... Skipping.`

## Service Categories

Services are grouped by category in the dashboard:

| Category | Services |
|---|---|
| **Media** | Jellyfin, Plex, Jellyseerr |
| **Automation** | Sonarr, Radarr, Lidarr, Readarr, Prowlarr, Bazarr |
| **Downloads** | qBittorrent, SABnzbd |
| **Management** | Maintainerr, Tautulli, Homepage, Envoy, FlareSolverr |

## Compose Profiles

Services with `profiles=["plex"]` are behind Docker Compose profiles. They don't start by default and show as "disabled" in the dashboard. Enable with:

```bash
docker compose --profile plex up -d
```

## API Key Formats

The registry supports these config formats:

| Format | Reader | Writer | Examples |
|---|---|---|---|
| `xml` | `<ApiKey>value</ApiKey>` | Same regex replace | Sonarr, Radarr, all *arr apps |
| `ini` | `api_key = value` | Same regex replace | SABnzbd, Tautulli |
| `yaml` | `auth.apikey: value` | YAML safe_load/dump | Bazarr |
| `json` | `main.apiKey: value` | JSON load/dump | Jellyseerr |
| `sqlite` | SQLite query on ApiKeys table | Via Jellyfin API | Jellyfin |

## Architecture

```
registry.py (source of truth)
    ↓
health.py ← SERVICE_PROBES, AUTH_PROBES (derived)
admin.py  ← rotate_keys(), reset_password() (registry-driven loops)
preflight/api_keys.py ← run_preflight() (registry-driven discovery)
dashboard.html ← SVCS array (TODO: should read from /api/services)
```

The controller (`controller_main.py`) and the core service (`controller_service.py`) have **zero hardcoded app names**. They operate on generic abstractions (arr_apps, media_server, download_clients) populated by the runtime factory from the config JSON.
