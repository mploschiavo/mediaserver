# Regenerating the Controller OpenAPI spec

## Where the spec lives

The single source of truth is:

- **`src/media_stack/api/openapi.yaml`** — hand-maintained OpenAPI 3.0
  document rendered at runtime by the `/api/docs` (Redoc) endpoint and
  served verbatim at `/api/openapi.yaml` / `/api/openapi.json`.

The `servers:` block in the file is a placeholder — `server.py` rebuilds
it at request time from the live routing config (see
`_build_openapi_servers()`).

## How to update it when you add a route

1. Land the handler in `src/media_stack/api/handlers_get.py` or
   `src/media_stack/api/handlers_post.py`.
2. Open `src/media_stack/api/openapi.yaml` and add a new path entry
   under `paths:`. Minimum fields:
   - `path` (the key)
   - HTTP method (`get` / `post` / ...)
   - `operationId` — unique across the spec
   - `tags` — pick one of the existing tags (see the `tags:` block at
     the top of the file). Add a new tag only when the route genuinely
     doesn't fit any existing category.
   - `summary` — single-sentence description
   - `responses` — at minimum a `"200"`; add `"400"` / `"401"` /
     `"403"` / `"404"` / `"429"` / `"500"` only when the handler
     actually returns them
   - `security:` block with `- basicAuth: []` and/or
     `- bearerAuth: []` when the handler enforces auth
3. Keep paths alphabetically sorted **within each tag section** to
   minimise diff churn.

### Marking a future route as "planned"

If you're landing the spec ahead of the handler (e.g. the
session-visibility PR wave), tag each operation with:

```yaml
x-status: planned
x-target-release: v1.x-session-visibility
```

The drift ratchet skips planned operations on the
handler-implementation side, so CI stays green until the handler
lands. Remove both extensions (or flip `x-status` to `released`) once
the handler is wired.

## The drift ratchet

The ratchet lives at
**`tests/unit/test_openapi_drift_ratchet.py`**. It enforces two
invariants:

- `test_handler_routes_in_spec` — every route dispatched by
  `handlers_get.py` / `handlers_post.py` appears in the spec (modulo
  `_HANDLER_ONLY_ALLOWLIST`).
- `test_spec_routes_have_handlers` — every spec path **without**
  `x-status: planned` has a matching dispatcher branch (modulo
  `_SPEC_ONLY_ALLOWLIST`).

Run it with the project venv:

```bash
cd /path/to/media-automation-stack-v4-intel-jellyfin/media-automation-stack
.venv/bin/python3 -m pytest tests/unit/test_openapi_drift_ratchet.py -v
```

If you see **"handler-dispatched routes are NOT documented"**, add
the listed paths to the spec (not the allowlist — the allowlist is
for internal probes that will never be public).

If you see **"spec paths have no matching dispatcher"**, either:
- Wire up the handler, OR
- Add `x-status: planned` to the operation(s), OR
- Add the path to `_SPEC_ONLY_ALLOWLIST` with a one-line reason
  (reserved for composite/dynamic routes that are matched by a
  predicate, not a literal string).

## Worked example

You just added a new GET endpoint `/api/feature-flags` that reads the
flag store and returns its contents:

```python
# handlers_get.py
elif path == "/api/feature-flags":
    from .services import feature_flags as ff_svc
    handler._json_response(200, ff_svc.list_flags())
```

Add to `openapi.yaml` — placed alphabetically inside the `Config`
section:

```yaml
  /api/feature-flags:
    get:
      operationId: listFeatureFlags
      tags: [Config]
      summary: Current feature-flag state
      responses:
        "200":
          description: Flags
          content:
            application/json:
              schema:
                type: object
                additionalProperties: true
```

Re-run the ratchet:

```bash
.venv/bin/python3 -m pytest tests/unit/test_openapi_drift_ratchet.py -v
```

Both tests should pass. Commit both files in the same PR so CI never
sees a state where the handler exists without its spec entry.
