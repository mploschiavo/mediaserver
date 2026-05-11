"""BootstrapJobArgParserBuilder — Builder pattern for the bootstrap-job argparse.

The ``run_controller_job`` CLI takes ~10 typed flags plus N
dynamic ``--skip-<phase>`` flags resolved from the bootstrap
config's phase plan. The argparse setup is ~70 lines: every flag
has a default that reads from env (via :class:`CliEnvReader`),
a type coercion, and help text. Pre-Phase-3c this lived inline on
:meth:`RunControllerJobCliConfigService.build_parser`, making
that class responsible for both the parser shape AND the
top-level "parse + assemble RunBootstrapJobConfig" orchestration.

Builder pattern: this class owns the argparse construction.
Callers ask for a parser configured with the right defaults + the
dynamic skip flags they care about, and they get back a ready-to-
use :class:`argparse.ArgumentParser`. The Facade
(:class:`RunControllerJobCliConfigService`) composes this Builder
with :class:`CliEnvReader` and uses both to build the final
:class:`RunBootstrapJobConfig` from CLI args.

Constructor-injects :class:`CliEnvReader` so every env-derived
argparse default reads through the same Repository the Facade
uses for its post-parse env reads — no second env-mapping copy,
no skew between the parser's default and the Facade's read.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from media_stack.core.defaults import default_controller_image
from media_stack.services.controller_component_resolver import (
    PhaseSkipFlagSpec,
)
from media_stack.cli.workflows.cli_env_reader import CliEnvReader


class BootstrapJobArgParserBuilder:
    """Builder: configure the bootstrap-job argparse with env-defaulted flags."""

    def __init__(self, env_reader: CliEnvReader) -> None:
        self._env = env_reader

    def build(
        self,
        root_dir: Path,
        *,
        skip_specs: tuple[PhaseSkipFlagSpec, ...] = (),
    ) -> argparse.ArgumentParser:
        """Return a configured parser. Adds one ``--skip-<phase>`` flag per spec.

        Each phase-skip flag's default reads from the spec's env-var
        candidates via the injected CliEnvReader, so operators can
        flip SKIP_* env vars instead of CLI flags and the parser
        picks them up.
        """
        parser = argparse.ArgumentParser(
            description=(
                "Run media-stack bootstrap job.\n\n"
                "Usage:\n"
                "  bin/run-bootstrap-job.sh [CONFIG_FILE]"
            ),
            formatter_class=argparse.RawTextHelpFormatter,
        )
        parser.add_argument(
            "config_file",
            nargs="?",
            default=str(root_dir / "contracts" / "media-stack.config.json"),
            help="Bootstrap JSON file path.",
        )
        parser.add_argument(
            "--namespace",
            default=self._env_or("NAMESPACE", "media-stack"),
            help="Kubernetes namespace (env: NAMESPACE).",
        )
        parser.add_argument(
            "--timeout",
            default=self._env_or("TIMEOUT", "10m"),
            help="Wait timeout, e.g. 600s, 10m, 1h (env: TIMEOUT).",
        )
        parser.add_argument(
            "--heartbeat-interval",
            type=int,
            default=max(1, int(self._env_or("HEARTBEAT_INTERVAL", "15"))),
            help="Heartbeat seconds while waiting for job completion.",
        )
        parser.add_argument(
            "--job-log-tail-lines",
            type=int,
            default=max(1, int(self._env_or("JOB_LOG_TAIL_LINES", "120"))),
            help="Tail lines to print from bootstrap job logs.",
        )
        parser.add_argument(
            "--prepare-host-root",
            default=self._env_or("PREPARE_HOST_ROOT", "/srv/media-stack"),
            help="Host root used in manifest overrides.",
        )
        parser.add_argument(
            "--ingress-name",
            default=self._env_or("INGRESS_NAME", "media-stack-ingress"),
            help="Ingress to read hosts from.",
        )
        parser.add_argument(
            "--bootstrap-runner-image",
            default=default_controller_image(),
            help="Bootstrap runner container image.",
        )
        parser.add_argument(
            "--alert-webhook-url",
            default=self._env_or("ALERT_WEBHOOK_URL", ""),
            help="Optional webhook for status notifications.",
        )
        for spec in skip_specs:
            parser.add_argument(
                *spec.option_strings,
                dest=f"phase_skip_{spec.key}",
                action="store_true",
                default=self._env.boolean_candidates(spec.env_vars, False),
                help=spec.help,
            )
        return parser

    def _env_or(self, name: str, default: str) -> str:
        """Env value with a non-None default (matches the old
        ``os.environ.get(name, default)`` shape used by argparse
        defaults — :class:`CliEnvReader.value` returns ``None`` for
        missing/blank, which argparse can't accept as a default)."""
        value = self._env.value(name)
        return value if value is not None else default


__all__ = ["BootstrapJobArgParserBuilder"]
