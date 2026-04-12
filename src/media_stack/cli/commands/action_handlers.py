"""Action handler functions for the controller dispatch loop.

Each function handles a specific action triggered via POST /actions/{name}.
Extracted from controller_main.py for maintainability.
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from media_stack.services import runtime_platform


class ActionHandlerService:
    """Wraps all action handler functions."""

    def action_bootstrap(self, args: argparse.Namespace, state: object,
                         run_preflights: Any, persist_keys: Any, build_runner: Any) -> None:
        """Core bootstrap: preflights + configure arr apps + download clients."""
        run_preflights_enabled = os.environ.get("BOOTSTRAP_RUN_PREFLIGHTS", "1") == "1"
        runtime_platform.log(f"[DEBUG] Bootstrap: preflights={'enabled' if run_preflights_enabled else 'disabled'}, "
                             f"config={getattr(args, 'config', '?')}, "
                             f"config_root={getattr(args, 'config_root', '?')}")
        if run_preflights_enabled:
            run_preflights(state, args)
            persist_keys(state)

        runtime_platform.log("[DEBUG] Bootstrap: building runner...")
        runner, runtime_state = build_runner(args)
        runtime_platform.log(f"[DEBUG] Bootstrap: runner built, plan phases={list(getattr(runtime_state, 'plan', {}).keys()) if hasattr(runtime_state, 'plan') else '?'}")
        runner.run(runtime_state)
        runtime_platform.log("[OK] Bootstrap completed successfully")

    def action_post_setup(self, args: argparse.Namespace, state: object,
                        build_runner: Any, run_post_bootstrap: Any) -> None:
        """Deferred post-bootstrap: media-server tuning, disk guardrails, hygiene, app restarts."""
        runtime_platform.log("[DEBUG] Post-setup: building runner...")
        runner, runtime_state = build_runner(args)
        runtime_platform.log("[DEBUG] Post-setup: running post-servarr steps...")
        try:
            runner._run_post_servarr_steps(runtime_state)
        except Exception as exc:
            runtime_platform.log(f"[WARN] Finalize post-servarr: {exc}")
            import traceback
            runtime_platform.log(f"[DEBUG] Post-setup traceback: {traceback.format_exc()}")

        runtime_platform.log("[DEBUG] Post-setup: running post-bootstrap handlers...")
        run_post_bootstrap(state, args)
        runtime_platform.log("[OK] Finalize completed")

    def action_discover_indexers(self, args: argparse.Namespace, build_runner: Any) -> None:
        """Run auto-indexer discovery (indexer phase only)."""
        runtime_platform.log("[INFO] Auto-indexer: building runner with auto_prowlarr_indexers=True")
        runner, runtime_state = build_runner(args, auto_prowlarr_indexers=True)
        try:
            runner._run_runner_plan_phase(runtime_state, "indexer_steps")
        except Exception:
            runtime_platform.log("[WARN] indexer_steps phase not available, running full pipeline")
            runner.run(runtime_state)
        runtime_platform.log("[OK] Auto-indexer discovery complete")

    def action_restart_apps(self, args: argparse.Namespace, state: object,
                            load_handler_specs: Any, run_handler_specs: Any) -> None:
        """Restart all apps to pick up config changes."""
        specs = load_handler_specs("container_post_setup_handlers")
        restart_specs = [s for s in specs if s.get("name") == "restart_apps"]
        if restart_specs:
            run_handler_specs(restart_specs, state, args, phase_label="RESTART")
        else:
            runtime_platform.log("[WARN] No restart_apps handler found in config")

    def action_push_indexers(self, args: argparse.Namespace, build_runner: Any) -> None:
        """Trigger indexer-manager ApplicationIndexerSync."""
        runner, runtime_state = build_runner(args)
        try:
            runner._run_runner_plan_phase(runtime_state, "indexer_steps")
        except Exception as exc:
            runtime_platform.log(f"[WARN] Sync indexers: {exc}")
        runtime_platform.log("[OK] Indexer sync complete")

    def action_envoy_config(self, args: argparse.Namespace) -> None:
        """Regenerate Envoy routing config from profile and bootstrap config."""
        runtime_platform.log("[INFO] Generating Envoy config")

        # Ensure CONFIG_ROOT is set — default to /srv-config for Docker containers.
        if not os.environ.get("CONFIG_ROOT"):
            os.environ["CONFIG_ROOT"] = "/srv-config"

        try:
            from media_stack.cli.commands.generate_envoy_config_main import main as gen_main
            gen_main()
        except SystemExit as exc:
            if exc.code:
                runtime_platform.log(f"[ERROR] Envoy config generation failed (exit {exc.code})")
                return
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

    def action_validate_credentials(self) -> None:
        """Probe admin credentials and auto-sync passwords for services that fail."""
        from media_stack.api.services.health import probe_credentials

        runtime_platform.log("[INFO] Validating admin credentials against running services")
        result = probe_credentials()
        creds = result.get("credentials", {})
        ok_count = result.get("ok", 0)
        total = result.get("total", 0)

        failed_svcs = []
        for svc, status in sorted(creds.items()):
            if status == "ok":
                runtime_platform.log(f"[CRED] {svc}: passed")
            elif status == "disabled":
                runtime_platform.log(f"[CRED] {svc}: auth disabled — syncing password to enable")
                failed_svcs.append(svc)
            elif status == "error":
                runtime_platform.log(f"[WARN] {svc}: unreachable (service may still be starting)")
            else:
                runtime_platform.log(f"[WARN] {svc}: credential check failed ({status}) — will attempt password sync")
                failed_svcs.append(svc)

        # Auto-sync: push stack admin password to services that failed or have auth disabled
        if failed_svcs:
            import os
            from media_stack.api.services.admin import reset_password
            stack_pass = os.environ.get("STACK_ADMIN_PASSWORD", "media-stack")
            runtime_platform.log(f"[INFO] Syncing credentials to {len(failed_svcs)} service(s): {', '.join(failed_svcs)}")
            sync_result = reset_password(stack_pass, target_services=failed_svcs)
            for svc_id in sync_result.get("services", []):
                runtime_platform.log(f"[OK] {svc_id}: password synced")
            for err in sync_result.get("errors", []):
                runtime_platform.log(f"[WARN] {err}")
            # Re-validate after sync
            recheck = probe_credentials(services=failed_svcs)
            ok_after = sum(1 for v in recheck.get("credentials", {}).values() if v == "ok")
            if ok_after > 0:
                runtime_platform.log(f"[OK] {ok_after} service(s) now passing after credential sync")
            ok_count += ok_after

        if total == 0:
            runtime_platform.log("[INFO] No services with login_mode configured — nothing to validate")
        elif ok_count >= total:
            runtime_platform.log(f"[OK] All {total} credential checks passed")
        else:
            failed = total - ok_count
            runtime_platform.log(
                f"[WARN] {failed}/{total} credential checks did not pass — "
                "review STACK_ADMIN_USERNAME / STACK_ADMIN_PASSWORD or service setup"
            )

    def action_configure_livetv(self, args: argparse.Namespace, build_runner: Any) -> None:
        """Configure Live TV only — targeted action, not full bootstrap."""
        runtime_platform.log("[INFO] Configuring Live TV (media server livetv phase)")
        runner, runtime_state = build_runner(args)
        try:
            runner._run_runner_plan_phase(runtime_state, "media_server_livetv_steps")
        except Exception:
            # Fallback: try the ensure step directly
            try:
                runner._run_runner_plan_phase(runtime_state, "ensure_steps")
                runtime_platform.log("[OK] Live TV: ran ensure phase (includes livetv)")
            except Exception as exc:
                runtime_platform.log(f"[WARN] Live TV configure: {exc}")
        runtime_platform.log("[OK] Live TV configuration complete")

    def action_reconcile(self, args: argparse.Namespace, build_runner: Any) -> None:
        """Re-run the full bootstrap pipeline to fix drift."""
        runtime_platform.log("[INFO] Running reconcile (full pipeline)")
        runner, runtime_state = build_runner(args)
        runner.run(runtime_state)
        runtime_platform.log("[OK] Reconcile complete")


_instance = ActionHandlerService()
action_bootstrap = _instance.action_bootstrap
action_post_setup = _instance.action_post_setup
action_discover_indexers = _instance.action_discover_indexers
action_restart_apps = _instance.action_restart_apps
action_push_indexers = _instance.action_push_indexers
action_envoy_config = _instance.action_envoy_config
action_validate_credentials = _instance.action_validate_credentials
action_configure_livetv = _instance.action_configure_livetv
action_reconcile = _instance.action_reconcile
