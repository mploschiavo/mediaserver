#!/usr/bin/env python3
"""Reconcile media-stack manifests and rollout deployments."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from core.exceptions import ConfigError, MediaStackError

from cli.cli_common import kube_cmd, repo_root_from_script_file, run_command


@dataclass(frozen=True)
class Microk8sReconcileConfig:
    namespace: str
    wait_timeout: str
    include_optional: bool
    root_dir: Path


def parse_config(argv: list[str] | None = None) -> Microk8sReconcileConfig:
    parser = argparse.ArgumentParser(
        prog="scripts/microk8s-reconcile.sh",
        description=(
            "Reconcile media-stack manifests on MicroK8s/Kubernetes and restart deployments."
        ),
    )
    parser.add_argument("--include-optional", action="store_true", default=False)
    args = parser.parse_args(argv)
    return Microk8sReconcileConfig(
        namespace=os.environ.get("NAMESPACE", "media-stack").strip() or "media-stack",
        wait_timeout=os.environ.get("WAIT_TIMEOUT", "20m").strip() or "20m",
        include_optional=bool(args.include_optional),
        root_dir=repo_root_from_script_file(__file__),
    )


def _get_optional_deployments(kubectl: list[str], namespace: str) -> list[str]:
    proc = run_command(
        [*kubectl, "-n", namespace, "get", "deploy", "-o", "name"],
        check=False,
    )
    if proc.returncode != 0:
        return []
    names: list[str] = []
    for row in (proc.stdout or "").splitlines():
        token = row.strip()
        if not token:
            continue
        short = token.removeprefix("deploy/")
        if short in {"homepage", "plex", "tautulli", "sabnzbd", "flaresolverr"}:
            names.append(short)
    return names


def run(cfg: Microk8sReconcileConfig) -> int:
    kubectl = kube_cmd()
    k8s_dir = cfg.root_dir / "k8s"
    if not k8s_dir.is_dir():
        raise ConfigError(f"k8s directory not found: {k8s_dir}")

    print(f"[INFO] Applying core manifests from {k8s_dir}")
    run_command([*kubectl, "apply", "-k", str(k8s_dir)])

    optional_path = k8s_dir / "optional.yaml"
    if cfg.include_optional:
        print(f"[INFO] Applying optional manifests from {optional_path}")
        run_command([*kubectl, "apply", "-f", str(optional_path)])
    else:
        existing_optional = _get_optional_deployments(kubectl, cfg.namespace)
        if existing_optional:
            print("[INFO] Existing optional deployments detected; applying optional.yaml.")
            run_command([*kubectl, "apply", "-f", str(optional_path)])

    unpackerr_path = k8s_dir / "unpackerr.yaml"
    unpackerr_probe = run_command(
        [*kubectl, "-n", cfg.namespace, "get", "deploy", "unpackerr"],
        check=False,
    )
    if unpackerr_probe.returncode == 0 and unpackerr_path.is_file():
        print("[INFO] Applying unpackerr manifest (replicas default to 0 in repo).")
        run_command([*kubectl, "apply", "-f", str(unpackerr_path)])

    print(f"[INFO] Restarting all deployments in namespace {cfg.namespace}")
    run_command([*kubectl, "-n", cfg.namespace, "rollout", "restart", "deploy", "--all"])

    deploy_proc = run_command(
        [
            *kubectl,
            "-n",
            cfg.namespace,
            "get",
            "deploy",
            "-o",
            "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}",
        ],
        check=False,
    )
    deploys = [line.strip() for line in (deploy_proc.stdout or "").splitlines() if line.strip()]
    failed = 0
    for deploy in deploys:
        print(f"[INFO] Waiting for deploy/{deploy}")
        status_proc = run_command(
            [
                *kubectl,
                "-n",
                cfg.namespace,
                "rollout",
                "status",
                f"deploy/{deploy}",
                f"--timeout={cfg.wait_timeout}",
            ],
            check=False,
        )
        sys.stdout.write(status_proc.stdout or "")
        sys.stderr.write(status_proc.stderr or "")
        if status_proc.returncode != 0:
            print(
                f"[WARN] deploy/{deploy} did not become ready in {cfg.wait_timeout}",
                file=sys.stderr,
            )
            failed += 1

    print("\n[INFO] Current pod state:")
    pods_proc = run_command([*kubectl, "-n", cfg.namespace, "get", "pods"], check=False)
    sys.stdout.write(pods_proc.stdout or "")
    sys.stderr.write(pods_proc.stderr or "")

    if failed > 0:
        print(f"\n[WARN] {failed} deployment(s) still not ready.", file=sys.stderr)
        joined = " ".join(kubectl)
        print("[WARN] Inspect with:", file=sys.stderr)
        print(
            f"  {joined} -n {cfg.namespace} get events --sort-by=.lastTimestamp | tail -n 200",
            file=sys.stderr,
        )
        print(
            f"  {joined} -n {cfg.namespace} logs deploy/<name> --tail=200",
            file=sys.stderr,
        )
        return 1

    print("\n[OK] Reconcile complete.")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parse_config(argv))
    except (ConfigError, MediaStackError, OSError, ValueError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

