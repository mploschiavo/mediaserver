from __future__ import annotations

import argparse
import os
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
    """CLI argument parsing + env-bool helpers for the controller bootstrap job.

    Env reads funnel through self._env (sampled from
    os.environ at construction or injected by tests) so method
    paths stay off the module-level mapping — the ADR-0012 /
    OS_ENVIRON_IN_METHODS_RATCHET pattern.
    """

    def __init__(self, env: dict[str, str] | None = None) -> None:
        self._env = dict(env) if env is not None else dict(os.environ)

    def env_bool(self, name: str, default: bool = False) -> bool:
        raw = self._env.get(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    def env_bool_candidates(self, names: tuple[str, ...], default: bool = False) -> bool:
        for name in names:
            token = str(name).strip()
            if not token:
                continue
            if token in self._env:
                return self.env_bool(token, default)
        return default

    def build_parser(
        self,
        root_dir: Path,
        *,
        skip_specs: tuple[PhaseSkipFlagSpec, ...] = (),
    ) -> argparse.ArgumentParser:
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
            default=self._env.get("NAMESPACE", "media-stack"),
            help="Kubernetes namespace (env: NAMESPACE).",
        )
        parser.add_argument(
            "--timeout",
            default=self._env.get("TIMEOUT", "10m"),
            help="Wait timeout, e.g. 600s, 10m, 1h (env: TIMEOUT).",
        )
        parser.add_argument(
            "--heartbeat-interval",
            type=int,
            default=max(1, int(self._env.get("HEARTBEAT_INTERVAL", "15"))),
            help="Heartbeat seconds while waiting for job completion.",
        )
        parser.add_argument(
            "--job-log-tail-lines",
            type=int,
            default=max(1, int(self._env.get("JOB_LOG_TAIL_LINES", "120"))),
            help="Tail lines to print from bootstrap job logs.",
        )
        parser.add_argument(
            "--prepare-host-root",
            default=self._env.get("PREPARE_HOST_ROOT", "/srv/media-stack"),
            help="Host root used in manifest overrides.",
        )
        parser.add_argument(
            "--ingress-name",
            default=self._env.get("INGRESS_NAME", "media-stack-ingress"),
            help="Ingress to read hosts from.",
        )
        parser.add_argument(
            "--bootstrap-runner-image",
            default=default_controller_image(),
            help="Bootstrap runner container image.",
        )
        parser.add_argument(
            "--alert-webhook-url",
            default=self._env.get("ALERT_WEBHOOK_URL", ""),
            help="Optional webhook for status notifications.",
        )
        for spec in skip_specs:
            parser.add_argument(
                *spec.option_strings,
                dest=f"phase_skip_{spec.key}",
                action="store_true",
                default=self.env_bool_candidates(spec.env_vars, False),
                help=spec.help,
            )
        return parser

    def parse_run_bootstrap_job_config(
        self, argv: list[str] | None, *, root_dir: Path
    ) -> RunBootstrapJobConfig:
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
        skip_specs = resolve_phase_skip_flag_specs(loaded_cfg, pipeline="bootstrap_job")

        parser = self.build_parser(root_dir, skip_specs=skip_specs)
        args = parser.parse_args(argv)
        phase_skip_flags = {
            spec.key: bool(getattr(args, f"phase_skip_{spec.key}", False)) for spec in skip_specs
        }
        for env_name in self._env:
            if not str(env_name).upper().startswith("SKIP_"):
                continue
            key = normalize_flag_token(env_name)
            if not key:
                continue
            phase_skip_flags[key] = bool(
                phase_skip_flags.get(key, False) or self.env_bool(env_name, False)
            )
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
            selected_apps=str(self._env.get("SELECTED_APPS", "")).strip(),
            internet_exposed=self.env_bool_candidates(("INTERNET_EXPOSED",), False),
            route_strategy=str(self._env.get("ROUTE_STRATEGY", "subdomain")).strip().lower()
            or "subdomain",
            ingress_domain=str(self._env.get("INGRESS_DOMAIN", "local")).strip().lower() or "local",
            app_gateway_host=str(self._env.get("APP_GATEWAY_HOST", "")).strip(),
            app_path_prefix=str(self._env.get("APP_PATH_PREFIX", "/app")).strip() or "/app",
            media_server_direct_host=str(self._env.get("MEDIA_SERVER_DIRECT_HOST", "")).strip(),
            preconfigure_api_keys=self.env_bool_candidates(("PRECONFIGURE_API_KEYS",), True),
            apply_initial_preferences=self.env_bool_candidates(
                ("APPLY_INITIAL_PREFERENCES", "FULLY_PRECONFIGURED"), True
            ),
            auto_download_content=self.env_bool_candidates(("AUTO_DOWNLOAD_CONTENT",), False),
            bootstrap_profile_file=str(self._env.get("BOOTSTRAP_PROFILE_FILE", "")).strip(),
            phase_skip_flags=phase_skip_flags,
        )


_INSTANCE = RunControllerJobCliConfigService()

env_bool = _INSTANCE.env_bool
env_bool_candidates = _INSTANCE.env_bool_candidates
build_parser = _INSTANCE.build_parser
parse_run_bootstrap_job_config = _INSTANCE.parse_run_bootstrap_job_config
