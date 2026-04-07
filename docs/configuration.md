# Configuration Architecture

## Config Files

| File | Purpose | Editable? |
|---|---|---|
| `contracts/services.yaml` | **Service registry** — all managed services, ports, probes, keys | Yes — primary way to add/remove services |
| `contracts/media-stack.config.json` | Bootstrap pipeline config — handler specs, app settings, guardrails | Advanced — controls the pipeline behavior |
| `contracts/media-stack.profile.yaml` | Stack profile — routing, bootstrap behavior, app enable/disable | Yes — per-deployment customization |
| `src/media_stack/contracts/plugins/*/manifest.json` | Per-app plugin manifests — adapter classes, capabilities | Advanced — defines how each app integrates |
| `src/media_stack/contracts/runner_operation_plans.json` | Pipeline phase definitions — what runs in what order | Advanced — pipeline orchestration |

## Adding a Service

1. **Edit `contracts/services.yaml`** — add a service entry with id, port, health path, etc.
2. **Optionally** create `services/apps/myapp/` with runtime code
3. **Optionally** add a plugin manifest at `src/media_stack/contracts/plugins/myapp/manifest.json`
4. **Optionally** add handler specs to `contracts/media-stack.config.json`

Step 1 is all that's needed for health probes, auth, key management. Steps 2-4 are for deeper integration (bootstrap pipeline, config policy, etc.).

## Removing a Service

1. **Delete from `contracts/services.yaml`** — probes/keys/rotation stop
2. **Optionally** delete `services/apps/myapp/` — handler loading catches ImportError gracefully
3. Config JSON handler refs that point to removed code log "[WARN] Handler not found" and skip

## Config Sprawl — Known Issues

The 60KB `media-stack.config.json` contains too many concerns:
- App-specific settings (should be in plugin manifests)
- Handler import paths (should be auto-discovered from plugins)
- Guardrail thresholds (should be in profile YAML)
- Pipeline phases (already separated to runner_operation_plans.json)

Future direction: config.json should only contain stack-level settings. Per-app config should live in the plugin manifests. Handler discovery should scan `services/apps/*/plugin.py` automatically.

## Environment Variables

The controller reads these at startup:

| Variable | Default | Purpose |
|---|---|---|
| `CONFIG_ROOT` | `/srv-config` | Shared config volume mount |
| `BOOTSTRAP_PROFILE_FILE` | (image-embedded) | Profile YAML path |
| `BOOTSTRAP_CONFIG_FILE` | (image-embedded) | Config JSON path |
| `SERVICES_REGISTRY_FILE` | (image-embedded) | Services YAML path |
| `FULLY_PRECONFIGURED` | `0` | Auto-run bootstrap on startup |
| `STACK_ADMIN_USERNAME` | `admin` | Admin credentials |
| `STACK_ADMIN_PASSWORD` | `media-stack` | Admin credentials |
| `K8S_NAMESPACE` | (empty) | K8s namespace (empty = compose mode) |
| `*_API_KEY` | (empty) | Per-service API keys (discovered at startup) |

## Dependency Injection

The runtime factory uses dependency injection to avoid hardcoded app imports:

```
controller_main.py → ControllerRuntimeFactoryService(deps=...)
                      ↓
                      runtime_builder.py → builds ControllerRuntime
                      ↓
                      controller_service.py → runs pipeline phases
                      ↓
                      runner_operation_plans.json → declares phase steps
                      ↓
                      plugins/*/manifest.json → declares app adapters
```

App-specific code is loaded dynamically via import paths in the config. If an app's code is missing, the handler loading catches `ImportError` and skips gracefully.
