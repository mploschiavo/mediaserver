"""ReleasePipelineRunner — workflow runner for the release-pipeline subcommands.

ADR-0015 Phase 7f. Pre-Phase-7f the entire release-pipeline CLI
(``cli/commands/release_pipeline_main.py``) lived as one class
with 19 methods — over the :envvar:`CLASSES_OVER_15_METHODS_RATCHET`
threshold and mixing argparse build helpers with the seven
``run_*`` orchestration methods.

Phase 7f's response: keep the argparse builder in commands/
(per the ADR boundary contract) and extract the seven ``run_*``
orchestration methods + the ``print_json`` utility onto this
workflows-tier class. The commands-tier shim instantiates this
runner once and the argparse handlers dispatch directly to its
methods.

The class composes :class:`WorkflowCompositionService` to reach
the seven workflow services (release-version-policy, image-builder,
compose-deployer, k8s-deployer, etc.). Each ``run_*`` method is
~5-10 lines: pull image refs, call a service, JSON-print the
result.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from media_stack.cli.workflows.workflow_composition_service import (
    WorkflowCompositionService,
)
from media_stack.core.exceptions import MediaStackError


class ReleasePipelineRunner:
    """Workflow runner: 7 release-pipeline subcommand handlers + JSON output."""

    def __init__(self, root_dir: Path) -> None:
        self._root_dir = root_dir
        self._composition = WorkflowCompositionService(root_dir)
        self._config = self._composition.release_config()

    def run_policy_check(self, args: argparse.Namespace) -> int:
        result = self._composition.release_version_policy().check(str(args.base_ref))
        self.print_json(asdict(result))
        return 0 if result.passed else 1

    def run_versions(self, _: argparse.Namespace) -> int:
        refs = self._config.release_image_refs()
        self.print_json(asdict(refs))
        return 0

    def run_build(self, args: argparse.Namespace) -> int:
        if not bool(args.skip_policy_check):
            result = self._composition.release_version_policy().check(
                str(args.base_ref),
            )
            self.print_json(asdict(result))
            if not result.passed:
                raise MediaStackError(
                    "Version policy check failed. "
                    "Bump versions before building release images."
                )
        refs = self._config.release_image_refs(
            str(args.controller_image), str(args.ui_image),
        )
        service = self._composition.release_image_builder()
        result = service.build(refs, no_push=bool(args.no_push))
        service.write_artifact(result, str(args.output_json))
        self.print_json(asdict(result))
        return 0

    def run_deploy_compose(self, args: argparse.Namespace) -> int:
        refs = self._config.release_image_refs(
            str(args.controller_image), str(args.ui_image),
        )
        result = self._composition.release_compose_deployer().deploy(
            refs,
            controller_health_url=str(args.controller_health_url),
            ui_health_url=str(args.ui_health_url),
            health_timeout_seconds=int(args.health_timeout_seconds),
        )
        self.print_json(asdict(result))
        return 0

    def run_verify_compose(self, args: argparse.Namespace) -> int:
        refs = self._config.release_image_refs(
            str(args.controller_image), str(args.ui_image),
        )
        result = self._composition.release_compose_deployer().verify(refs)
        self.print_json(asdict(result))
        return 0

    def run_deploy_kubernetes(self, args: argparse.Namespace) -> int:
        refs = self._config.release_image_refs(
            str(args.controller_image), str(args.ui_image),
        )
        result = self._composition.release_kubernetes_deployer().deploy(
            refs,
            namespace=str(args.namespace),
            rollout_timeout=str(args.rollout_timeout),
            include_controller_cronjobs=bool(args.include_controller_cronjobs),
        )
        self.print_json(asdict(result))
        return 0

    def run_verify_kubernetes(self, args: argparse.Namespace) -> int:
        refs = self._config.release_image_refs(
            str(args.controller_image), str(args.ui_image),
        )
        result = self._composition.release_kubernetes_deployer().verify(
            refs,
            namespace=str(args.namespace),
            include_controller_cronjobs=bool(args.include_controller_cronjobs),
        )
        self.print_json(asdict(result))
        return 0

    def print_json(self, payload: object) -> None:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        sys.stdout.write("\n")


__all__ = ["ReleasePipelineRunner"]
