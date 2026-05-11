"""Microk8sSmokeTestRunner — LAN smoke test for media-stack ingress routing.

ADR-0015 Phase 7h. Pre-Phase-7h the entire smoke test (host-IP
detection + ingress-rule query + per-host HTTP probe + ingress-
class validation) lived inline in
``cli/commands/microk8s_smoke_test_main.py`` alongside argparse.
Phase 7h moves the workflow logic onto this class; the commands
shim shrinks to argparse + main.
"""

from __future__ import annotations

import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

from media_stack.core.cli_common import kube_cmd, run_command
from media_stack.core.exceptions import MediaStackError
from media_stack.core.logging_utils import log_swallowed


_HTTP_PROBE_TIMEOUT_SECONDS = 8
_OK_STATUS_CODES = frozenset({200, 301, 302, 303, 307, 308, 401, 403})


@dataclass(frozen=True)
class Microk8sSmokeTestConfig:
    node_ip: str
    namespace: str
    ingress_name: str


class Microk8sSmokeTestRunner:
    """Workflow runner: probe ingress routes + validate class membership."""

    def first_host_ip(self) -> str:
        """Detect the first non-loopback IPv4 address for this host."""
        try:
            name = socket.gethostname()
            values = socket.gethostbyname_ex(name)[2]
            for value in values:
                if value and not value.startswith("127."):
                    return value
        except (OSError, socket.gaierror) as exc:
            log_swallowed(exc)
        return ""

    def http_code_with_host(self, node_ip: str, host: str) -> int:
        req = urllib.request.Request(
            f"http://{node_ip}/", method="GET", headers={"Host": host},
        )
        try:
            with urllib.request.urlopen(
                req, timeout=_HTTP_PROBE_TIMEOUT_SECONDS,
            ) as resp:
                return int(getattr(resp, "status", 200))
        except urllib.error.HTTPError as exc:
            return int(exc.code)
        except (urllib.error.URLError, OSError):
            return 0

    def ingress_rules(
        self, kubectl: list[str], namespace: str, ingress_name: str,
    ) -> list[tuple[str, str]]:
        proc = run_command(
            [
                *kubectl,
                "-n", namespace,
                "get", "ingress", ingress_name,
                "-o",
                (
                    "jsonpath={range .spec.rules[*]}{.host}{'|'}"
                    "{.http.paths[0].backend.service.name}{'\\n'}{end}"
                ),
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

    def run(
        self,
        cfg: Microk8sSmokeTestConfig,
        kubectl: list[str] | None = None,
    ) -> int:
        if kubectl is None:
            kubectl = kube_cmd()
        print(f"Using kubectl command: {' '.join(kubectl)}")
        print(f"Namespace: {cfg.namespace}")
        print(f"Ingress: {cfg.ingress_name}")
        print(f"Node IP: {cfg.node_ip}\n")

        self._print_pre_probe_status(kubectl, cfg)
        class_valid = self._validate_ingress_class(kubectl, cfg)
        rules = self.ingress_rules(kubectl, cfg.namespace, cfg.ingress_name)
        if not rules:
            raise MediaStackError(
                f"No ingress hosts found on {cfg.ingress_name}"
            )

        print(
            f"\nTesting ingress routes from this node "
            f"(Host header -> http://{cfg.node_ip}/)"
        )
        failures = self._probe_each_host(kubectl, cfg, rules)

        host_list = [h for h, _svc in rules if h]
        if host_list:
            print("\nHosts file helper:")
            print(f"{cfg.node_ip} {' '.join(host_list)}")

        if failures:
            if not class_valid:
                raise MediaStackError(
                    "Smoke test failed and ingress class appears invalid/missing."
                )
            raise MediaStackError(
                f"Smoke test completed with {failures} failing route(s)."
            )

        print("[OK] Smoke test passed for all ingress hosts.")
        return 0

    def _print_pre_probe_status(
        self, kubectl: list[str], cfg: Microk8sSmokeTestConfig,
    ) -> None:
        for cmd in (
            [*kubectl, "-n", cfg.namespace, "get", "pods"],
            [*kubectl, "-n", cfg.namespace, "get", "svc,ingress"],
            [*kubectl, "get", "ingressclass"],
        ):
            proc = run_command(cmd, check=False)
            sys.stdout.write(proc.stdout or "")
            sys.stderr.write(proc.stderr or "")

    def _validate_ingress_class(
        self, kubectl: list[str], cfg: Microk8sSmokeTestConfig,
    ) -> bool:
        ingress_class_proc = run_command(
            [
                *kubectl, "-n", cfg.namespace, "get", "ingress",
                cfg.ingress_name, "-o", "jsonpath={.spec.ingressClassName}",
            ],
            check=False,
        )
        ingress_class = (ingress_class_proc.stdout or "").strip()
        classes_proc = run_command(
            [
                *kubectl, "get", "ingressclass", "-o",
                "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}",
            ],
            check=False,
        )
        classes = [
            line.strip() for line in (classes_proc.stdout or "").splitlines()
            if line.strip()
        ]
        if not ingress_class:
            print(
                f"[WARN] Ingress class is empty on {cfg.ingress_name}",
                file=sys.stderr,
            )
            return False
        if ingress_class in classes:
            print(f"[OK] Ingress class on {cfg.ingress_name}: {ingress_class}")
            return True
        print(
            f"[WARN] Ingress class on {cfg.ingress_name} is '{ingress_class}', "
            f"available classes: {' '.join(classes) or '(none)'}",
            file=sys.stderr,
        )
        print(
            "[WARN] Patch example: bash bin/microk8s-patch-ingress-class.sh public",
            file=sys.stderr,
        )
        return False

    def _probe_each_host(
        self,
        kubectl: list[str],
        cfg: Microk8sSmokeTestConfig,
        rules: list[tuple[str, str]],
    ) -> int:
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
                        f"[WARN] {host} -> skipped (backend service '{svc}' "
                        "not installed)",
                        file=sys.stderr,
                    )
                    continue
            code = self.http_code_with_host(cfg.node_ip, host)
            if code in _OK_STATUS_CODES:
                print(f"[OK] {host} -> HTTP {code}")
            else:
                print(
                    f"[WARN] {host} -> HTTP {code or 0:03d}", file=sys.stderr,
                )
                failures += 1
        return failures


__all__ = ["Microk8sSmokeTestConfig", "Microk8sSmokeTestRunner"]
