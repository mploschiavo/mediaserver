"""SetupLanTlsRunner — generate LAN TLS cert + patch ingress.

ADR-0015 Phase 7l. Pre-Phase-7l ``SetupLanTlsCommand`` lived in
commands/ with 4 helper methods that shell out to ``mkcert`` /
``openssl`` / ``kubectl``. The class is workflow material (file
I/O + subprocess + k8s API patches); Phase 7l moves it to
workflows/.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from media_stack.core.cli_common import kube_cmd, run_command
from media_stack.core.exceptions import ConfigError, MediaStackError


_OPENSSL_CERT_DAYS = "365"
_OPENSSL_KEY_SIZE = "rsa:4096"


class SetupLanTlsRunner:
    """Workflow: generate TLS cert (mkcert preferred) + patch ingress TLS."""

    def run(self, args: argparse.Namespace) -> int:
        kubectl = kube_cmd()
        node_ip = self._resolve_node_ip(args.node_ip)
        hosts = self._read_ingress_hosts(kubectl, args.namespace, args.ingress_name)
        if not hosts:
            raise ConfigError(
                f"No ingress hosts found on {args.namespace}/{args.ingress_name}"
            )
        with tempfile.TemporaryDirectory(prefix="media-stack-lan-tls-") as tmpdir:
            work_dir = Path(tmpdir)
            crt = work_dir / "tls.crt"
            key = work_dir / "tls.key"
            if shutil.which("mkcert"):
                print("[INFO] Generating TLS cert with mkcert")
                self._generate_with_mkcert(crt, key, hosts, node_ip)
            else:
                print(
                    "[WARN] mkcert not found; generating self-signed cert with openssl"
                )
                self._generate_with_openssl(crt, key, hosts, node_ip, work_dir)
            secret_yaml = run_command(
                [
                    *kubectl, "-n", args.namespace, "create", "secret", "tls",
                    args.tls_secret_name,
                    f"--cert={crt}", f"--key={key}",
                    "--dry-run=client", "-o", "yaml",
                ],
                check=True,
            ).stdout
            run_command(
                [*kubectl, "-n", args.namespace, "apply", "-f", "-"],
                check=True,
                input_text=secret_yaml,
            )
        patch_payload = {
            "spec": {"tls": [{"secretName": args.tls_secret_name, "hosts": hosts}]}
        }
        run_command(
            [
                *kubectl, "-n", args.namespace, "patch", "ingress",
                args.ingress_name, "--type", "merge", "-p",
                json.dumps(patch_payload),
            ],
            check=True,
        )
        print(f"[OK] TLS secret applied: {args.namespace}/{args.tls_secret_name}")
        print(
            f"[OK] Ingress TLS enabled on {args.namespace}/{args.ingress_name}"
        )
        return 0

    def _resolve_node_ip(self, explicit_node_ip: str) -> str:
        if explicit_node_ip.strip():
            return explicit_node_ip.strip()
        proc = run_command(["hostname", "-I"], check=False)
        if proc.returncode != 0:
            return ""
        out = (proc.stdout or "").strip()
        return out.split()[0] if out else ""

    def _read_ingress_hosts(
        self, kubectl: list[str], namespace: str, ingress_name: str,
    ) -> list[str]:
        proc = run_command(
            [
                *kubectl, "-n", namespace, "get", "ingress", ingress_name,
                "-o", "jsonpath={range .spec.rules[*]}{.host}{'\\n'}{end}",
            ],
            check=False,
        )
        if proc.returncode != 0:
            return []
        return [
            line.strip() for line in (proc.stdout or "").splitlines()
            if line.strip()
        ]

    def _generate_with_mkcert(
        self, crt: Path, key: Path, hosts: list[str], node_ip: str,
    ) -> None:
        args = ["mkcert", "-cert-file", str(crt), "-key-file", str(key), *hosts]
        if node_ip:
            args.append(node_ip)
        run_command(args, check=True)

    def _generate_with_openssl(
        self,
        crt: Path,
        key: Path,
        hosts: list[str],
        node_ip: str,
        work_dir: Path,
    ) -> None:
        if shutil.which("openssl") is None:
            raise MediaStackError(
                "openssl is required when mkcert is not installed."
            )
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
                "openssl", "req", "-x509", "-nodes",
                "-newkey", _OPENSSL_KEY_SIZE,
                "-keyout", str(key), "-out", str(crt),
                "-days", _OPENSSL_CERT_DAYS,
                "-config", str(san_file), "-extensions", "v3_req",
            ],
            check=True,
        )


__all__ = ["SetupLanTlsRunner"]
