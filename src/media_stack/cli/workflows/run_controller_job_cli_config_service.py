"""RunControllerJobCliConfigService — Facade composing the bootstrap-job CLI parser.

Phase 3c refactor (ADR-0015). Pre-Phase-3c this class was a god
service that owned argparse construction (~70 lines of
``build_parser``), env-bool helpers, and the top-level
``parse_run_bootstrap_job_config`` orchestration on one 240-line
class. Phase 3c split the concerns:

* :class:`CliEnvReader` (Repository) — sampled env mapping + typed
  reads. Shared with :class:`DeployCliConfigService`.
* :class:`BootstrapJobArgParserBuilder` (Builder) — the 70-line
  argparse setup with env-defaulted flags and dynamic skip-flag
  injection.
* :class:`RunControllerJobCliConfigService` (this class, Facade)
  — composes the two above and exposes
  :meth:`parse_run_bootstrap_job_config` as the CLI entry point.

The Facade owns no parser-construction logic; it delegates to the
Builder. Its remaining responsibility is the "parse the args +
assemble the typed RunBootstrapJobConfig dataclass" step that
binds env reads to typed config fields, plus the SKIP_* env-var
sweep that fills in phase-skip flags the operator named via env
instead of CLI.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:  # pragma: no cover - import compatibility shim.
    sys.path.insert(0, str(SCRIPTS_ROOT))

from media_stack.core.defaults import default_controller_image  # noqa: E402
from media_stack.core.exceptions import ConfigError  # noqa: E402

from media_stack.services.controller_component_resolver import (  # noqa: E402
    PhaseSkipFlagSpec,
    normalize_flag_token,
    resolve_bootstrap_component_plan,
    resolve_phase_skip_flag_specs,
)
from media_stack.cli.workflows.bootstrap_job_arg_parser_builder import (  # noqa: E402
    BootstrapJobArgParserBuilder,
)
from media_stack.cli.workflows.cli_env_reader import CliEnvReader  # noqa: E402


@dataclass(frozen=True)
class RunBootstrapJobConfig:
    namespace: str
    timeout_raw: str
    heartbeat_interval: int
    job_log_tail_lines: int
    alert_webhook_url: str
    prepare_host_root: str
    ingress_name: str
    bootstrap_runner_image: str
    root_dir: Path
    config_file: Path
    selected_apps: str = ""
    internet_exposed: bool = False
    route_strategy: str = "subdomain"
    ingress_domain: str = "local"
    app_gateway_host: str = ""
    app_path_prefix: str = "/app"
    media_server_direct_host: str = ""
    preconfigure_api_keys: bool = True
    apply_initial_preferences: bool = True
    auto_download_content: bool = False
    bootstrap_profile_file: str = ""
    phase_skip_flags: dict[str, bool] = field(default_factory=dict)

    @property
    def timeout_seconds(self) -> int:
        raw = self.timeout_raw.strip()
        match = re.match(r"^(\d+)([smh]?)$", raw)
        if not match:
            return 600
        num = int(match.group(1))
        unit = match.group(2)
        if unit == "h":
            return num * 3600
        if unit in ("m", ""):
            return num * 60
        if unit == "s":
            return num
        return 600

    @property
    def effective_phase_skip_flags(self) -> dict[str, bool]:
        return {
            str(key).strip().lower(): bool(value)
            for key, value in (self.phase_skip_flags or {}).items()
            if str(key).strip()
        }


class RunControllerJobCliConfigService:
    """Facade: parses the bootstrap-job CLI into :class:`RunBootstrapJobConfig`.

    Composes :class:`CliEnvReader` (env reads) and
    :class:`BootstrapJobArgParserBuilder` (argparse construction);
    owns the post-parse step that builds the typed config dataclass
    from CLI args + env values.

    Backward-compat: the legacy ``env_bool`` / ``env_bool_candidates``
    methods are kept as one-line delegations to the env reader so
    existing module-level aliases (``env_bool``,
    ``env_bool_candidates``) continue to work.
    """

    def __init__(
        self,
        env: dict[str, str] | None = None,
        *,
        env_reader: CliEnvReader | None = None,
        parser_builder: BootstrapJobArgParserBuilder | None = None,
    ) -> None:
        # Two construction modes:
        #   1. Default (production): pass env_reader=None and we
        #      build a CliEnvReader from os.environ. Tests can also
        #      pass env={...} as a shorthand.
        #   2. Test fixture: pass a pre-built env_reader (and
        #      optionally parser_builder) for full isolation.
        self._env = env_reader or CliEnvReader(env=env)
        self._parser_builder = parser_builder or BootstrapJobArgParserBuilder(self._env)

    # -- legacy thin-shim env helpers (delegate to CliEnvReader) ----------

    def env_bool(self, name: str, default: bool = False) -> bool:
        return self._env.boolean(name, default)

    def env_bool_candidates(self, names: tuple[str, ...], default: bool = False) -> bool:
        return self._env.boolean_candidates(names, default)

    # -- argparse construction (delegates to Builder) ---------------------

    def build_parser(
        self,
        root_dir: Path,
        *,
        skip_specs: tuple[PhaseSkipFlagSpec, ...] = (),
    ) -> argparse.ArgumentParser:
        return self._parser_builder.build(root_dir, skip_specs=skip_specs)

    # -- top-level entry point: parse args + assemble typed config --------

    def parse_run_bootstrap_job_config(
        self, argv: list[str] | None, *, root_dir: Path
    ) -> RunBootstrapJobConfig:
        skip_specs = self._discover_skip_specs(argv, root_dir=root_dir)
        parser = self._parser_builder.build(root_dir, skip_specs=skip_specs)
        args = parser.parse_args(argv)
        phase_skip_flags = self._collect_phase_skip_flags(args, skip_specs)
        return self._build_config(args, root_dir=root_dir, phase_skip_flags=phase_skip_flags)

    # -- private helpers (one concern each, all instance methods) ---------

    def _discover_skip_specs(
        self,
        argv: list[str] | None,
        *,
        root_dir: Path,
    ) -> tuple[PhaseSkipFlagSpec, ...]:
        """Pre-parse just enough of argv to find the config file, then
        ask the controller component resolver which phase-skip flags
        that config supports. Two-stage parsing because the skip-flag
        set is data-driven from the config we haven't loaded yet."""
        default_config = str(root_dir / "contracts" / "media-stack.config.json")
        pre_parser = argparse.ArgumentParser(add_help=False)
        pre_parser.add_argument("config_file", nargs="?", default=default_config)
        pre_args, _ = pre_parser.parse_known_args(argv)
        config_file = Path(str(pre_args.config_file))
        loaded_cfg: dict[str, object] = {}
        if config_file.exists():
            try:
                loaded_cfg = resolve_bootstrap_component_plan(config_file).config
            except ConfigError:
                loaded_cfg = {}
        return resolve_phase_skip_flag_specs(loaded_cfg, pipeline="bootstrap_job")

    def _collect_phase_skip_flags(
        self,
        args: argparse.Namespace,
        skip_specs: tuple[PhaseSkipFlagSpec, ...],
    ) -> dict[str, bool]:
        """Build the phase_skip_flags dict from CLI args + SKIP_* env sweep.

        Two sources OR'd together: any --skip-<phase> CLI flag, plus
        any ``SKIP_*`` env var the operator set that names a
        recognised phase via :func:`normalize_flag_token`.
        """
        flags = {
            spec.key: bool(getattr(args, f"phase_skip_{spec.key}", False))
            for spec in skip_specs
        }
        for env_name in self._env.keys():
            if not str(env_name).upper().startswith("SKIP_"):
                continue
            key = normalize_flag_token(env_name)
            if not key:
                continue
            flags[key] = bool(
                flags.get(key, False) or self._env.boolean(env_name, False),
            )
        return flags

    def _build_config(
        self,
        args: argparse.Namespace,
        *,
        root_dir: Path,
        phase_skip_flags: dict[str, bool],
    ) -> RunBootstrapJobConfig:
        """Assemble the frozen RunBootstrapJobConfig from parsed args +
        env values. The string ``or`` fallbacks preserve behaviour for
        the case where argparse received an empty string from an env-
        defaulted flag (the env value was '' rather than absent)."""
        return RunBootstrapJobConfig(
            namespace=str(args.namespace).strip() or "media-stack",
            timeout_raw=str(args.timeout).strip() or "10m",
            heartbeat_interval=max(1, int(args.heartbeat_interval)),
            job_log_tail_lines=max(1, int(args.job_log_tail_lines)),
            alert_webhook_url=str(args.alert_webhook_url).strip(),
            prepare_host_root=str(args.prepare_host_root).strip() or "/srv/media-stack",
            ingress_name=str(args.ingress_name).strip() or "media-stack-ingress",
            bootstrap_runner_image=str(args.bootstrap_runner_image).strip()
            or default_controller_image(),
            root_dir=root_dir,
            config_file=Path(str(args.config_file)),
            selected_apps=self._env_str("SELECTED_APPS"),
            internet_exposed=self._env.boolean_candidates(("INTERNET_EXPOSED",), False),
            route_strategy=self._env_str("ROUTE_STRATEGY", default="subdomain").lower()
            or "subdomain",
            ingress_domain=self._env_str("INGRESS_DOMAIN", default="local").lower() or "local",
            app_gateway_host=self._env_str("APP_GATEWAY_HOST"),
            app_path_prefix=self._env_str("APP_PATH_PREFIX", default="/app") or "/app",
            media_server_direct_host=self._env_str("MEDIA_SERVER_DIRECT_HOST"),
            preconfigure_api_keys=self._env.boolean_candidates(
                ("PRECONFIGURE_API_KEYS",), True,
            ),
            apply_initial_preferences=self._env.boolean_candidates(
                ("APPLY_INITIAL_PREFERENCES", "FULLY_PRECONFIGURED"), True,
            ),
            auto_download_content=self._env.boolean_candidates(
                ("AUTO_DOWNLOAD_CONTENT",), False,
            ),
            bootstrap_profile_file=self._env_str("BOOTSTRAP_PROFILE_FILE"),
            phase_skip_flags=phase_skip_flags,
        )

    def _env_str(self, name: str, *, default: str = "") -> str:
        """Stripped string view of env[name], default when absent."""
        value = self._env.value(name)
        return value if value is not None else default


_INSTANCE = RunControllerJobCliConfigService()

env_bool = _INSTANCE.env_bool
env_bool_candidates = _INSTANCE.env_bool_candidates
build_parser = _INSTANCE.build_parser
parse_run_bootstrap_job_config = _INSTANCE.parse_run_bootstrap_job_config
