# Software Development Lifecycle (SDLC)

## Development Workflow

### Branch Strategy

All work happens on feature branches merged to `main`. The `main` branch is always deployable.

```
main (always deployable)
  └── feature/add-new-service
  └── fix/bootstrap-timeout
  └── refactor/split-monolith
```

### Development Loop

1. **Branch** from `main`
2. **Implement** changes in `services/apps/{service}/` for service-specific code
3. **Test locally** with `python -m pytest tests/unit/ -q`
4. **Verify architecture** with `python -m pytest tests/unit/test_no_hardcoded_services.py -v`
5. **Build image** with `docker build -f docker/controller.Dockerfile -t media-stack-controller:latest .`
6. **Deploy locally** with `docker compose up -d media-stack-controller`
7. **Verify runtime** via controller dashboard at `http://localhost:9100/`
8. **Push** and create PR

## CI Pipeline

GitHub Actions (`.github/workflows/ci.yml`) runs on every push:

| Job | Purpose | Tools |
|-----|---------|-------|
| **preflight-fast** | Syntax and compile checks | `python -m compileall`, shellcheck |
| **validate** | Lint + type check + unit tests | ruff, black, mypy, pytest |
| **kind-smoke** | Kubernetes deployment test | kind, kustomize, kubectl |
| **playwright-spec-check** | Browser test discovery | Playwright |

### Architecture Enforcement in CI

The `validate` job runs the hardcoded service scanner. Any new service-name reference in platform code fails the build. This ensures the plugin isolation architecture is maintained.

## Architecture Enforcement

### Service Isolation Contract

All service-specific code must live in `src/media_stack/services/apps/{service}/`. Platform code (`src/media_stack/` outside `services/apps/`) must not contain hardcoded service names.

**Enforced by:** `tests/unit/test_no_hardcoded_services.py`

- Content scanner: 0 allowlist entries (all eliminated)
- Filename scanner: 0 allowlist entries (all shims deleted)
- Runs in CI on every push

### Adding a New Service

No platform code changes required:

1. Create `contracts/services/{service}.yaml` with service metadata
2. Create `src/media_stack/services/apps/{service}/` with implementation
3. The service registry auto-discovers from YAML contracts
4. The architecture scanner verifies isolation automatically

## Test Pyramid

```
        /  E2E (Playwright)  \        Slow, browser-based
       /   API E2E (curl)     \       Medium, HTTP contract
      /    Unit Tests (2294+)  \      Fast, isolated
     /  Arch Enforcement (9)    \     Fast, structural
    /___________________________\
```

- **Unit tests** are the primary quality gate (2294+ tests, ~40s)
- **Architecture tests** prevent structural regression
- **API E2E** validates controller HTTP contract
- **Browser E2E** validates ingress routing and app UIs

## Release Process

### Docker Image Build

```bash
# Build locally
docker build -f docker/controller.Dockerfile -t media-stack-controller:latest .

# Build and push to registry
docker build -f docker/controller.Dockerfile -t registry.example.com/media-stack-controller:latest .
docker push registry.example.com/media-stack-controller:latest
```

### Deploy

```bash
# Docker Compose
cd docker && docker compose up -d media-stack-controller

# Kubernetes
kubectl apply -k k8s/all/
```

### Verify

```bash
# Check controller health
curl http://localhost:9100/healthz

# Check bootstrap status
curl http://localhost:9100/status

# View all API keys
curl http://localhost:9100/api/keys

# Run API E2E
bash bin/run-api-e2e.sh
```

## Code Review Checklist

- [ ] No service-specific code outside `services/apps/`
- [ ] Architecture scanner passes (`test_no_hardcoded_services.py`)
- [ ] All unit tests pass (`python -m pytest tests/unit/ -q`)
- [ ] No new `ALLOWLIST` entries without justification
- [ ] Service contracts updated if adding/modifying services
- [ ] Config changes use YAML defaults, not hardcoded values
- [ ] Docker image builds successfully
- [ ] Runtime verified on local stack (if touching bootstrap flow)
