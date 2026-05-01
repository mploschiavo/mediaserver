#!/usr/bin/env python3
"""Release pipeline CLI for build, deploy, and runtime verification."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from media_stack.cli.workflows.workflow_composition_service import WorkflowCompositionService
from media_stack.core.exceptions import MediaStackError

COMPOSE_DEPLOY_HEALTH_TIMEOUT_SECONDS = 180
COMPOSE_VERIFY_HEALTH_TIMEOUT_SECONDS = 60


class ReleasePipelineCommand:
    """Coordinates release build/deploy/verify subcommands."""

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir or Path(__file__).resolve().parents[4]
        self.composition = WorkflowCompositionService(self.root_dir)
        self.config = self.composition.release_config()

    def build_arg_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="media-stack-release",
            description="Build, deploy, and prove Compose/Kubernetes media-stack releases.",
        )
        subcommands = parser.add_subparsers(dest="command", required=True)
        self.add_policy_check_parser(subcommands)
        self.add_versions_parser(subcommands)
        self.add_build_parser(subcommands)
        self.add_compose_parsers(subcommands)
        self.add_kubernetes_parsers(subcommands)
        return parser

    def add_policy_check_parser(self, subcommands: argparse._SubParsersAction) -> None:
        parser = subcommands.add_parser("policy-check", help="Enforce release version bump policy.")
        parser.add_argument("--base-ref", default="origin/main")
        parser.set_defaults(handler=self.run_policy_check)

    def add_versions_parser(self, subcommands: argparse._SubParsersAction) -> None:
        parser = subcommands.add_parser("versions", help="Print release versions and default image refs.")
        parser.set_defaults(handler=self.run_versions)

    def add_build_parser(self, subcommands: argparse._SubParsersAction) -> None:
        parser = subcommands.add_parser("build", help="Build/push controller and UI images.")
        parser.add_argument("--controller-image", default="")
        parser.add_argument("--ui-image", default="")
        parser.add_argument("--no-push", action="store_true")
        parser.add_argument("--output-json", default="")
        parser.add_argument("--base-ref", default="origin/main")
        parser.add_argument("--skip-policy-check", action="store_true")
        parser.set_defaults(handler=self.run_build)

    def add_compose_parsers(self, subcommands: argparse._SubParsersAction) -> None:
        deploy = subcommands.add_parser("deploy-compose", help="Deploy Compose controller and UI images.")
        self.add_image_args(deploy)
        self.add_health_args(deploy, default_timeout=COMPOSE_DEPLOY_HEALTH_TIMEOUT_SECONDS)
        deploy.set_defaults(handler=self.run_deploy_compose)

        verify = subcommands.add_parser("verify-compose", help="Verify Compose image refs and health.")
        self.add_image_args(verify)
        self.add_health_args(verify, default_timeout=COMPOSE_VERIFY_HEALTH_TIMEOUT_SECONDS)
        verify.set_defaults(handler=self.run_verify_compose)

    def add_kubernetes_parsers(self, subcommands: argparse._SubParsersAction) -> None:
        deploy = subcommands.add_parser("deploy-k8s", help="Deploy Kubernetes controller and UI images.")
        self.add_kubernetes_args(deploy)
        deploy.set_defaults(handler=self.run_deploy_kubernetes)

        verify = subcommands.add_parser("verify-k8s", help="Verify Kubernetes image specs and running pod IDs.")
        self.add_kubernetes_args(verify)
        verify.set_defaults(handler=self.run_verify_kubernetes)

    def add_image_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--controller-image", default="")
        parser.add_argument("--ui-image", default="")

    def add_health_args(self, parser: argparse.ArgumentParser, *, default_timeout: int) -> None:
        parser.add_argument("--controller-health-url", default="http://127.0.0.1:9100/healthz")
        parser.add_argument("--ui-health-url", default="http://127.0.0.1:9101/healthz")
        parser.add_argument("--health-timeout-seconds", type=int, default=default_timeout)

    def add_kubernetes_args(self, parser: argparse.ArgumentParser) -> None:
        self.add_image_args(parser)
        parser.add_argument("--namespace", default="media-stack")
        parser.add_argument("--rollout-timeout", default="300s")
        parser.add_argument("--include-controller-cronjobs", action="store_true")

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

    def run_policy_check(self, args: argparse.Namespace) -> int:
        result = self.composition.release_version_policy().check(str(args.base_ref))
        self.print_json(asdict(result))
        return 0 if result.passed else 1

    def run_versions(self, _: argparse.Namespace) -> int:
        refs = self.config.release_image_refs()
        self.print_json(asdict(refs))
        return 0

    def run_build(self, args: argparse.Namespace) -> int:
        if not bool(args.skip_policy_check):
            result = self.composition.release_version_policy().check(str(args.base_ref))
            self.print_json(asdict(result))
            if not result.passed:
                raise MediaStackError("Version policy check failed. Bump versions before building release images.")
        refs = self.config.release_image_refs(str(args.controller_image), str(args.ui_image))
        service = self.composition.release_image_builder()
        result = service.build(refs, no_push=bool(args.no_push))
        service.write_artifact(result, str(args.output_json))
        self.print_json(asdict(result))
        return 0

    def run_deploy_compose(self, args: argparse.Namespace) -> int:
        refs = self.config.release_image_refs(str(args.controller_image), str(args.ui_image))
        result = self.composition.release_compose_deployer().deploy(
            refs,
            controller_health_url=str(args.controller_health_url),
            ui_health_url=str(args.ui_health_url),
            health_timeout_seconds=int(args.health_timeout_seconds),
        )
        self.print_json(asdict(result))
        return 0

    def run_verify_compose(self, args: argparse.Namespace) -> int:
        refs = self.config.release_image_refs(str(args.controller_image), str(args.ui_image))
        result = self.composition.release_compose_deployer().verify(refs)
        self.print_json(asdict(result))
        return 0

    def run_deploy_kubernetes(self, args: argparse.Namespace) -> int:
        refs = self.config.release_image_refs(str(args.controller_image), str(args.ui_image))
        result = self.composition.release_kubernetes_deployer().deploy(
            refs,
            namespace=str(args.namespace),
            rollout_timeout=str(args.rollout_timeout),
            include_controller_cronjobs=bool(args.include_controller_cronjobs),
        )
        self.print_json(asdict(result))
        return 0

    def run_verify_kubernetes(self, args: argparse.Namespace) -> int:
        refs = self.config.release_image_refs(str(args.controller_image), str(args.ui_image))
        result = self.composition.release_kubernetes_deployer().verify(
            refs,
            namespace=str(args.namespace),
            include_controller_cronjobs=bool(args.include_controller_cronjobs),
        )
        self.print_json(asdict(result))
        return 0

    def print_json(self, payload: object) -> None:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        sys.stdout.write("\n")


_instance = ReleasePipelineCommand()
build_arg_parser = _instance.build_arg_parser
main = _instance.main


if __name__ == "__main__":
    raise SystemExit(main())
