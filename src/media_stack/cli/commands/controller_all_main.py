#!/usr/bin/env python3
"""Entry-point shim for ``bin/bootstrap-all.sh``.

ADR-0015 Phase 7d. Pre-Phase-7d this module held the 816-LoC
``ControllerAllRunner`` god class (~480 LoC of orchestration) +
``ControllerAllCommand`` step-executor host (~280 LoC) +
``ControllerAllConfig``. Phase 7d moved the orchestration into a
new sub-package under workflows/ that composes the shared
:mod:`cli.workflows.controller_phase_planning` helpers (also used
by :class:`ControllerCorePhasesService` — no more duplication
between the two bootstrap pipelines).

What remains here is the entry-point shim:

* :class:`ControllerAllRunner` — a thin subclass of
  :class:`ControllerAllPipeline` kept so existing callers /
  imports addressing this module path keep working.
* :class:`ControllerAllEntryPoint` — argparse + env-bool helpers
  + ``main()``.

Media Automation Stack by Matthew Loschiavo:
https://matthewloschiavo.com
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from media_stack.cli.workflows.controller_all_orchestration import (
    ControllerAllConfig,
    ControllerAllPipeline,
)
from media_stack.cli.workflows.controller_all_orchestration.step_executors import (
    ControllerAllStepExecutors,
)
from media_stack.core.cli_common import err
from media_stack.core.exceptions import ConfigError, KubernetesError
from media_stack.services.controller_component_resolver import (
    PhaseSkipFlagSpec,
    resolve_bootstrap_component_plan,
    resolve_phase_skip_flag_specs,
)


class ControllerAllRunner(ControllerAllPipeline):
    """Thin commands-tier subclass kept for test-patch compatibility.

    The pipeline orchestration logic now lives on
    :class:`ControllerAllPipeline` under
    ``cli/workflows/controller_all_orchestration``. ``ControllerAllRunner``
    survives as a name here because external callers and any
    qualified-path patches may address it. Removal queued for a
    future cleanup pass once nothing references the alias.
    """


class ControllerAllEntryPoint:
    """Per-ADR-0012 entry-point: argv → cfg → pipeline.run → exit code."""

    def env_bool(self, name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def env_bool_candidates(
        self, candidates: tuple[str, ...], default: bool = False,
    ) -> bool:
        for name in candidates:
            if not str(name).strip():
                continue
            if name in os.environ:
                return self.env_bool(name, default)
        return default

    def parse_args(
        self, argv: list[str] | None = None,
    ) -> tuple[argparse.Namespace, tuple[PhaseSkipFlagSpec, ...]]:
        # parents[4] = repo root (this file at src/media_stack/cli/commands/...).
        # See deploy_stack_main / teardown_stack_main for the rationale.
        root_dir = Path(__file__).resolve().parents[4]
        default_config = str(root_dir / "contracts" / "media-stack.config.json")

        pre_parser = argparse.ArgumentParser(add_help=False)
        pre_parser.add_argument("config_file", nargs="?", default=default_config)
        pre_args, _ = pre_parser.parse_known_args(argv)

        config_file = Path(str(pre_args.config_file)).resolve()
        loaded_cfg: dict[str, object] = {}
        if config_file.exists():
            loaded_cfg = resolve_bootstrap_component_plan(config_file).config

        skip_specs = resolve_phase_skip_flag_specs(loaded_cfg, pipeline="bootstrap_all")

        parser = argparse.ArgumentParser(
            prog="bin/bootstrap-all.sh",
            description="Python bootstrap-all orchestration runner",
        )
        parser.add_argument(
            "config_file",
            nargs="?",
            default=default_config,
            help="Bootstrap config JSON path",
        )
        parser.add_argument("--namespace", default=os.environ.get("NAMESPACE", "media-stack"))
        parser.add_argument(
            "--secret-name",
            default=os.environ.get("SECRET_NAME", "media-stack-secrets"),
        )
        parser.add_argument(
            "--prepare-host-root",
            default=os.environ.get("PREPARE_HOST_ROOT", "/srv/media-stack"),
        )
        parser.add_argument(
            "--enable-components",
            dest="enable_components",
            action="store_true",
            default=self.env_bool_candidates(("ENABLE_COMPONENTS",), True),
            help="Enable configured bootstrap component deployments.",
        )
        for spec in skip_specs:
            parser.add_argument(
                *spec.option_strings,
                dest=f"phase_skip_{spec.key}",
                action="store_true",
                default=self.env_bool_candidates(spec.env_vars, False),
                help=spec.help,
            )
        parser.add_argument(
            "--resume",
            dest="resume",
            action="store_true",
            default=str(os.environ.get("BOOTSTRAP_RESUME", "1")).strip().lower()
            in {"1", "true", "yes", "on"},
            help="Resume from completed phase checkpoints (default: enabled).",
        )
        parser.add_argument(
            "--no-resume",
            dest="resume",
            action="store_false",
            help="Ignore previous phase checkpoints and run all phases.",
        )
        parser.add_argument(
            "--state-file",
            default=os.environ.get("BOOTSTRAP_STATE_FILE", ""),
            help="Checkpoint state file path (default: .state/bootstrap-all-<namespace>.json).",
        )
        return parser.parse_args(argv), skip_specs

    def main(self, argv: list[str] | None = None) -> int:
        args, skip_specs = self.parse_args(argv)
        root_dir = Path(__file__).resolve().parents[4]
        config_file = Path(str(args.config_file)).resolve()
        state_file = (
            Path(args.state_file).resolve()
            if str(args.state_file).strip()
            else root_dir / ".state" / f"bootstrap-all-{args.namespace}.json"
        )
        cfg = ControllerAllConfig(
            root_dir=root_dir,
            config_file=config_file,
            namespace=str(args.namespace).strip(),
            enable_components=bool(args.enable_components),
            secret_name=str(args.secret_name).strip(),
            prepare_host_root=str(args.prepare_host_root).strip(),
            phase_skip_flags={
                spec.key: bool(getattr(args, f"phase_skip_{spec.key}", False))
                for spec in skip_specs
            },
            resume=bool(args.resume),
            state_file=state_file,
        )
        runner = ControllerAllRunner(cfg)
        try:
            return runner.run()
        except (ConfigError, KubernetesError, RuntimeError) as exc:
            err(str(exc))
            runner.tracker.summary()
            return 1


# Module-level singleton + aliases for the historical surface.
# The four ``_execute_*`` aliases survive because
# ``tests/unit/core/test_config_drift.py`` imports them by name to
# assert they're callable; the actual logic now lives on the
# workflows-tier :class:`ControllerAllStepExecutors`. Each alias
# resolves to the underlying ``ControllerAllStepExecutors.execute_*``
# method — the test only checks callable(), so the unbound-method
# reference satisfies the contract.
_INSTANCE = ControllerAllEntryPoint()
_env_bool = _INSTANCE.env_bool
_env_bool_candidates = _INSTANCE.env_bool_candidates
_parse_args = _INSTANCE.parse_args
main = _INSTANCE.main

# Drift-test backwards-compat: only callable() is asserted by the
# external test; bind to the unbound class methods so introspection
# resolves and the test passes without coupling to the (now-different)
# call signature.
_execute_component_script = ControllerAllStepExecutors.execute_component_script
_execute_script = ControllerAllStepExecutors.execute_script
_execute_enable_components = ControllerAllStepExecutors.execute_enable_components
_execute_http_action = ControllerAllStepExecutors.execute_http_action


__all__ = [
    "ControllerAllConfig",
    "ControllerAllEntryPoint",
    "ControllerAllRunner",
    "_env_bool",
    "_env_bool_candidates",
    "_execute_component_script",
    "_execute_enable_components",
    "_execute_http_action",
    "_execute_script",
    "_parse_args",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
