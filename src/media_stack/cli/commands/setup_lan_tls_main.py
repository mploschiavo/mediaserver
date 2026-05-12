#!/usr/bin/env python3
"""Entry-point shim for ``bin/setup-lan-tls.sh``.

ADR-0015 Phase 7l. Pre-Phase-7l this module held ``SetupLanTlsCommand``
with 4 helper methods that shell out to ``mkcert`` / ``openssl`` /
``kubectl``. Phase 7l moved the workflow onto :class:`SetupLanTlsRunner`
under workflows/; what remains is argparse + back-compat aliases.
"""

from __future__ import annotations

import argparse
import os

from media_stack.cli.workflows.setup_lan_tls_runner import SetupLanTlsRunner


class SetupLanTlsEntryPoint:
    """Per-ADR-0012 entry-point: argparse → runner.run."""

    def __init__(self) -> None:
        self._runner = SetupLanTlsRunner()

    @property
    def runner(self) -> SetupLanTlsRunner:
        return self._runner

    def parse_args(self, argv: list[str] | None = None) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            prog="bin/setup-lan-tls.sh",
            description=(
                "Generates a LAN TLS certificate for ingress hosts and "
                "configures ingress TLS."
            ),
        )
        parser.add_argument(
            "--namespace",
            default=(os.environ.get("NAMESPACE", "media-stack") or "media-stack"),
        )
        parser.add_argument(
            "--ingress-name",
            default=(
                os.environ.get("INGRESS_NAME", "media-stack-ingress")
                or "media-stack-ingress"
            ),
        )
        parser.add_argument(
            "--tls-secret-name",
            default=(
                os.environ.get("TLS_SECRET_NAME", "media-stack-tls")
                or "media-stack-tls"
            ),
        )
        parser.add_argument(
            "--node-ip", default=(os.environ.get("NODE_IP", "") or "").strip(),
        )
        return parser.parse_args(argv)

    def main(self, argv: list[str] | None = None) -> int:
        return self._runner.run(self.parse_args(argv))


# Module-level singleton + back-compat aliases for the historical
# import surface (helpers were aliased pre-Phase-7l).
_INSTANCE = SetupLanTlsEntryPoint()
_RUNNER = _INSTANCE.runner
parse_args = _INSTANCE.parse_args
main = _INSTANCE.main
_resolve_node_ip = _RUNNER._resolve_node_ip
_read_ingress_hosts = _RUNNER._read_ingress_hosts
_generate_with_mkcert = _RUNNER._generate_with_mkcert
_generate_with_openssl = _RUNNER._generate_with_openssl


__all__ = [
    "SetupLanTlsEntryPoint",
    "_generate_with_mkcert",
    "_generate_with_openssl",
    "_read_ingress_hosts",
    "_resolve_node_ip",
    "main",
    "parse_args",
]


if __name__ == "__main__":
    raise SystemExit(main())
