# Adding a Service

The platform enforces a strict two-location rule. To add a service, you edit:

1. `contracts/services/<name>.yaml` — service metadata, API key format, health endpoint.
2. `src/media_stack/services/apps/<name>/` — all implementation code.

**Zero changes to platform code are required.** The `services/apps/` directory is designed to be extractable into a separate git repo; if you can't add your service without touching `cli/commands/` or `services/runtime_factory/`, the abstraction is wrong and that's a bug.

## Walkthrough — adding "myservice"

### 1. Service contract

Create `contracts/services/myservice.yaml`:

```yaml
service:
  id: myservice
  name: MyService
  desc: One-line description of what it does
  category: <one of: media_server, indexer, downloader, request_manager, subtitles, monitoring, downloads, etc.>
  host: myservice          # container/service name in compose + k8s
  port: 8080               # internal port the service listens on
  health_path: /api/health # GET endpoint that returns 200 when healthy
  api_key_format: header   # header | query | basic
  api_key_header: X-Api-Key
  web_ui: true             # exposes a browser UI
  scale_to_zero: false     # OK to KEDA-scale-to-zero?

plugin:
  jobs:                    # optional — bootstrap jobs this service registers
    ensure-myservice-config:
      handler: media_stack.services.apps.myservice.adapters:ensure_myservice_config
      label: "Configure MyService defaults"
      phase: post
      priority: 95
      requires: []
```

### 2. Implementation

Create `src/media_stack/services/apps/myservice/`:

```
src/media_stack/services/apps/myservice/
├── __init__.py
├── adapters.py            # the ensure_* functions referenced by jobs
├── config.py              # config resolution / parsing
└── runtime_ops.py         # any service-specific runtime operations
```

Pattern for `adapters.py`:

```python
from media_stack.api.services.registry import service_internal_url

def ensure_myservice_config(*, log=None, **kwargs) -> None:
    """Idempotent — re-asserts MyService's desired config state."""
    def info(msg: str) -> None:
        if log:
            log(msg)
    url = service_internal_url("myservice")
    # ... GET current state, compare, PUT/POST if different ...
    info("MyService: config converged")
```

### 3. Add the promise

If the new service ships a guarantee (e.g. "MyService has its API key configured after a fresh install"), add an entry to `contracts/promises/promises.yaml` and re-render the reference. See [promises-registry.md](promises-registry.md) for the full procedure.

### 4. Add the service to the deployment

Both Compose and Kubernetes pick up the contract automatically — but you still need to add the service to the runtime spec:

- **Compose**: add a service block in `docker/docker-compose.yml`. Mount `${CONFIG_ROOT}/myservice:/config` and any data volumes.
- **Kubernetes**: add a Deployment + Service manifest in `k8s/optional.yaml` (or as its own file in `k8s/profiles/<profile>/`).

### 5. Wire the dashboard (optional)

The dashboard discovers services via `/api/services`, which reads the registry. Your new service appears automatically once the contract is loaded. If you want a direct "Open MyService" button in the wizard's Access Your Services step, add the service ID to `userFacingIds` in `src/media_stack/api/dashboard.html`.

### 6. Test it

```bash
# unit tests
bash bin/test.sh

# meta-ratchet — confirms the contract + handler resolve
python3 -m pytest tests/unit/test_promises_registry.py -v

# fresh-install verifier
bash bin/verify-fresh-install.sh
```

## What you must NOT do

- **Don't add `if service == "myservice":` branches to platform code.** That's the abstraction the registry is meant to replace. Use `category` lookups instead (`category="indexer"`, `category="downloader"`).
- **Don't import from `services/apps/` in platform code.** Platform code uses the registry and the `adapter_hooks` plan. App code is the leaf.
- **Don't write a one-shot bootstrap script that mutates state.** Write an idempotent adapter that re-asserts state on every run, and register it as a contract job. Then add a promise so the meta-ratchet enforces it.

## Reference patterns

The cleanest existing examples to crib from:

- **A simple stateless service**: `contracts/services/flaresolverr.yaml` + `src/media_stack/services/apps/flaresolverr/`.
- **A service with a complex configurator**: `contracts/services/bazarr.yaml` + `src/media_stack/services/apps/bazarr/`.
- **A service whose config is generated post-bootstrap**: `contracts/services/unpackerr.yaml` + `src/media_stack/api/preflight/unpackerr.py`. Uses `post_setup_handler` instead of a job because it depends on other services' API keys being discovered first.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
