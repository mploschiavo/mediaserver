# Principles

The "why" behind Media Stack. If you're extending the project — adding a service, changing the bootstrap flow, deciding how a new feature should behave — these are the constraints to design within.

## Why this project exists

Media stacks usually start as a collection of manually-configured apps and decay into fragile installs:

- credentials drift between apps
- path mappings drift between Arr and the downloader
- post-install click paths get forgotten or lost when the maintainer leaves
- rebuilding a node becomes expensive enough that nobody does it

Media Stack exists to turn that into a declarative platform where rebuild is a normal workflow, not an outage. The 11 operational principles below are the rules that make that possible.

### Who this helps

- **Self-hosters** who want a polished streaming setup that doesn't drift over time.
- **Platform engineers** who want repeatable homelab/lab/prod-like environments.
- **Households or small teams** who need stable automation across host or cluster changes.

### What it solves better than ad hoc installs

- End-to-end service wiring derived from code, not screenshots.
- Shared secret strategy with deterministic regeneration.
- Explicit deployment profiles with namespace isolation.
- Built-in verification scripts that confirm route health and integration plumbing.
- Operations documentation suitable for handoff and long-term maintenance.

---

## The 11 operational principles

### 1. Rebuildability over fragility

Treat full namespace recreation as a normal operation. If rebuild is painful, automation is incomplete. Every "I'll just SSH in and fix it" moment is a debt; a fix that doesn't survive `compose down -v` doesn't count.

### 2. Declarative first

Prefer changing manifests, bootstrap config, or scripts. Avoid permanent UI-only configuration. UI tuning is allowed for personalization (per-user playback prefs, library colors); it is not allowed for stack-wide wiring (download clients, indexers, auth).

### 3. Idempotent automation

Every install / contract / reconcile action should be safe to rerun and should converge state, not duplicate resources. Running bootstrap twice should produce the same end state as running it once.

### 4. Fail fast, log clearly

- Phase-based logs with timings.
- Explicit error context and diagnostics, never silent swallows.
- Health and smoke checks integrated into the normal flow, not bolted on later.

### 5. Secrets discipline

- Centralize credentials in Kubernetes secrets (or compose env-files).
- Generate secure defaults when missing.
- Support deterministic credential sync where app APIs are brittle.

### 6. Drift control

- Bootstrap job and reconcile loop enforce desired state.
- Verification scripts (the promises registry, the meta-ratchet, `bin/verify-fresh-install.sh`) detect silent integration breakage.
- Promote runtime fixes back into declarative config — never leave a working hack uncodified.

### 7. Namespace isolation

Use separate namespaces and ingress domains for dev / e2e / prod-style environments. The same contracts deploy unchanged across all three.

### 8. UX is a platform concern

Playback quality, metadata quality, and discovery rails are first-class config — not afterthoughts. The dashboard wizard is a doc surface; user-facing strings carry no developer-speak.

### 9. Operational feedback loops

- Regular flow verification (`bin/verify-stack.sh`).
- Backup/restore drills.
- Clear rollback and rerun procedures.

### 10. Progressive hardening

Start with stable defaults, then layer:

- TLS (self-signed → real cert)
- network boundaries
- storage migration plans
- GitOps promotion flows

### 11. Pluggability by contract

- Choose active technologies via `technology_bindings`.
- Register technologies only through per-service YAML contracts.
- Keep shared orchestration generic; put app-specific behavior in app/adapter modules.
- Prove swap safety with contract and matrix tests before merge.

---

## Community tooling worth knowing

The bootstrap covers end-to-end automation, but these patterns layer on cleanly when you outgrow the defaults.

| Tool | When to add it | Reference |
|---|---|---|
| **Prowlarr** (already core) | Single source of truth for indexers; syncs into Arr apps so per-app indexer setup is unnecessary. | <https://github.com/Prowlarr/Prowlarr> |
| **Recyclarr** | When you want Sonarr/Radarr quality profiles and custom formats managed as code (TRaSH-style). | <https://recyclarr.dev/> |
| **Buildarr** | When you want broader declarative config across additional Arr apps via plugins. | <https://buildarr.github.io/> |
| **KEDA** | Scale-to-zero for non-interactive workers only. Cold starts hurt interactive UX (Jellyfin / Jellyseerr / Arr) — keep those always-on. | <https://keda.sh/docs/> |
| **Envoy Gateway** | If you want Kubernetes-native Envoy control via the Gateway API. | <https://gateway.envoyproxy.io/docs/> |

### Adoption order

1. Run `bin/deploy-stack.sh` (or the dist compose) — that's the baseline.
2. Add Recyclarr if quality-profile drift becomes a problem.
3. Introduce Buildarr only when you need full declarative lifecycle for additional apps.
4. Test scale-to-zero on non-critical services first.

### Path-mapping rule

Keep `/data` paths identical across Arr + downloader containers. Shared volumes and coherent paths prevent import failures and preserve hardlink/rename behavior. (Servarr/Prowlarr docs cover this in detail.)

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
