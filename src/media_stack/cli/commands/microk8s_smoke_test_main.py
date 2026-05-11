#!/usr/bin/env python3
"""Entry-point shim for ``bin/microk8s-smoke-test.sh``.

ADR-0015 Phase 7h. Pre-Phase-7h this module held a 234-LoC
``Microk8sSmokeTestCommand`` with 6 instance methods doing the
LAN smoke test work + argparse glue. Phase 7h moved the smoke-
test logic onto :class:`Microk8sSmokeTestRunner` under workflows/;
what remains is argparse + main + module-level back-compat
aliases.
"""

from __future__ import annotations

import argparse
import os
import sys

from media_stack.cli.workflows.microk8s_smoke_test_runner import (
    Microk8sSmokeTestConfig,
    Microk8sSmokeTestRunner,
)
from media_stack.core.exceptions import ConfigError, MediaStackError


class Microk8sSmokeTestEntryPoint:
    """Per-ADR-0012 entry-point: argparse → runner.run."""

    def __init__(self) -> None:
        self._runner = Microk8sSmokeTestRunner()

    @property
    def runner(self) -> Microk8sSmokeTestRunner:
        return self._runner

    def parse_config(self, argv: list[str] | None = None) -> Microk8sSmokeTestConfig:
        module = sys.modules[__name__]
        parser = argparse.ArgumentParser(
            prog="bin/microk8s-smoke-test.sh",
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
            os.environ.get("INGRESS_NAME", "media-stack-ingress").strip()
            or "media-stack-ingress"
        )
        node_ip = str(args.node_ip or "").strip() or module.first_host_ip()
        if not node_ip:
            raise ConfigError(
                "Unable to detect node IP. Pass it explicitly: "
                "bin/microk8s-smoke-test.sh <NODE_IP>"
            )
        return Microk8sSmokeTestConfig(
            node_ip=node_ip, namespace=namespace, ingress_name=ingress_name,
        )

    def main(self, argv: list[str] | None = None) -> int:
        try:
            cfg = self.parse_config(argv)
            return self._runner.run(cfg)
        except (ConfigError, MediaStackError, OSError, ValueError) as exc:
            print(f"[ERR] {exc}", file=sys.stderr)
            return 1


# Module-level singleton + aliases for the historical surface.
_INSTANCE = Microk8sSmokeTestEntryPoint()
_RUNNER = _INSTANCE.runner

first_host_ip = _RUNNER.first_host_ip
parse_config = _INSTANCE.parse_config
http_code_with_host = _RUNNER.http_code_with_host
ingress_rules = _RUNNER.ingress_rules
run = _RUNNER.run
main = _INSTANCE.main


__all__ = [
    "Microk8sSmokeTestConfig",
    "Microk8sSmokeTestEntryPoint",
    "first_host_ip",
    "http_code_with_host",
    "ingress_rules",
    "main",
    "parse_config",
    "run",
]


if __name__ == "__main__":
    raise SystemExit(main())
