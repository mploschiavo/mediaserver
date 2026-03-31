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
