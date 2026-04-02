#!/usr/bin/env python3
"""LAN smoke test for media-stack ingress routing."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

from core.exceptions import ConfigError, MediaStackError

from cli.cli_common import kube_cmd, run_command


@dataclass(frozen=True)
class Microk8sSmokeTestConfig:
    node_ip: str
    namespace: str
    ingress_name: str


def _first_host_ip() -> str:
    try:
        name = socket.gethostname()
        values = socket.gethostbyname_ex(name)[2]
        for value in values:
            if value and not value.startswith("127."):
                return value
    except Exception:
        pass
    return ""


def parse_config(argv: list[str] | None = None) -> Microk8sSmokeTestConfig:
    parser = argparse.ArgumentParser(
        prog="scripts/microk8s-smoke-test.sh",
        description="Quick LAN smoke test for media-stack ingress routing.",
    )
    parser.add_argument("node_ip", nargs="?", default="")
    parser.add_argument("namespace", nargs="?", default="")
    args = parser.parse_args(argv)

    namespace = (
        str(args.namespace or "").strip()
        or os.environ.get("NAMESPACE", "media-stack").strip()
        or "media-stack"
    )
    ingress_name = (
        os.environ.get("INGRESS_NAME", "media-stack-ingress").strip() or "media-stack-ingress"
    )
    node_ip = str(args.node_ip or "").strip() or _first_host_ip()
    if not node_ip:
        raise ConfigError(
            "Unable to detect node IP. Pass it explicitly: scripts/microk8s-smoke-test.sh <NODE_IP>"
        )
    return Microk8sSmokeTestConfig(node_ip=node_ip, namespace=namespace, ingress_name=ingress_name)


def _http_code_with_host(node_ip: str, host: str) -> int:
    req = urllib.request.Request(f"http://{node_ip}/", method="GET", headers={"Host": host})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return int(getattr(resp, "status", 200))
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        return 0


def _ingress_rules(kubectl: list[str], namespace: str, ingress_name: str) -> list[tuple[str, str]]:
    proc = run_command(
        [
            *kubectl,
            "-n",
            namespace,
            "get",
            "ingress",
            ingress_name,
            "-o",
            "jsonpath={range .spec.rules[*]}{.host}{'|'}{.http.paths[0].backend.service.name}{'\\n'}{end}",
        ],
        check=False,
    )
    if proc.returncode != 0:
        return []
    pairs: list[tuple[str, str]] = []
    for line in (proc.stdout or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        if "|" in raw:
            host, svc = raw.split("|", 1)
        else:
            host, svc = raw, ""
        pairs.append((host.strip(), svc.strip()))
    return pairs


def run(cfg: Microk8sSmokeTestConfig) -> int:
    kubectl = kube_cmd()

    print(f"Using kubectl command: {' '.join(kubectl)}")
    print(f"Namespace: {cfg.namespace}")
    print(f"Ingress: {cfg.ingress_name}")
    print(f"Node IP: {cfg.node_ip}\n")

    for cmd in (
        [*kubectl, "-n", cfg.namespace, "get", "pods"],
        [*kubectl, "-n", cfg.namespace, "get", "svc,ingress"],
        [*kubectl, "get", "ingressclass"],
    ):
        proc = run_command(cmd, check=False)
        sys.stdout.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")

    ingress_class_proc = run_command(
        [
            *kubectl,
            "-n",
            cfg.namespace,
            "get",
            "ingress",
            cfg.ingress_name,
            "-o",
            "jsonpath={.spec.ingressClassName}",
        ],
        check=False,
    )
    ingress_class = (ingress_class_proc.stdout or "").strip()
    classes_proc = run_command(
        [
            *kubectl,
            "get",
            "ingressclass",
            "-o",
            "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}",
        ],
        check=False,
    )
    classes = [line.strip() for line in (classes_proc.stdout or "").splitlines() if line.strip()]
    class_valid = True
    if ingress_class:
        if ingress_class in classes:
            print(f"[OK] Ingress class on {cfg.ingress_name}: {ingress_class}")
        else:
            class_valid = False
            print(
                f"[WARN] Ingress class on {cfg.ingress_name} is '{ingress_class}', available classes: {' '.join(classes) or '(none)'}",
                file=sys.stderr,
            )
            print(
                "[WARN] Patch example: bash scripts/microk8s-patch-ingress-class.sh public",
                file=sys.stderr,
            )
    else:
        class_valid = False
        print(f"[WARN] Ingress class is empty on {cfg.ingress_name}", file=sys.stderr)

    rules = _ingress_rules(kubectl, cfg.namespace, cfg.ingress_name)
    if not rules:
        raise MediaStackError(f"No ingress hosts found on {cfg.ingress_name}")

    print(f"\nTesting ingress routes from this node (Host header -> http://{cfg.node_ip}/)")
    failures = 0
    for host, svc in rules:
        if not host:
            continue
        if svc:
            svc_probe = run_command(
                [*kubectl, "-n", cfg.namespace, "get", "svc", svc],
                check=False,
            )
            if svc_probe.returncode != 0:
                print(
                    f"[WARN] {host} -> skipped (backend service '{svc}' not installed)",
                    file=sys.stderr,
                )
                continue
        code = _http_code_with_host(cfg.node_ip, host)
        if code in {200, 301, 302, 303, 307, 308, 401, 403}:
            print(f"[OK] {host} -> HTTP {code}")
        else:
            print(f"[WARN] {host} -> HTTP {code or 0:03d}", file=sys.stderr)
            failures += 1

    host_list = [h for h, _svc in rules if h]
    if host_list:
        print("\nHosts file helper:")
        print(f"{cfg.node_ip} {' '.join(host_list)}")

    if failures:
        if not class_valid:
            raise MediaStackError("Smoke test failed and ingress class appears invalid/missing.")
        raise MediaStackError(f"Smoke test completed with {failures} failing route(s).")

    print("[OK] Smoke test passed for all ingress hosts.")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parse_config(argv))
    except (ConfigError, MediaStackError, OSError, ValueError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
