#!/usr/bin/env python3
"""Entry-point shim for ``media-stack-release``.

ADR-0015 Phase 7f. Pre-Phase-7f ``ReleasePipelineCommand`` was a
19-method class — over the :envvar:`CLASSES_OVER_15_METHODS_RATCHET`
threshold and mixing argparse build helpers (10 methods) with the
seven ``run_*`` orchestration methods.

Phase 7f keeps the argparse builder in commands/ (per the ADR
boundary contract: "Per-CLI argument parsing stays in commands")
and moves the orchestration onto
:class:`ReleasePipelineRunner` under workflows/. The split:

* :class:`ReleasePipelineArgParserBuilder` — owns the 10 argparse
  helper methods (build the main parser + 5 sub-parsers). Each
  ``add_*_parser`` wires ``set_defaults(handler=runner.run_*)`` so
  the dispatch wiring lives in one place.
* :class:`ReleasePipelineCommand` — thin entry-point: build parser,
  parse argv, dispatch to handler, translate exit code.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from media_stack.cli.workflows.release_pipeline_runner import (
    ReleasePipelineRunner,
)
from media_stack.core.exceptions import MediaStackError


COMPOSE_DEPLOY_HEALTH_TIMEOUT_SECONDS = 180
COMPOSE_VERIFY_HEALTH_TIMEOUT_SECONDS = 60


class ReleasePipelineArgParserBuilder:
    """Builder: assemble the release-pipeline argparse tree.

    Each ``add_*_parser`` sub-method wires its sub-parser's
    ``handler`` default to the matching :class:`ReleasePipelineRunner`
    method so the main entry-point can call ``args.handler(args)``
    without a subcommand-name switch statement.
    """

    def __init__(self, runner: ReleasePipelineRunner) -> None:
        self._runner = runner

    def build(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="media-stack-release",
            description=(
                "Build, deploy, and prove Compose/Kubernetes media-stack releases."
            ),
        )
        subcommands = parser.add_subparsers(dest="command", required=True)
        self._add_policy_check_parser(subcommands)
        self._add_versions_parser(subcommands)
        self._add_build_parser(subcommands)
        self._add_compose_parsers(subcommands)
        self._add_kubernetes_parsers(subcommands)
        return parser

    def _add_policy_check_parser(
        self, subcommands: argparse._SubParsersAction,
    ) -> None:
        parser = subcommands.add_parser(
            "policy-check", help="Enforce release version bump policy.",
        )
        parser.add_argument("--base-ref", default="origin/main")
        parser.set_defaults(handler=self._runner.run_policy_check)

    def _add_versions_parser(
        self, subcommands: argparse._SubParsersAction,
    ) -> None:
        parser = subcommands.add_parser(
            "versions", help="Print release versions and default image refs.",
        )
        parser.set_defaults(handler=self._runner.run_versions)

    def _add_build_parser(
        self, subcommands: argparse._SubParsersAction,
    ) -> None:
        parser = subcommands.add_parser(
            "build", help="Build/push controller and UI images.",
        )
        parser.add_argument("--controller-image", default="")
        parser.add_argument("--ui-image", default="")
        parser.add_argument("--no-push", action="store_true")
        parser.add_argument("--output-json", default="")
        parser.add_argument("--base-ref", default="origin/main")
        parser.add_argument("--skip-policy-check", action="store_true")
        parser.set_defaults(handler=self._runner.run_build)

    def _add_compose_parsers(
        self, subcommands: argparse._SubParsersAction,
    ) -> None:
        deploy = subcommands.add_parser(
            "deploy-compose", help="Deploy Compose controller and UI images.",
        )
        self._add_image_args(deploy)
        self._add_health_args(
            deploy, default_timeout=COMPOSE_DEPLOY_HEALTH_TIMEOUT_SECONDS,
        )
        deploy.set_defaults(handler=self._runner.run_deploy_compose)

        verify = subcommands.add_parser(
            "verify-compose", help="Verify Compose image refs and health.",
        )
        self._add_image_args(verify)
        self._add_health_args(
            verify, default_timeout=COMPOSE_VERIFY_HEALTH_TIMEOUT_SECONDS,
        )
        verify.set_defaults(handler=self._runner.run_verify_compose)

    def _add_kubernetes_parsers(
        self, subcommands: argparse._SubParsersAction,
    ) -> None:
        deploy = subcommands.add_parser(
            "deploy-k8s", help="Deploy Kubernetes controller and UI images.",
        )
        self._add_kubernetes_args(deploy)
        deploy.set_defaults(handler=self._runner.run_deploy_kubernetes)

        verify = subcommands.add_parser(
            "verify-k8s",
            help="Verify Kubernetes image specs and running pod IDs.",
        )
        self._add_kubernetes_args(verify)
        verify.set_defaults(handler=self._runner.run_verify_kubernetes)

    def _add_image_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--controller-image", default="")
        parser.add_argument("--ui-image", default="")

    def _add_health_args(
        self, parser: argparse.ArgumentParser, *, default_timeout: int,
    ) -> None:
        parser.add_argument(
            "--controller-health-url",
            default="http://127.0.0.1:9100/healthz",
        )
        parser.add_argument(
            "--ui-health-url", default="http://127.0.0.1:9101/healthz",
        )
        parser.add_argument(
            "--health-timeout-seconds", type=int, default=default_timeout,
        )

    def _add_kubernetes_args(self, parser: argparse.ArgumentParser) -> None:
        self._add_image_args(parser)
        parser.add_argument("--namespace", default="media-stack")
        parser.add_argument("--rollout-timeout", default="300s")
        parser.add_argument("--include-controller-cronjobs", action="store_true")


class ReleasePipelineCommand:
    """Thin entry-point: parse argv, dispatch to runner, translate exit code."""

    def __init__(self, root_dir: Path | None = None) -> None:
        self._root_dir = root_dir or Path(__file__).resolve().parents[4]
        self._runner = ReleasePipelineRunner(self._root_dir)
        self._arg_parser_builder = ReleasePipelineArgParserBuilder(self._runner)

    def build_arg_parser(self) -> argparse.ArgumentParser:
        return self._arg_parser_builder.build()

    def main(self, argv: list[str] | None = None) -> int:
        args = self.build_arg_parser().parse_args(argv)
        try:
            return int(args.handler(args))
        except subprocess.CalledProcessError as exc:
            sys.stderr.write(f"{exc}\n")
            return exc.returncode or 1
        except (MediaStackError, OSError, RuntimeError, ValueError) as exc:
            sys.stderr.write(f"{exc}\n")
            return 1


_instance = ReleasePipelineCommand()
build_arg_parser = _instance.build_arg_parser
main = _instance.main


if __name__ == "__main__":
    raise SystemExit(main())
