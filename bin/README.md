# Scripts Directory

## Structure

```
bin/
  bootstrap-all.sh         # Operator one-liner: full bootstrap pipeline (shim → media-stack-bootstrap-all)
  run-bootstrap-job.sh     # Operator one-liner: single bootstrap job (shim → media-stack-run-job)
  reset-admin.sh           # Operator one-liner: reset admin credential
  with-env.sh              # Operator one-liner: load env file + run command
  sync-etc-hosts.sh        # Operator one-liner: sync /etc/hosts to running Envoy vhosts

  release/                 # Release pipeline (versioning, signing, dist regen)
    release.sh
    regen-dist.sh
    sign-image.sh
    verify-image.sh
    generate-sbom.sh

  build/                   # Container image builders
    build-controller-image.sh
    build-ui-image.sh

  install/                 # Pre-deploy + deploy entry points
    install.sh
    deploy-stack.sh

  test/                    # Test runners + verification probes
    test.sh
    verify-fresh-install.sh
    verify-stack.sh
    deploy-verify.sh
    fast-first-run.sh
    microk8s-smoke-test.sh
    run-api-e2e.sh
    run-integration-test.sh
    run-playwright-screenshots.sh
    run-playwright-smoke.sh
    verify-flow.sh
    watch-install.sh

  ops/                     # Operations / docs / hotfix scripts
    generate-reference-docs.sh
    lychee.sh
    hotfix_envoy_ext_authz_headers.py
    hotfix_envoy_ext_authz_recover.py
    hotfix_envoy_x_original_url.py
    recapture-all-fixtures.sh
    gen-fixture-codegen-validation.py

  utils/                   # Operator utilities
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

  debug/                   # Per-service debugging
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

  k8s/                     # K8s-specific
    microk8s-reconcile.sh
    microk8s-patch-ingress-class.sh

  docs/                    # Documentation
    render-architecture-diagrams.sh

  lib/                     # Shared shim helpers (LOAD-BEARING)
    run-python-cli.sh      # Python CLI resolver (used by all wrappers)
```

## Design Rules

- Shell scripts are thin wrappers around Python CLIs via `lib/run-python-cli.sh`
- Framework CLIs: `src/media_stack/cli/commands/*_main.py`
- App CLIs: `src/media_stack/services/apps/<app>/cli/*_main.py`
- The controller calls Python modules directly (not shell scripts)
- Shell scripts exist for operators to run manually
- ADR-0001 Phase 13-D groups scripts by concern (release/, build/, install/, test/, ops/)
  rather than leaving everything flat under bin/

## Pluggable Runtime Contract

- Technology registration is per-service YAML-driven (`contracts/services/*.yaml`)
- Shared orchestration scripts remain technology-neutral
- Runtime hook overrides are in per-service YAML plugin sections
- Pipeline phases are in `contracts/adapter-hooks.k8s.yaml` (K8s only)
