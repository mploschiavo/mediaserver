# API response fixture capture

Each `*.json` here is a real response from a running controller, used by
[`tests/unit/test_api_response_contract.py`](../../unit/test_api_response_contract.py)
to validate that the live response matches `openapi.yaml`. The fixtures
are committed to git so the contract check runs in CI without a live
cluster.

## Refresh workflow

When you change a response shape (handler edit, new field, schema
tightening), refresh the affected fixture:

```bash
CTRL_POD=$(kubectl -n media-stack get pod -l app=media-stack-controller \
                       -o jsonpath='{.items[0].metadata.name}')
kubectl -n media-stack exec "$CTRL_POD" -- python3 -c "
import urllib.request, json
r = urllib.request.urlopen(urllib.request.Request(
    'http://localhost:9100/api/<PATH>',
    headers={'Remote-User':'admin'}), timeout=10)
print(json.dumps(json.loads(r.read()), indent=2, sort_keys=True))
" > tests/fixtures/api_responses/<filename>.json
```

Commit the fixture **alongside** the spec / handler change — the
single PR proves the three sides agree.

## Bulk refresh (all simple GETs)

After a wide change (new spec version, batch handler edit), regenerate
every fixture in one go:

```bash
bash tools/recapture-all-fixtures.sh
```

The script walks `openapi.yaml` for `/api/...` GET paths with no
path-template parameters and captures each. Fixtures for endpoints
that need parameters (e.g. `/api/users/{id}`) must be hand-curated —
add them with a representative ID committed alongside.

## Adding a new endpoint

1. Add path + 200 schema to `openapi.yaml`.
2. `npm run gen:api` (in `ui/`) — regenerates `src/api/types.ts`.
3. Implement the handler.
4. Capture a fixture (snippet above).
5. Add the fixture filename → API path mapping to `ENDPOINTS` in
   `tests/unit/test_api_response_contract.py`.
6. Run `pytest tests/unit/test_api_response_contract.py` — both schema
   conformance and "no undeclared keys" validate.

## Why these are committed

- **Reproducible** — CI doesn't need a live cluster.
- **Reviewable** — diff in PRs shows exactly what shape changed.
- **Audit-friendly** — `git log --follow tests/fixtures/api_responses/foo.json`
  is the history of the response contract for that endpoint.

## Why these are not in `.gitignore`

They're contract artifacts, not test outputs. Treat them like the
spec itself — versioned, code-reviewed, never auto-generated outside a
deliberate refresh.
