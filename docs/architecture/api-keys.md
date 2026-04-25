# API keys — the two sources of truth

The 2026-04 `discover-api-keys` regression had a single root cause:
two independent code paths read service API keys from two
different places, and the loser silently fell back to an empty
string. This doc is the canonical map of where keys live, who
writes them, who reads them, and which contract tests lock the
invariant.

## Where keys live

| Location                       | Role                            | Lifetime                                       |
| ------------------------------ | ------------------------------- | ---------------------------------------------- |
| K8s `media-stack-secrets`      | Cluster-canonical store         | Survives pod restarts; mounted as env vars     |
| On-disk service config files   | Per-service source of truth     | Persists in the service's PVC                  |
| `runtime_keys` in-process cache| Hot-path read amplifier         | 30s TTL, per controller process                |

## Ordered precedence

Reads must always go through `runtime_keys.read_service_api_key`,
which applies this precedence:

```
env (os.environ['<SVC>_API_KEY'])  →  on-disk config file  →  None
```

**Env wins** because the K8s Secret is the cluster-canonical
store and is the cheapest source. We fall back to the on-disk
config file when the env entry is missing or empty (typical on
first boot, or just after a service rotates its key in its UI).
Returning `None` (rather than an empty string) lets callers
surface a clear error to the operator instead of silently
sending a blank credential.

## Who writes

| Writer                                              | Writes to              |
| --------------------------------------------------- | ---------------------- |
| Bootstrap (controller boot)                         | PVC config files       |
| `discover-api-keys` job                             | `media-stack-secrets`  |
| Operator (`kubectl edit secret`, key-rotation UI)   | `media-stack-secrets`  |

## Who reads

Exactly one reader: `runtime_keys.read_service_api_key`.

Direct `os.environ.get('*_API_KEY')` reads outside the helper
are **banned** — the static-analysis ratchet at
`tests/ratchets/test_no_direct_env_keys.py` walks the source
tree on every CI run and fails the build if a fresh direct read
appears. Legitimate exceptions:

- `runtime_keys.py` itself — it implements the helper.
- Test files — may mock `os.environ` directly.
- `STACK_ADMIN_*` env reads — those are admin credentials, not
  service API keys, and live in a different lifecycle.

## Contract tests that lock this

| Ratchet | File                                                          | Asserts                                               |
| ------- | ------------------------------------------------------------- | ----------------------------------------------------- |
| #3      | `tests/services/test_libraries_e2e.py`                        | env-empty + file-real → libraries return real data    |
| #4      | `tests/jobs/test_discover_api_keys_secret_writeback.py`       | bootstrap → Secret round-trip; empty values don't overwrite |
| #6      | `tests/services/test_runtime_keys_consistency.py`             | 3×3 env × file matrix; env wins, file fallback, None  |
| #7      | `tests/api/test_runtime_contract.py`                          | live `/api/libraries`, `/api/recent`, `/api/indexers` actually carry data |
| #10     | `tests/ratchets/test_no_direct_env_keys.py`                   | no direct `*_API_KEY` env reads outside `runtime_keys.py` |

## ASCII flow

```
                ┌──────────────────────────────┐
                │  on-disk config files (PVC)  │
                │  config.xml / Server.xml /   │
                │  Jellyfin SQLite DB / etc.   │
                └────────────────┬─────────────┘
                                 │
      bootstrap                  │ discover-api-keys job
   (writes file)                 │ harvests + base64-encodes
                                 ▼
                ┌──────────────────────────────┐
                │  K8s media-stack-secrets     │ ◄── operator
                │  (mounted as env on the      │     (kubectl edit)
                │   controller pod)            │
                └────────────────┬─────────────┘
                                 │ os.environ at process start
                                 ▼
                ┌──────────────────────────────┐
                │  runtime_keys cache (30s)    │
                │  read_service_api_key(svc)   │ ──── single legal reader
                │     ├─ env first             │
                │     ├─ file fallback         │
                │     └─ None otherwise        │
                └────────────────┬─────────────┘
                                 │
        ┌────────────────────────┼─────────────────────────┐
        ▼                        ▼                         ▼
  /api/libraries          /api/recent              /api/indexers
  (services/content.py)   (services/content.py)   (services/content.py)
```

## When you change something here

- Touching the precedence rule? Update this doc, then re-read the
  3×3 matrix in ratchet #6 to be sure every cell still matches.
- Adding a new key source (e.g. Vault)? Pin its precedence,
  document it here, and extend the matrix.
- Adding a new caller? Route it through
  `runtime_keys.read_service_api_key` only. The static-analysis
  ratchet will fail the PR if you bypass it.
