#!/usr/bin/env python3
"""Generate LAN TLS certificate and patch ingress TLS settings."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from media_stack.core.exceptions import ConfigError, MediaStackError

from media_stack.cli.workflows.cli_common import kube_cmd, run_command


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bin/setup-lan-tls.sh",
        description=(
            "Generates a LAN TLS certificate for ingress hosts and configures ingress TLS. "
            "Prefers mkcert, falls back to openssl self-signed cert."
        ),
    )
    parser.add_argument(
        "--namespace",
        default=(os.environ.get("NAMESPACE", "media-stack") or "media-stack"),
        help="Kubernetes namespace (default: media-stack)",
    )
    parser.add_argument(
        "--ingress-name",
        default=(os.environ.get("INGRESS_NAME", "media-stack-ingress") or "media-stack-ingress"),
        help="Ingress resource name (default: media-stack-ingress)",
    )
    parser.add_argument(
        "--tls-secret-name",
        default=(os.environ.get("TLS_SECRET_NAME", "media-stack-tls") or "media-stack-tls"),
        help="TLS secret name (default: media-stack-tls)",
    )
    parser.add_argument(
        "--node-ip",
        default=(os.environ.get("NODE_IP", "") or "").strip(),
        help="Optional SAN IP override",
    )
    return parser.parse_args(argv)


def _resolve_node_ip(explicit_node_ip: str) -> str:
    if explicit_node_ip.strip():
        return explicit_node_ip.strip()
    proc = run_command(["hostname", "-I"], check=False)
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip().split()[0] if (proc.stdout or "").strip() else ""


def _read_ingress_hosts(kubectl: list[str], namespace: str, ingress_name: str) -> list[str]:
    proc = run_command(
        [
            *kubectl,
            "-n",
            namespace,
            "get",
            "ingress",
            ingress_name,
            "-o",
            "jsonpath={range .spec.rules[*]}{.host}{'\\n'}{end}",
        ],
        check=False,
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def _generate_with_mkcert(crt: Path, key: Path, hosts: list[str], node_ip: str) -> None:
    args = ["mkcert", "-cert-file", str(crt), "-key-file", str(key), *hosts]
    if node_ip:
        args.append(node_ip)
    run_command(args, check=True)


def _generate_with_openssl(
    crt: Path, key: Path, hosts: list[str], node_ip: str, work_dir: Path
) -> None:
    if shutil.which("openssl") is None:
        raise MediaStackError("openssl is required when mkcert is not installed.")
    san_file = work_dir / "san.cnf"
    lines = [
        "[req]",
        "distinguished_name=req_distinguished_name",
        "x509_extensions=v3_req",
        "prompt=no",
        "[req_distinguished_name]",
        f"CN={hosts[0]}",
        "[v3_req]",
        "subjectAltName=@alt_names",
        "[alt_names]",
    ]
    for idx, host in enumerate(hosts, start=1):
        lines.append(f"DNS.{idx}={host}")
    if node_ip:
        lines.append(f"IP.1={node_ip}")
    san_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run_command(
        [
            "openssl",
            "req",
            "-x509",
            "-nodes",
            "-newkey",
            "rsa:4096",
            "-keyout",
            str(key),
            "-out",
            str(crt),
            "-days",
            "365",
            "-config",
            str(san_file),
            "-extensions",
            "v3_req",
        ],
        check=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    kubectl = kube_cmd()
    node_ip = _resolve_node_ip(args.node_ip)
    hosts = _read_ingress_hosts(kubectl, args.namespace, args.ingress_name)
    if not hosts:
        raise ConfigError(f"No ingress hosts found on {args.namespace}/{args.ingress_name}")

    with tempfile.TemporaryDirectory(prefix="media-stack-lan-tls-") as tmpdir:
        work_dir = Path(tmpdir)
        crt = work_dir / "tls.crt"
        key = work_dir / "tls.key"

        if shutil.which("mkcert"):
            print("[INFO] Generating TLS cert with mkcert")
            _generate_with_mkcert(crt, key, hosts, node_ip)
        else:
            print("[WARN] mkcert not found; generating self-signed cert with openssl")
            _generate_with_openssl(crt, key, hosts, node_ip, work_dir)

        secret_yaml = run_command(
            [
                *kubectl,
                "-n",
                args.namespace,
                "create",
                "secret",
                "tls",
                args.tls_secret_name,
                f"--cert={crt}",
                f"--key={key}",
                "--dry-run=client",
                "-o",
                "yaml",
            ],
            check=True,
        ).stdout
        run_command(
            [*kubectl, "-n", args.namespace, "apply", "-f", "-"],
            check=True,
            input_text=secret_yaml,
        )

    patch_payload = {
        "spec": {
            "tls": [
                {
                    "secretName": args.tls_secret_name,
                    "hosts": hosts,
                }
            ]
        }
    }
    run_command(
        [
            *kubectl,
            "-n",
            args.namespace,
            "patch",
            "ingress",
            args.ingress_name,
            "--type",
            "merge",
            "-p",
            json.dumps(patch_payload),
        ],
        check=True,
    )

    print(f"[OK] TLS secret applied: {args.namespace}/{args.tls_secret_name}")
    print(f"[OK] Ingress TLS enabled on {args.namespace}/{args.ingress_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
