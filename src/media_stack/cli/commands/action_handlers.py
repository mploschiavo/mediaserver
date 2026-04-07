"""Action handler functions for the controller dispatch loop.

Each function handles a specific action triggered via POST /actions/{name}.
Extracted from controller_main.py for maintainability.
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from media_stack.services import runtime_platform


def action_bootstrap(args: argparse.Namespace, state: object,
                     run_preflights: Any, persist_keys: Any, build_runner: Any) -> None:
    """Core bootstrap: preflights + configure arr apps + download clients."""
    run_preflights_enabled = os.environ.get("BOOTSTRAP_RUN_PREFLIGHTS", "1") == "1"
    if run_preflights_enabled:
        run_preflights(state, args)
        persist_keys(state)

    runner, runtime_state = build_runner(args)
    runner.run(runtime_state)
    runtime_platform.log("[OK] Bootstrap completed successfully")


def action_finalize(args: argparse.Namespace, state: object,
                    build_runner: Any, run_post_bootstrap: Any) -> None:
    """Deferred post-bootstrap: Jellyfin tuning, disk guardrails, hygiene, app restarts."""
    runner, runtime_state = build_runner(args)
    try:
        runner._run_post_servarr_steps(runtime_state)
    except Exception as exc:
        runtime_platform.log(f"[WARN] Finalize post-servarr: {exc}")

    run_post_bootstrap(state, args)
    runtime_platform.log("[OK] Finalize completed")


def action_auto_indexers(args: argparse.Namespace, build_runner: Any) -> None:
    """Run Prowlarr auto-indexer discovery (indexer phase only)."""
    runtime_platform.log("[INFO] Auto-indexer: building runner with auto_prowlarr_indexers=True")
    runner, runtime_state = build_runner(args, auto_prowlarr_indexers=True)
    try:
        runner._run_runner_plan_phase(runtime_state, "indexer_steps")
    except Exception:
        runtime_platform.log("[WARN] indexer_steps phase not available, running full pipeline")
        runner.run(runtime_state)
    runtime_platform.log("[OK] Auto-indexer discovery complete")


def action_restart_apps(args: argparse.Namespace, state: object,
                        load_handler_specs: Any, run_handler_specs: Any) -> None:
    """Restart all apps to pick up config changes."""
    specs = load_handler_specs("container_post_setup_handlers")
    restart_specs = [s for s in specs if s.get("name") == "restart_apps"]
    if restart_specs:
        run_handler_specs(restart_specs, state, args, phase_label="RESTART")
    else:
        runtime_platform.log("[WARN] No restart_apps handler found in config")


def action_sync_indexers(args: argparse.Namespace, build_runner: Any) -> None:
    """Trigger Prowlarr ApplicationIndexerSync."""
    runner, runtime_state = build_runner(args)
    try:
        runner._run_runner_plan_phase(runtime_state, "indexer_steps")
    except Exception as exc:
        runtime_platform.log(f"[WARN] Sync indexers: {exc}")
    runtime_platform.log("[OK] Indexer sync complete")


def action_envoy_config(args: argparse.Namespace) -> None:
    """Regenerate Envoy routing config from profile and bootstrap config."""
    runtime_platform.log("[INFO] Generating Envoy config")
    try:
        from media_stack.cli.commands.generate_envoy_config_main import main as gen_main
        gen_main()
    except SystemExit:
        pass
    runtime_platform.log("[OK] Envoy config written")

    # Restart envoy to pick up new config
    try:
        namespace = os.environ.get("K8S_NAMESPACE", "")
        if namespace:
            from kubernetes import client as k8s_client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
            v1 = k8s_client.CoreV1Api()
            pods = v1.list_namespaced_pod(namespace, label_selector="app=envoy")
            for pod in pods.items:
                v1.delete_namespaced_pod(name=pod.metadata.name, namespace=namespace)
            runtime_platform.log("[OK] Envoy pod restarted (K8s)")
        else:
            import docker
            client = docker.from_env()
            try:
                envoy = client.containers.get("envoy")
                envoy.restart(timeout=10)
                runtime_platform.log("[OK] Envoy container restarted (Docker)")
            except Exception:
                runtime_platform.log("[WARN] Envoy container not found, skipping restart")
    except Exception as exc:
        runtime_platform.log(f"[WARN] Envoy restart skipped: {exc}")


def action_reconcile(args: argparse.Namespace, build_runner: Any) -> None:
    """Re-run the full bootstrap pipeline to fix drift."""
    runtime_platform.log("[INFO] Running reconcile (full pipeline)")
    runner, runtime_state = build_runner(args)
    runner.run(runtime_state)
    runtime_platform.log("[OK] Reconcile complete")
