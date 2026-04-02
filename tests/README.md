# Automated Tests

## Folder layout
- `tests/unit/` -> Python unit tests for reusable bootstrap helpers.
- `tests/e2e/playwright/` -> Playwright ingress/browser smoke tests.
- `tests/e2e/api/` -> API-level integration checks for Arr/Prowlarr/Jellyseerr/qB/SAB/Bazarr wiring.
- `tests/e2e/playwright/tests/screenshot-capture.spec.ts` -> deterministic app UI screenshot capture.

## Run locally
```bash
bash scripts/test.sh
```

Run only a subset and keep telemetry output focused:
```bash
UNIT_TEST_PATTERN='test_bootstrap_services_*.py' UNIT_TEST_TOP_N=15 bash scripts/test.sh
```

Add per-test timeout protection for runaway cases:
```bash
UNIT_TEST_TIMEOUT_SECONDS=30 UNIT_TEST_TOP_N=20 bash scripts/test.sh
```

Run the unit telemetry runner directly:
```bash
bash scripts/lib/run-python-cli.sh run_unit_tests_main.py --pattern 'test_*.py' --top-n 20
```

Run with Playwright against a live cluster ingress:
```bash
RUN_PLAYWRIGHT=1 STACK_NODE_IP=<NODE_IP> bash scripts/test.sh
# or directly:
bash scripts/run-playwright-smoke.sh <NODE_IP> [NAMESPACE]
```

Capture app screenshots via Playwright:
```bash
bash scripts/run-playwright-screenshots.sh <NODE_IP> [NAMESPACE]
```

Capture Kubernetes terminal evidence:
```bash
bash scripts/capture-k8s-snapshots.sh [NAMESPACE]
```

Run API relationship verification against a live namespace:
```bash
RUN_API_E2E=1 NAMESPACE=media-stack bash scripts/test.sh
# or directly:
python3 tests/e2e/api/verify_api_relationships.py --namespace media-stack
bash scripts/run-api-e2e.sh media-stack
```

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
