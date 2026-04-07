#!/usr/bin/env python3
"""Python CLI for one-command install orchestration.

Media Automation Stack by Matthew Loschiavo:
https://matthewloschiavo.com
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib import request

from media_stack.cli.workflows.cli_common import repo_root_from_script_file
from media_stack.core.phase_tracker import PhaseTracker
from media_stack.core.platforms.kubernetes.kube_client import resolve_kubectl_binary


class InstallError(RuntimeError):
    """Raised when install orchestration fails."""


class SkipPhase(RuntimeError):
    """Signal that current phase should be marked as skipped."""


def ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def info(message: str) -> None:
    print(f"[{ts()}] [INFO] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[{ts()}] [WARN] {message}", file=sys.stderr, flush=True)


def err(message: str) -> None:
    print(f"[{ts()}] [ERR] {message}", file=sys.stderr, flush=True)


@dataclass
class InstallConfig:
    root_dir: Path
    profile: str = "full"
    node_ip: str = ""
    namespace: str = "media-stack"
    prepare_host_root: str = "/srv/media-stack"
    storage_mode: str = "dynamic-pvc"
    pvc_storage_class: str = ""
    ingress_domain: str = "local"
    enable_tls: str = "0"
    enable_secrets_gen: str = "1"
    alert_webhook_url: str = ""


@dataclass
class InstallRunner:
    cfg: InstallConfig
    kubectl: list[str]
    tracker: PhaseTracker = field(default_factory=lambda: PhaseTracker(info=info, warn=warn))

    def run(self) -> int:
        self._validate_inputs()
        self._detect_node_ip_if_needed()

        info("Install start")
        info(f"Profile: {self.cfg.profile}")
        info(f"Namespace: {self.cfg.namespace}")
        info(f"Storage mode: {self.cfg.storage_mode}")
        if self.cfg.pvc_storage_class:
            info(f"PVC storage class override: {self.cfg.pvc_storage_class}")
        else:
            info("PVC storage class override: <cluster default>")
        info(f"Ingress domain: {self.cfg.ingress_domain}")
        info(f"Node IP: {self.cfg.node_ip}")

        self.notify("info", f"media-stack install started (profile={self.cfg.profile})")

        self._run_phase("Preflight checks", self.preflight_checks)
        self._run_phase("Prepare host directories", self.prepare_host_directories)
        self._run_phase(
            "Apply scale policy guardrails (dry-run)", self.apply_scale_policy_guardrails_dry_run
        )
        self._run_phase("Deploy and bootstrap stack", self.deploy_and_bootstrap)

        if self.cfg.enable_tls == "1":
            self._run_phase("Configure LAN TLS", self.configure_lan_tls)
        else:
            self._run_phase("Configure LAN TLS", lambda: None, enabled=False)

        self._run_phase("Collect final stack status", self.collect_final_stack_status)
        self.tracker.summary()

        print("\n[OK] Install complete.")
        print("[INFO] Primary URLs:")
        print(f"  http://homepage.{self.cfg.ingress_domain}")
        print(f"  http://jellyfin.{self.cfg.ingress_domain}")
        print(f"  http://jellyseerr.{self.cfg.ingress_domain}")
        print(f"  http://maintainerr.{self.cfg.ingress_domain}")
        print("[INFO] Host entries helper:")
        print(f"  bash bin/render-hosts-example.sh {self.cfg.node_ip} {self.cfg.namespace}")
        print("[INFO] Generated secrets file:")
        print(f"  {self.cfg.root_dir / 'secrets.generated.env'}")

        self.notify("ok", f"media-stack install succeeded (profile={self.cfg.profile})")
        return 0

    def _run_phase(self, name: str, fn: Callable[[], None], *, enabled: bool = True) -> None:
        self.tracker.start(name)
        if not enabled:
            self.tracker.end("skipped")
            return
        try:
            fn()
            self.tracker.end("ok")
        except SkipPhase:
            self.tracker.end("skipped")
        except Exception:
            self.tracker.end("failed")
            raise

    def _validate_inputs(self) -> None:
        if self.cfg.profile not in {"minimal", "full", "public-demo", "power-user"}:
            raise InstallError(
                f"Unsupported profile '{self.cfg.profile}'. Use minimal|full|public-demo|power-user."
            )
        if self.cfg.storage_mode != "dynamic-pvc":
            raise InstallError(
                f"Unsupported storage mode '{self.cfg.storage_mode}'. "
                "legacy-hostpath was removed; use dynamic-pvc."
            )
        if not self.cfg.namespace.strip():
            raise InstallError("Namespace cannot be empty.")
        self.cfg.ingress_domain = self.cfg.ingress_domain.lstrip(".").strip()
        if not self.cfg.ingress_domain:
            raise InstallError("Ingress domain cannot be empty.")
        profile_dir = self.cfg.root_dir / "k8s" / "profiles" / self.cfg.profile
        if not profile_dir.is_dir():
            raise InstallError(f"Missing profile directory: {profile_dir}")

    def _detect_node_ip_if_needed(self) -> None:
        if self.cfg.node_ip.strip():
            self.cfg.node_ip = self.cfg.node_ip.strip()
            return
        probe = subprocess.run(
            ["bash", "-lc", "hostname -I | awk '{print $1}'"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.cfg.node_ip = (probe.stdout or "").strip()
        if not self.cfg.node_ip:
            raise InstallError("Could not detect node IP. Pass --node-ip.")

    def _run_script(
        self,
        script_name: str,
        *args: str,
        env: dict[str, str] | None = None,
        ignore_failure: bool = False,
    ) -> None:
        script_path = self.cfg.root_dir / "bin" / script_name
        merged_env = dict(os.environ)
        if env:
            merged_env.update({k: str(v) for k, v in env.items()})
        proc = subprocess.run(
            ["bash", str(script_path), *args],
            cwd=str(self.cfg.root_dir),
            env=merged_env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.stdout.strip():
            print(proc.stdout.rstrip())
        if proc.stderr.strip():
            print(proc.stderr.rstrip(), file=sys.stderr)
        if proc.returncode != 0 and not ignore_failure:
            raise InstallError(
                f"{script_name} failed ({proc.returncode}): "
                f"{' '.join(shlex.quote(x) for x in [str(script_path), *args])}"
            )

    def notify(self, status: str, message: str) -> None:
        if not self.cfg.alert_webhook_url:
            return
        payload = json.dumps({"status": status, "message": message}).encode("utf-8")
        req = request.Request(
            self.cfg.alert_webhook_url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=8):
                return
        except Exception:
            return

    def preflight_checks(self) -> None:
        info("Preflight: checking ingress classes")
        proc = subprocess.run(
            [*self.kubectl, "get", "ingressclass"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            warn(
                "No ingress classes returned yet. "
                "Install may still succeed after ingress add-on is enabled."
            )

    def prepare_host_directories(self) -> None:
        info("Skipping host directory prep (dynamic PVC mode only).")
        raise SkipPhase()

    def apply_scale_policy_guardrails_dry_run(self) -> None:
        info("Applying scale policy guardrails")
        self._run_script(
            "apply-scale-policy.sh",
            "--dry-run",
            env={"NAMESPACE": self.cfg.namespace},
            ignore_failure=True,
        )

    def deploy_and_bootstrap(self) -> None:
        info("Deploying and bootstrapping stack")
        self._run_script(
            "deploy-stack.sh",
            self.cfg.node_ip,
            env={
                "PROFILE": self.cfg.profile,
                "NAMESPACE": self.cfg.namespace,
                "NODE_IP": self.cfg.node_ip,
                "ALERT_WEBHOOK_URL": self.cfg.alert_webhook_url,
                "STORAGE_MODE": self.cfg.storage_mode,
                "PVC_STORAGE_CLASS": self.cfg.pvc_storage_class,
                "PREPARE_HOST_ROOT": self.cfg.prepare_host_root,
                "INGRESS_DOMAIN": self.cfg.ingress_domain,
                "GENERATE_SECRETS_ON_REBUILD": self.cfg.enable_secrets_gen,
            },
        )

    def configure_lan_tls(self) -> None:
        info("Setting up LAN TLS certificates")
        self._run_script(
            "setup-lan-tls.sh",
            env={
                "NAMESPACE": self.cfg.namespace,
                "NODE_IP": self.cfg.node_ip,
            },
        )

    def collect_final_stack_status(self) -> None:
        info("Collecting final status")
        self._run_script("stack-status.sh", env={"NAMESPACE": self.cfg.namespace})


def parse_args(argv: list[str]) -> InstallConfig:
    root_dir = repo_root_from_script_file(__file__)

    parser = argparse.ArgumentParser(
        prog="bin/install.sh",
        description="One-command install wizard for media-stack.",
    )
    parser.add_argument("--profile", default=os.environ.get("PROFILE", "full"))
    parser.add_argument("--namespace", default=os.environ.get("NAMESPACE", "media-stack"))
    parser.add_argument("--storage-mode", default=os.environ.get("STORAGE_MODE", "dynamic-pvc"))
    parser.add_argument("--storage-class", default=os.environ.get("PVC_STORAGE_CLASS", ""))
    parser.add_argument("--ingress-domain", default=os.environ.get("INGRESS_DOMAIN", "local"))
    parser.add_argument("--node-ip", default=os.environ.get("NODE_IP", ""))
    parser.add_argument("--enable-tls", action="store_true")
    parsed = parser.parse_args(argv)

    enable_tls = os.environ.get("ENABLE_TLS", "0")
    if parsed.enable_tls:
        enable_tls = "1"

    return InstallConfig(
        root_dir=root_dir,
        profile=parsed.profile,
        node_ip=parsed.node_ip,
        namespace=parsed.namespace,
        prepare_host_root=os.environ.get("PREPARE_HOST_ROOT", "/srv/media-stack"),
        storage_mode=parsed.storage_mode,
        pvc_storage_class=parsed.storage_class,
        ingress_domain=parsed.ingress_domain,
        enable_tls=enable_tls,
        enable_secrets_gen=os.environ.get("ENABLE_SECRETS_GEN", "1"),
        alert_webhook_url=os.environ.get("ALERT_WEBHOOK_URL", ""),
    )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    cfg = parse_args(args)

    try:
        kubectl = resolve_kubectl_binary()
    except Exception as exc:
        err(str(exc))
        return 2

    runner = InstallRunner(cfg=cfg, kubectl=kubectl)
    try:
        return runner.run()
    except Exception as exc:
        warn(f"Install failed: {exc}")
        runner.tracker.summary()
        runner.notify(
            "error",
            f"media-stack install failed (profile={cfg.profile}, namespace={cfg.namespace})",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
