# Testing

## Overview

The test suite validates the media automation stack at multiple levels: unit tests for business logic, architecture enforcement tests that prevent service-name leaks into platform code, and end-to-end tests for API and browser verification.

| Layer | Count | Tool | Purpose |
|-------|-------|------|---------|
| Unit | 2294+ | pytest | Business logic, service wiring, config parsing |
| Architecture enforcement | 5 | pytest | Hardcoded service name scanner, filename scanner |
| Cache isolation | 4 | pytest | Singleton LRU cache leak detection |
| API E2E | varies | bash/curl | Controller API contract verification |
| Browser E2E | varies | Playwright | Ingress routing, UI smoke tests |
| Screenshot capture | 17 apps | Playwright | Visual evidence of running services |
| Cluster snapshot | varies | kubectl | Kubernetes state evidence |

## Running Tests

```bash
# All unit tests
python -m pytest tests/unit/ -q

# Specific test file
python -m pytest tests/unit/test_bootstrap_profile.py -v

# Architecture enforcement only
python -m pytest tests/unit/test_no_hardcoded_services.py -v

# Full test suite with all layers
bash bin/test.sh

# Browser smoke tests (requires running stack)
bash bin/run-playwright-smoke.sh

# API E2E (requires running stack)
bash bin/run-api-e2e.sh

# App UI screenshots (requires running stack)
bash bin/run-playwright-screenshots.sh <NODE_IP> [NAMESPACE]

# Kubernetes cluster snapshots
bash bin/capture-k8s-snapshots.sh [NAMESPACE] [OUT_DIR]
```

## Architecture Enforcement

The `test_no_hardcoded_services.py` scanner prevents service-specific references from leaking into platform code. This is the primary enforcement mechanism for the plugin isolation architecture.

### Content Scanner

Walks every `.py` file under `src/media_stack/` (excluding `services/apps/`, `contracts/`, `api/preflight/`, `api/services/admin.py`) and flags any word-boundary match for service names like `jellyfin`, `sonarr`, `qbittorrent`, etc.

The scanner skips:
- Comments (`#`)
- Docstrings (multi-line `"""` tracking)
- Standalone string literals
- Imports from `services.apps.*`

**Current state: 0 allowlist entries.** All service-specific logic lives in `services/apps/`.

### Filename Scanner

Ensures no Python files outside `services/apps/` have service-specific names (e.g., `jellyfin_helper.py`, `qbit_ops.py`).

**Current state: 0 filename allowlist entries.** All shim files deleted.

### Adding to the Allowlist

If a platform-level reference is truly unavoidable:

```python
ALLOWLIST: dict[str, set] = {
    "path/relative/to/src/media_stack/file.py": {
        (42, "servicename"),  # Brief justification
    },
}
```

New entries cause the test to fail, forcing conscious review.

## Singleton Cache Isolation

Several production modules use `@lru_cache` on functions that read from the service registry or YAML contracts. The `tests/unit/conftest.py` fixture clears all LRU caches before and after each test function to prevent stale data from leaking across tests.

Protected caches:
- `catalog_loader._load_bootstrap_profile_catalog_cached`
- `platform_plugin_registry.load_platform_plugins`
- `controller_config_policy._load_policy_catalog`
- `edge/provider_registry.load_builtin_edge_router_provider_specs`
- `compose/edge/provider_registry.load_compose_edge_provider_plugins`

The `test_singleton_cache_isolation.py` file validates this mechanism:
- Verifies all LRU caches expose `cache_clear()`
- Proves poisoned caches don't persist after clearing
- Tests the runtime `reload_registry()` + `clear_catalog_cache()` cycle

## Coverage Target

Target: **85%** unit test coverage across the entire project.

## Adding Tests for New Services

When adding a new service to `services/apps/{service}/`:

1. Create unit tests in `tests/unit/test_bootstrap_apps_{service}.py`
2. The architecture scanner will automatically verify your code stays in `services/apps/`
3. No platform code changes should be needed -- if the scanner flags a violation, move the logic to your app directory
4. Add your service's YAML contract to `contracts/services/{service}.yaml`
5. The pluggability contract tests (`test_technology_pluggability_contracts.py`) will verify your manifest registrations are importable
