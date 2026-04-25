# Automated Tests

## Test Suite Summary

- **2294+ unit tests** covering business logic, service wiring, config parsing
- **9 architecture enforcement tests** preventing service-name leaks into platform code
- **4 singleton cache isolation tests** preventing LRU cache pollution across tests
- **E2E API and Playwright tests** for runtime verification

See [docs/architecture/testing.md](../docs/architecture/testing.md) for the full testing guide and [docs/architecture/sdlc.md](../docs/architecture/sdlc.md) for the development lifecycle.

## Folder layout
- `tests/unit/` -- Python unit tests (2294+), architecture enforcement, cache isolation
- `tests/unit/conftest.py` -- Autouse fixture that clears LRU caches between tests
- `tests/unit/test_no_hardcoded_services.py` -- Architecture scanner (0 allowlist entries)
- `tests/unit/test_singleton_cache_isolation.py` -- Cache isolation validation
- `tests/browser/` -- Playwright ingress/browser smoke tests (was `tests/e2e/playwright/`)
- `tests/e2e/api/` -- API-level integration checks
- `tests/browser/tests/screenshot-capture.spec.ts` -- deterministic app UI screenshots

## Run locally
```bash
bash bin/test.sh
```

Run only a subset and keep telemetry output focused:
```bash
UNIT_TEST_PATTERN='test_bootstrap_services_*.py' UNIT_TEST_TOP_N=15 bash bin/test.sh
```

Add per-test timeout protection for runaway cases:
```bash
UNIT_TEST_TIMEOUT_SECONDS=30 UNIT_TEST_TOP_N=20 bash bin/test.sh
```

Run the unit telemetry runner directly:
```bash
bash bin/lib/run-python-cli.sh run_unit_tests_main.py --pattern 'test_*.py' --top-n 20
```

Run with Playwright against a live cluster ingress:
```bash
RUN_PLAYWRIGHT=1 STACK_NODE_IP=<NODE_IP> bash bin/test.sh
# or directly:
bash bin/run-playwright-smoke.sh <NODE_IP> [NAMESPACE]
```

Capture app screenshots via Playwright:
```bash
bash bin/run-playwright-screenshots.sh <NODE_IP> [NAMESPACE]
```

Capture Kubernetes terminal evidence:
```bash
bash bin/capture-k8s-snapshots.sh [NAMESPACE]
```

Run API relationship verification against a live namespace:
```bash
RUN_API_E2E=1 NAMESPACE=media-stack bash bin/test.sh
# or directly:
python3 tests/e2e/api/verify_api_relationships.py --namespace media-stack
bash bin/run-api-e2e.sh media-stack
```

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
