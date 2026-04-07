#!/usr/bin/env python3
"""Cross-platform deploy script for Media Automation Stack.

Works on Linux, macOS, and Windows. Requires Python 3.11+ and either
kubectl (for Kubernetes) or docker compose (for Compose).

Usage:
    python deploy.py k8s                                         # K8s default profile
    python deploy.py k8s examples/bootstrap-profiles/media-k8s-standard.yaml
    python deploy.py k8s --delete                                # teardown + redeploy
    python deploy.py compose                                     # Docker Compose
    python deploy.py compose --delete                            # teardown + redeploy
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BOOTSTRAP_PORT = int(os.environ.get("BOOTSTRAP_API_PORT", "9100"))


def run(cmd: list[str], *, check: bool = True, capture: bool = False, **kwargs) -> subprocess.CompletedProcess:
    """Run a command, printing it for visibility."""
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=capture, text=True, **kwargs)


def run_quiet(cmd: list[str]) -> str:
    """Run a command and return stdout, empty string on failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def yaml_val(path: Path, key: str) -> str:
    """Extract a simple scalar from a YAML file without requiring PyYAML."""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}:"):
            val = stripped.split(":", 1)[1].strip().strip("\"'")
            return val
    return ""


# ---------------------------------------------------------------------------
# Kubernetes deploy
# ---------------------------------------------------------------------------

def deploy_k8s(args: argparse.Namespace) -> None:
    profile_path = SCRIPT_DIR / args.profile
    if not profile_path.exists():
        sys.exit(f"ERROR: Profile not found: {profile_path}")

    namespace = yaml_val(profile_path, "name")
    install_profile = yaml_val(profile_path, "install_profile") or "standard"
    if not namespace:
        sys.exit("ERROR: metadata.name is required in profile")

    profile_dir = SCRIPT_DIR / "k8s" / "profiles" / install_profile
    if not profile_dir.exists():
        sys.exit(f"ERROR: K8s profile dir not found: {profile_dir}")

    print(f"K8s deploy: namespace={namespace} profile={install_profile}")

    # Handle --delete
    if args.delete:
        print(f"  Deleting namespace {namespace}...")
        run(["kubectl", "delete", "namespace", namespace, "--force", "--grace-period=0"], check=False)
        for _ in range(24):
            out = run_quiet(["kubectl", "get", "ns", namespace])
            if "NotFound" in out or not out:
                break
            time.sleep(5)

    # Create namespace
    run(["kubectl", "create", "namespace", namespace], check=False)

    # Apply manifests via kustomize with namespace override via sed-like replacement
    print("  Applying manifests...")
    kustomize = subprocess.run(
        ["kubectl", "kustomize", str(profile_dir), "--load-restrictor", "LoadRestrictionsNone"],
        capture_output=True, text=True, check=True,
    )
    manifests = kustomize.stdout.replace("namespace: media-stack", f"namespace: {namespace}")
    result = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=manifests, capture_output=True, text=True,
    )
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)

    # Create ConfigMaps
    print("  Creating ConfigMaps...")
    config_json = SCRIPT_DIR / "contracts" / "media-stack.config.json"
    cm_sources = [
        ("media-stack-controller-profile", "profile.yaml", profile_path),
    ]
    # Only include config.json ConfigMap if the file exists (optional now)
    if config_json.is_file():
        cm_sources.insert(0, ("media-stack-controller-config", "config.json", config_json))

    for cm_name, flag, src in cm_sources:
        dry = subprocess.run(
            ["kubectl", "-n", namespace, "create", "configmap", cm_name,
             f"--from-file={flag}={src}", "--dry-run=client", "-o", "yaml"],
            capture_output=True, text=True, check=True,
        )
        subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=dry.stdout, capture_output=True, text=True,
        )
    print("  ConfigMaps created.")

    # Wait for pods
    print("  Waiting for pods...")
    for i in range(30):
        out = run_quiet(["kubectl", "-n", namespace, "get", "pods", "--no-headers"])
        lines = [l for l in out.splitlines() if l.strip()] if out else []
        ready = sum(1 for l in lines if "1/1" in l)
        total = len(lines)
        if ready >= 10:
            print(f"  Pods: {ready}/{total} ready")
            break
        if i == 29:
            print(f"  WARN: Only {ready}/{total} pods ready after timeout")
        time.sleep(10)

    # Wait for controller service
    print("  Waiting for controller service...")
    pod = ""
    for _ in range(40):
        pod = run_quiet([
            "kubectl", "-n", namespace, "get", "pods",
            "-l", "app=media-stack-controller",
            "-o", "jsonpath={.items[0].metadata.name}",
        ])
        if pod:
            health = run_quiet([
                "kubectl", "-n", namespace, "exec", pod, "--",
                "wget", "-qO-", "http://127.0.0.1:9100/healthz",
            ])
            if health:
                break
            pod = ""
        time.sleep(3)

    if not pod:
        sys.exit("  ERROR: Controller service pod not found within 120s")

    # Trigger bootstrap
    print(f"  Triggering bootstrap on pod {pod}...")
    run([
        "kubectl", "-n", namespace, "exec", pod, "--",
        "wget", "-qO-", "--post-data={}", "--header=Content-Type: application/json",
        "http://127.0.0.1:9100/actions/bootstrap",
    ], check=False)

    # Poll status
    print("  Polling bootstrap status...")
    for _ in range(60):
        raw = run_quiet([
            "kubectl", "-n", namespace, "exec", pod, "--",
            "wget", "-qO-", "http://127.0.0.1:9100/status",
        ])
        if raw:
            try:
                phase = json.loads(raw).get("phase", "")
            except (json.JSONDecodeError, ValueError):
                phase = ""
            if phase == "complete":
                print("  Bootstrap: complete")
                break
            if phase == "error":
                print("  Bootstrap: error (check logs)")
                break
        time.sleep(10)

    print()
    print(f"Deploy complete: {namespace}")
    print(f"  Dashboard: http://apps.{namespace}.local:30180/app/media-stack-controller/")
    print(f"  Homepage:  http://apps.{namespace}.local:30180/app/homepage")


# ---------------------------------------------------------------------------
# Docker Compose deploy
# ---------------------------------------------------------------------------

def deploy_compose(args: argparse.Namespace) -> None:
    compose_file = SCRIPT_DIR / "docker" / "docker-compose.yml"

    if args.delete:
        print("Tearing down compose stack...")
        run(["docker", "compose", "-f", str(compose_file), "down", "-v", "--remove-orphans"], check=False)

    print("Compose deploy: starting services...")
    run(["docker", "compose", "-f", str(compose_file), "up", "-d"])

    # Wait for controller service
    print("  Waiting for controller service...")
    health = ""
    for _ in range(40):
        health = run_quiet(["curl", "-sf", f"http://127.0.0.1:{BOOTSTRAP_PORT}/healthz"])
        if not health:
            # Fallback for Windows (no curl): try Python urllib
            try:
                import urllib.request
                with urllib.request.urlopen(f"http://127.0.0.1:{BOOTSTRAP_PORT}/healthz", timeout=5) as resp:
                    health = resp.read().decode()
            except Exception:
                health = ""
        if health:
            break
        time.sleep(3)

    if not health:
        sys.exit(f"  ERROR: Controller service not responding on port {BOOTSTRAP_PORT} within 120s")
    print("  Controller service ready.")

    # Trigger bootstrap
    print("  Triggering bootstrap...")
    try:
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{BOOTSTRAP_PORT}/actions/bootstrap",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass

    # Poll status
    print("  Polling bootstrap status...")
    for _ in range(60):
        try:
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{BOOTSTRAP_PORT}/status", timeout=5) as resp:
                data = json.loads(resp.read().decode())
                phase = data.get("phase", "")
        except Exception:
            phase = ""
        if phase == "complete":
            print("  Bootstrap: complete")
            break
        if phase == "error":
            print("  Bootstrap: error (check dashboard)")
            break
        time.sleep(10)

    print()
    print("Deploy complete.")
    print(f"  Dashboard: http://127.0.0.1:{BOOTSTRAP_PORT}/")
    print(f"  Homepage:  http://127.0.0.1:80/app/homepage (via Envoy)")
    print(f"  Trigger:   curl -X POST http://127.0.0.1:{BOOTSTRAP_PORT}/actions/bootstrap")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy Media Automation Stack (cross-platform)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="target", required=True)

    k8s = sub.add_parser("k8s", help="Deploy to Kubernetes")
    k8s.add_argument("profile", nargs="?", default="examples/bootstrap-profiles/media-k8s-standard.yaml",
                      help="Path to bootstrap profile YAML")
    k8s.add_argument("--delete", action="store_true", help="Teardown namespace before deploy")

    compose = sub.add_parser("compose", help="Deploy with Docker Compose")
    compose.add_argument("--delete", action="store_true", help="Teardown stack before deploy")

    args = parser.parse_args()
    if args.target == "k8s":
        deploy_k8s(args)
    elif args.target == "compose":
        deploy_compose(args)


if __name__ == "__main__":
    main()
