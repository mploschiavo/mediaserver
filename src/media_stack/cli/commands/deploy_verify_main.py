#!/usr/bin/env python3
"""Entry-point shim for ``bin/deploy-verify.sh``.

ADR-0015 Phase 7i. Pre-Phase-7i this module held the full
``DeployVerifyCommand`` (162 LoC) with a ``@staticmethod _run``
violator and module-level alias hacks. Phase 7i moved the
orchestration onto :class:`DeployVerifyRunner` under workflows/.

Test-surface preservation: ``build_arg_parser`` and ``ts`` are
still module-level aliases so
:mod:`tests.unit.core.test_cli_commands_extended` keeps working.

Media Automation Stack by Matthew Loschiavo:
https://matthewloschiavo.com
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from media_stack.cli.workflows.deploy_verify_runner import DeployVerifyRunner
from media_stack.core.exceptions import ConfigError, MediaStackError


class DeployVerifyEntryPoint:
    """Per-ADR-0012 entry-point: argparse → runner.run."""

    def __init__(self) -> None:
        self._runner = DeployVerifyRunner()

    @property
    def runner(self) -> DeployVerifyRunner:
        return self._runner

    def build_arg_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="bin/deploy-verify.sh",
            description=(
                "End-to-end deterministic deploy runner: install/bootstrap, "
                "flow verify, smoke checks, optional Playwright smoke, "
                "final status snapshot."
            ),
        )
        parser.add_argument("node_ip", nargs="?")
        parser.add_argument(
            "namespace", nargs="?",
            default=os.environ.get("NAMESPACE", "media-stack"),
        )
        parser.add_argument(
            "profile", nargs="?",
            default=os.environ.get("PROFILE", "full"),
        )
        parser.add_argument(
            "--ingress-domain",
            default=os.environ.get("INGRESS_DOMAIN", "local"),
        )
        parser.add_argument(
            "--run-playwright",
            action="store_true",
            default=str(os.environ.get("RUN_PLAYWRIGHT", "0")).strip() == "1",
        )
        return parser

    def main(self, argv: list[str] | None = None) -> int:
        args = self.build_arg_parser().parse_args(argv)
        # parents[4] = repo root (this file at src/media_stack/cli/commands/...).
        root_dir = Path(__file__).resolve().parents[4]
        return self._runner.run(
            node_ip=str(args.node_ip or os.environ.get("NODE_IP") or "").strip(),
            namespace=str(args.namespace or "").strip(),
            profile=str(args.profile or "").strip(),
            ingress_domain=str(args.ingress_domain or "").strip(),
            run_playwright=bool(args.run_playwright),
            root_dir=root_dir,
        )


# Module-level singleton + back-compat aliases.
_INSTANCE = DeployVerifyEntryPoint()
build_arg_parser = _INSTANCE.build_arg_parser
main = _INSTANCE.main
ts = _INSTANCE.runner.ts
info = _INSTANCE.runner.info


__all__ = [
    "DeployVerifyEntryPoint",
    "build_arg_parser",
    "info",
    "main",
    "ts",
]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ConfigError, MediaStackError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        sys.exit(1)
