# Scripts Directory

## Structure

```
bin/
  install.sh              # Pre-deploy stack setup
  deploy-stack.sh         # Deploy to K8s
  bootstrap-all.sh        # Full K8s bootstrap pipeline
  run-bootstrap-job.sh    # Bootstrap job runner
  build-controller-image.sh # Build Docker image
  test.sh                 # Run test suite
  with-env.sh             # Load env file + run command
  controller.py           # Controller entrypoint

  lib/
    run-python-cli.sh     # Python CLI resolver (used by all wrappers)

  utils/                  # Operational utilities
    validate-bootstrap-config.sh
    validate-bootstrap-profile.sh
    backup-stack.sh / restore-stack.sh
    generate-secrets.sh
    setup-lan-tls.sh
    stack-status.sh
    apply-scale-policy.sh
    set-pvc-storage-class.sh
    prepare-host.sh / fix-media-perms.sh
    render-hosts-example.sh / render-dnsmasq-snippet.sh

  debug/                  # Per-service debugging
    ensure-qbit-credentials.sh
    ensure-sabnzbd-api-access.sh
    ensure-jellyfin-bootstrap.sh
    seed-jellyseerr-local-admin.sh
    sync-unpackerr-keys.sh
    reset-qbit-webui-auth.sh
    set-jellyfin-api-key.sh / set-qbit-secret.sh
    toggle-jellyfin-intel-gpu.sh
    reconcile-jellyfin-home-rails.sh
    run-prowlarr-auto-indexers.sh
    capture-k8s-snapshots.sh

  test/                   # Test and verification
    deploy-verify.sh / verify-flow.sh
    microk8s-smoke-test.sh
    run-playwright-smoke.sh / run-playwright-screenshots.sh
    run-api-e2e.sh / run-integration-test.sh
    fast-first-run.sh / watch-install.sh

  k8s/                    # K8s-specific
    microk8s-reconcile.sh
    microk8s-patch-ingress-class.sh

  docs/                   # Documentation
    render-architecture-diagrams.sh
```

## Design Rules

- Shell scripts are thin wrappers around Python CLIs via `lib/run-python-cli.sh`
- Framework CLIs: `src/media_stack/cli/commands/*_main.py`
- App CLIs: `src/media_stack/services/apps/<app>/cli/*_main.py`
- The controller calls Python modules directly (not shell scripts)
- Shell scripts exist for operators to run manually

## Pluggable Runtime Contract

- Technology registration is per-service YAML-driven (`contracts/services/*.yaml`)
- Shared orchestration scripts remain technology-neutral
- Runtime hook overrides are in per-service YAML plugin sections
- Pipeline phases are in `contracts/adapter-hooks.k8s.yaml` (K8s only)
