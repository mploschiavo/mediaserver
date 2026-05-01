#!/usr/bin/env python3
"""Safe teardown CLI for Compose and Kubernetes media-stack deployments."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from media_stack.cli.workflows.teardown_models import TeardownRequest
from media_stack.cli.workflows.workflow_composition_service import WorkflowCompositionService
from media_stack.core.exceptions import MediaStackError


class TeardownStackCommand:
    """Parses CLI args and delegates teardown work to workflow services."""

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir or Path(__file__).resolve().parents[4]

    def build_arg_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="media-stack-teardown",
            description="Cross-platform safe teardown for Compose and Kubernetes media-stack deployments.",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.add_argument("--target", choices=["auto", "compose", "k8s", "both"], default="auto")
        parser.add_argument("--scope", choices=["config", "data", "everything"], default="config")
        parser.add_argument("--environment", choices=["local", "dev", "staging", "prod"], default="local")
        parser.add_argument(
            "--compose-file",
            default=str(self.root_dir / "deploy" / "compose" / "docker-compose.yml"),
        )
        parser.add_argument("--config-root", default=str(self.root_dir / "config"))
        parser.add_argument("--data-root", default=str(self.root_dir / "data"))
        parser.add_argument("--media-root", default=str(self.root_dir / "media"))
        parser.add_argument("--k8s-namespace", default="media-stack")
        parser.add_argument(
            "--confirm-token",
            default="",
            help="Required for production execute mode. Expected form: TEARDOWN <namespace>.",
        )
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", default=True)
        mode.add_argument("--execute", dest="dry_run", action="store_false")
        mode.add_argument("--preview", dest="dry_run", action="store_true")
        parser.add_argument("--yes", "-y", action="store_true")
        return parser

    def main(self, argv: list[str] | None = None) -> int:
        args = self.build_arg_parser().parse_args(argv)
        try:
            request = self.request_from_args(args)
            composition = WorkflowCompositionService(self.root_dir)
            plan = composition.teardown_planner().build_plan(request)
            result = composition.teardown_executor(assume_yes=request.assume_yes).execute(plan)
            return result.exit_code
        except (MediaStackError, OSError, RuntimeError, ValueError) as exc:
            sys.stderr.write(f"{exc}\n")
            return 1

    def request_from_args(self, args: argparse.Namespace) -> TeardownRequest:
        return TeardownRequest(
            target=args.target,
            scope=args.scope,
            compose_file=Path(str(args.compose_file)).expanduser().resolve(),
            config_root=Path(str(args.config_root)).expanduser().resolve(),
            data_root=Path(str(args.data_root)).expanduser().resolve(),
            media_root=Path(str(args.media_root)).expanduser().resolve(),
            k8s_namespace=str(args.k8s_namespace),
            dry_run=bool(args.dry_run),
            assume_yes=bool(args.yes),
            environment=args.environment,
            confirmation_token=str(args.confirm_token),
        )


_instance = TeardownStackCommand()
build_arg_parser = _instance.build_arg_parser
main = _instance.main


if __name__ == "__main__":
    raise SystemExit(main())
