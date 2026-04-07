# Configuration Architecture

## Config Files

| File | Purpose | Editable? |
|---|---|---|
| `contracts/services/*.yaml` | **Per-service definitions** — one file per service with ports, probes, handlers, routing flags | Yes — primary way to add/remove services |
| `contracts/media-stack.profile.yaml` | **Deployment config** — routing, auth, technology bindings, bootstrap flags, app auth | Yes — per-deployment customization |
| `contracts/media-stack.catalog.yaml` | **Install profiles** — minimal/standard/full app presets, aliases, auth providers | Yes — define install tiers |
| `contracts/adapter-hooks.k8s.yaml` | **K8s pipeline** — deploy phases, secret priming, manifest paths (only loaded for K8s) | Advanced — K8s orchestration |
| `contracts/defaults/*.yaml` | **Stack-level defaults** — shared arr, download, operations settings | Yes — tune defaults |

## Adding a Service (No Code Changes Required)

1. **Create `contracts/services/myapp.yaml`** with service definition
2. **Restart the controller** — service appears in dashboard, health checks, key rotation
3. See `contracts/services/_template.yaml` for the full field reference

That's it. The service registry auto-loads all `*.yaml` files from `contracts/services/`.

For deeper integration, add `plugin:` and `defaults:` sections to the same YAML file:
- `plugin.preflight_handler` — HTTP health checks before bootstrap
- `plugin.event_handlers` — pipeline phase handlers (VALIDATE/RUN/ENSURE)
- `plugin.phase_scripts` — Python CLI modules for K8s deploy phases
- `plugin.call_handlers` — named handlers for K8s job phases
- `defaults:` — runtime config defaults merged during bootstrap

## Removing a Service

1. **Delete `contracts/services/myapp.yaml`** — probes/keys/rotation stop
2. **Optionally** delete `src/media_stack/services/apps/myapp/` — handler loading catches ImportError gracefully

## Swapping Implementations

Edit `contracts/media-stack.profile.yaml`:

```yaml
technology_bindings:
  torrent_client: transmission    # was: qbittorrent
  media_server: plex              # was: jellyfin
```

The technology binding determines which service handles each role. Create the per-service YAML for the new implementation and set the binding.

## Profile YAML Structure

```yaml
# Deployment identity
metadata:
  platform: compose          # compose | k8s
  purpose: standard

# What apps to enable
install_profile: standard    # minimal | standard | full
apps:
  lidarr: false              # per-app overrides

# How to bootstrap
bootstrap:
  preconfigure_apps: true
  trigger_indexer_sync: true
  refresh_health_after_setup: true

# Role-to-technology mappings
technology_bindings:
  torrent_client: qbittorrent
  usenet_client: sabnzbd
  media_server: jellyfin
  request_manager: jellyseerr
  indexer_manager: prowlarr

# Edge routing
routing:
  provider: envoy
  strategy: hybrid
  gateway_host: apps.media-stack.local

# Service-level auth (set passwords on apps)
app_auth:
  enabled: true
  method: Forms
  username_env: STACK_ADMIN_USERNAME
  password_env: STACK_ADMIN_PASSWORD
```

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `CONFIG_ROOT` | `/srv-config` | Shared config volume mount |
| `BOOTSTRAP_PROFILE_FILE` | (image-embedded) | Profile YAML path |
| `BOOTSTRAP_CONFIG_FILE` | (image-embedded) | Config JSON path (optional, can be empty) |
| `SERVICES_REGISTRY_DIR` | (image-embedded) | Per-service YAML directory |
| `MEDIA_STACK_PLATFORM` | (from profile) | Override platform detection |
| `FULLY_PRECONFIGURED` | `0` | Auto-run bootstrap on startup |
| `STACK_ADMIN_USERNAME` | `admin` | Admin credentials |
| `STACK_ADMIN_PASSWORD` | `media-stack` | Admin credentials |

## Config Loading Pipeline

```
contracts/services/*.yaml     → Per-service defaults + plugin manifests
contracts/defaults/*.yaml     → Stack-level defaults (arr, downloads, ops)
contracts/media-stack.profile.yaml → technology_bindings, app_auth, bootstrap flags
contracts/adapter-hooks.{platform}.yaml → Platform-specific pipeline (k8s only)
                              ↓
                    ControllerConfigLoader merges all sources
                              ↓
                    TopLevelBootstrapConfig validates structure
                              ↓
                    ControllerRuntimeFactory builds runtime
                              ↓
                    ControllerService runs pipeline phases
```

## Third-Party Extensibility

See `contracts/services/_template.yaml` for the complete developer guide.

| Scenario | Code required? |
|---|---|
| Add a new service | No — create per-service YAML |
| Swap an implementation | No — change technology_bindings in profile |
| Add a new role | No — add to technology_bindings |
| Add a new router provider | Yes — Python provider code |
| Add a new auth provider | Mostly no — catalog + profile YAML |
| Create a new pipeline job | Yes — Python action executors |
