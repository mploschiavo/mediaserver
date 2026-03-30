# Why This Exists

Media stacks often start as a collection of manually configured apps and eventually become fragile:
- credentials drift
- path mappings drift
- post-install click paths are forgotten
- rebuilding a node becomes expensive and risky

This project exists to provide a declarative reference architecture where:
- Kubernetes resources are managed as infrastructure-as-code
- app wiring is managed as configuration-as-code
- reinstall/rebuild is a normal workflow, not an outage event

## Design Intent

- Make fresh rebuilds predictable.
- Keep behavior convergent through idempotent bootstrap/reconcile flows.
- Reduce manual UI work to optional tuning only.
- Keep multi-namespace environments possible for testing and promotion.

## Who This Helps

- Self-hosters who want a polished streaming platform without manual drift.
- Platform engineers who want repeatable homelab/lab/prod-like environments.
- Teams or households who need stable automation after host or cluster changes.

## What It Solves Better Than Ad Hoc Installs

- End-to-end service wiring from code, not screenshots.
- Shared secret strategy with deterministic regeneration.
- Explicit deployment profiles and namespace isolation.
- Built-in verification scripts to confirm route health and service integrations.
- Architecture/operations documentation suitable for handoff and long-term maintenance.
