# Operational Principles

## 1) Rebuildability Over Fragility

Treat full namespace recreation as a normal operation. If rebuild is painful, automation is incomplete.

## 2) Declarative First

Prefer changing:
- manifests
- bootstrap config
- scripts

Avoid permanent UI-only configuration.

## 3) Idempotent Automation

Every install/bootstrap/reconcile action should be safe to rerun and should converge state rather than duplicate resources.

## 4) Fail Fast, Log Clearly

- phase-based logs with timings
- explicit error context and diagnostics
- health and smoke checks integrated into normal workflow

## 5) Secrets Discipline

- centralize credentials in Kubernetes secrets
- generate secure defaults when missing
- support deterministic credential sync where app APIs are brittle

## 6) Drift Control

- bootstrap job + reconcile cron enforce desired state
- verification scripts detect silent integration breakage
- promote runtime fixes back into declarative config

## 7) Namespace Isolation

Use separate namespaces and ingress domains for dev/e2e/prod-style environments.

## 8) UX Is a Platform Concern

Playback quality, metadata quality, and discovery rails are treated as first-class config, not afterthoughts.

## 9) Operational Feedback Loops

- regular flow verification
- backup/restore drills
- clear rollback and rerun procedures

## 10) Progressive Hardening

Start with stable defaults, then layer:
- TLS
- network boundaries
- storage migration plans
- GitOps promotion flows
