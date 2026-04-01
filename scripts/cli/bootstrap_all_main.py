#!/usr/bin/env python3
"""Python bootstrap-all orchestration entrypoint.

Media Automation Stack by Matthew Loschiavo:
https://matthewloschiavo.com
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.exceptions import ConfigError, KubernetesError
from core.kube import KubernetesClient
from core.state_store import CheckpointStateStore

from cli.bootstrap_component_resolver import (
    BootstrapComponentPlan,
    BootstrapPhasePlanStep,
    PhaseSkipFlagSpec,
    evaluate_phase_condition,
    normalize_flag_token,
    resolve_bootstrap_component_plan,
    resolve_bootstrap_enable_components,
    resolve_component_deployment_name,
    resolve_component_manifest_path,
    resolve_phase_skip_flag_specs,
    resolve_pipeline_components,
    resolve_pipeline_phase_plan,
    resolve_runner_phase_script,
)


def ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def info(message: str) -> None:
    print(f"[{ts()}] [INFO] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[{ts()}] [WARN] {message}", file=sys.stderr, flush=True)


def err(message: str) -> None:
    print(f"[{ts()}] [ERR] {message}", file=sys.stderr, flush=True)


@dataclass
class PhaseTracker:
    run_start_epoch: int = field(default_factory=lambda: int(time.time()))
    current_phase: str = ""
    current_start: int = 0
    names: list[str] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    seconds: list[int] = field(default_factory=list)

    def start(self, phase_name: str) -> None:
        self.current_phase = phase_name
        self.current_start = int(time.time())
        info(f"[PHASE] START: {phase_name}")

    def end(self, result: str) -> None:
        now = int(time.time())
        if self.current_phase:
            elapsed = now - self.current_start
            self.names.append(self.current_phase)
            self.results.append(result)
            self.seconds.append(elapsed)
            if result == "ok":
                info(f"[PHASE] DONE: {self.current_phase} ({elapsed}s)")
            elif result == "skipped":
                info(f"[PHASE] SKIP: {self.current_phase} ({elapsed}s)")
            else:
                warn(f"[PHASE] FAIL: {self.current_phase} ({elapsed}s)")
        self.current_phase = ""
        self.current_start = 0

    def summary(self) -> None:
        total = int(time.time()) - self.run_start_epoch
        info(f"Phase Summary (total {total}s)")
        if not self.names:
            info("  (no phases recorded)")
            return
        for idx, name in enumerate(self.names):
            info(f"  {name} => {self.results[idx]} ({self.seconds[idx]}s)")


@dataclass(frozen=True)
class BootstrapAllConfig:
    root_dir: Path
    config_file: Path
    namespace: str
    enable_components: bool
    secret_name: str
    prepare_host_root: str
    phase_skip_flags: dict[str, bool]
    resume: bool
    state_file: Path


class BootstrapAllRunner:
    def __init__(self, cfg: BootstrapAllConfig) -> None:
        self.cfg = cfg
        self.kube = KubernetesClient.from_environment()
        self.tracker = PhaseTracker()
        self.state = CheckpointStateStore(cfg.state_file)
        self._plan: BootstrapComponentPlan | None = None
        self.state.load()

    def _run_script(self, script_name: str, *args: str, env: dict[str, str] | None = None) -> None:
        script_path = self.cfg.root_dir / "scripts" / script_name
        call_env = dict(os.environ)
        if env:
            call_env.update({k: str(v) for k, v in env.items()})
        proc = subprocess.run(
            ["bash", str(script_path), *list(args)],
            cwd=str(self.cfg.root_dir),
            env=call_env,
            check=False,
            text=True,
            capture_output=True,
        )
        if proc.stdout.strip():
            print(proc.stdout.rstrip())
        if proc.stderr.strip():
            print(proc.stderr.rstrip(), file=sys.stderr)
        if proc.returncode != 0:
            raise RuntimeError(
                f"{script_name} failed ({proc.returncode}): "
                f"{' '.join(shlex.quote(x) for x in [str(script_path), *args])}"
            )

    def _component_plan(self) -> BootstrapComponentPlan:
        if self._plan is None:
            self._plan = resolve_bootstrap_component_plan(self.cfg.config_file)
        return self._plan

    def _phase_script(self, phase_key: str, technology: str) -> str:
        return resolve_runner_phase_script(
            self._component_plan().config,
            phase_key=phase_key,
            technology=technology,
            aliases=self._component_plan().aliases,
        )

    def _skip_phase(self, flag_key: str) -> bool:
        token = normalize_flag_token(flag_key)
        if not token:
            return False
        return bool(self.cfg.phase_skip_flags.get(token, False))

    def _render_template_value(
        self,
        value: object,
        *,
        component_key: str = "",
        component_technology: str = "",
    ) -> str:
        text = str(value or "")
        tokens = {
            "$namespace": self.cfg.namespace,
            "$prepare_host_root": self.cfg.prepare_host_root,
            "$secret_name": self.cfg.secret_name,
            "$config_file": str(self.cfg.config_file),
            "$component_key": component_key,
            "$component": component_technology,
        }
        for token, token_value in tokens.items():
            text = text.replace(token, str(token_value))
        return text

    def _format_phase_name(
        self,
        template: str,
        *,
        component_key: str = "",
        component_technology: str = "",
    ) -> str:
        raw = str(template or "").strip()
        if not raw:
            return ""
        out = raw.replace("{component_key}", component_key)
        out = out.replace("{component|unbound}", component_technology or "unbound")
        out = out.replace("{component}", component_technology)
        return out

    def _manifest_overrides(self, text: str) -> str:
        out = re.sub(
            r"namespace:\s*media-stack\b",
            f"namespace: {self.cfg.namespace}",
            text,
        )
        out = re.sub(
            r"name:\s*media-stack\s*$", f"name: {self.cfg.namespace}", out, flags=re.MULTILINE
        )
        out = out.replace("/srv/media-stack", self.cfg.prepare_host_root)
        return out

    def _apply_manifest_file(self, manifest_path: Path, *, component: str) -> None:
        if not manifest_path.is_file():
            raise ConfigError(f"Component manifest not found for '{component}': {manifest_path}")
        patched_text = self._manifest_overrides(manifest_path.read_text(encoding="utf-8"))
        from tempfile import TemporaryDirectory

        prefix_component = re.sub(r"[^a-z0-9-]+", "-", str(component or "").lower()).strip("-")
        prefix_component = prefix_component or "component"
        with TemporaryDirectory(prefix=f"media-stack-{prefix_component}-") as tmp:
            patched = Path(tmp) / manifest_path.name
            patched.write_text(patched_text, encoding="utf-8")
            result = self.kube.run(["apply", "-f", str(patched)], check=False)
            if result.stdout.strip():
                print(result.stdout.rstrip())
            if result.stderr.strip():
                print(result.stderr.rstrip(), file=sys.stderr)
            if result.returncode != 0:
                raise KubernetesError(result.stderr or result.stdout)

    def _enable_component_deployment(self, component: str) -> None:
        plan = self._component_plan()
        component_manifest = resolve_component_manifest_path(
            plan.config,
            component=component,
            aliases=plan.aliases,
        )
        manifest_path = (self.cfg.root_dir / component_manifest).resolve()
        if not manifest_path.is_file():
            raise ConfigError(f"Component manifest not found for '{component}': {manifest_path}")

        deployment_name = resolve_component_deployment_name(
            plan.config,
            component=component,
            aliases=plan.aliases,
        )
        self._apply_manifest_file(manifest_path, component=component)
        self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "scale",
                f"deploy/{deployment_name}",
                "--replicas=1",
            ]
        )
        self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "rollout",
                "status",
                f"deploy/{deployment_name}",
                "--timeout=10m",
            ]
        )

    def _run_phase(
        self,
        name: str,
        action: Callable[[], None],
        *,
        enabled: bool = True,
    ) -> None:
        self.tracker.start(name)
        if not enabled:
            self.tracker.end("skipped")
            self.state.mark_phase(name, "skipped")
            return
        if self.cfg.resume and self.state.is_phase_done(name):
            info(f"Resume checkpoint: skipping already-completed phase '{name}'.")
            self.tracker.end("skipped")
            return
        try:
            action()
            self.tracker.end("ok")
            self.state.mark_phase(name, "ok")
        except Exception as exc:
            self.tracker.end("failed")
            self.state.mark_phase(name, "failed", error=str(exc))
            raise

    def run(self) -> int:
        if not self.cfg.config_file.exists():
            raise ConfigError(f"Config file not found: {self.cfg.config_file}")

        plan = self._component_plan()
        phase_plan = resolve_pipeline_phase_plan(
            plan.config,
            pipeline="bootstrap_all",
        )
        adapter_hooks = plan.config.get("adapter_hooks")
        runner_phase_scripts = (
            (adapter_hooks or {}).get("runner_phase_scripts")
            if isinstance(adapter_hooks, dict)
            else {}
        )
        configured_phase_keys = (
            tuple(str(key) for key in runner_phase_scripts.keys())
            if isinstance(runner_phase_scripts, dict)
            else ()
        )

        components: dict[str, str] = resolve_pipeline_components(
            plan.config,
            pipeline="bootstrap_all",
            aliases=plan.aliases,
            role_bindings=plan.role_bindings,
        )

        def _resolve_component_technology(step: BootstrapPhasePlanStep) -> tuple[str, str]:
            params = dict(step.params or {})
            component_key = str(params.get("component") or "").strip()
            if component_key:
                token = str(components.get(component_key) or "").strip()
                if token:
                    return component_key, token

            binding_key = str(params.get("binding") or "").strip()
            if binding_key:
                token = str(plan.role_bindings.get(binding_key) or "").strip()
                if token:
                    return component_key or binding_key, token

            technology = str(params.get("technology") or "").strip()
            if technology:
                return component_key or technology, technology

            return component_key, ""

        for step in phase_plan:
            params = dict(step.params or {})
            operation = str(step.operation or "").strip()
            action = str(params.get("action") or "").strip().lower()
            if operation != "run" or action != "component_script":
                continue
            key, technology = _resolve_component_technology(step)
            if key and key not in components:
                components[key] = technology

        component_context: dict[str, dict[str, object]] = {}
        for component_key, technology in components.items():
            script_map: dict[str, str] = {}
            for phase_key in configured_phase_keys:
                script_map[phase_key] = self._phase_script(phase_key, technology)
            selected_client = plan.technology_settings.get(technology)
            component_context[component_key] = {
                "technology": str(technology or "").strip(),
                "scripts": script_map,
                "selected": dict(selected_client) if isinstance(selected_client, dict) else {},
            }

        phase_context: dict[str, object] = {
            "config": plan.config,
            "bindings": dict(plan.role_bindings),
            "components": component_context,
            "flags": {
                "enable_components": self.cfg.enable_components,
            },
        }

        def _phase_enabled(step: BootstrapPhasePlanStep) -> bool:
            enabled = bool(step.enabled) and evaluate_phase_condition(
                step.when, context=phase_context
            )
            if enabled and step.skip_flag and self._skip_phase(step.skip_flag):
                enabled = False
            return enabled

        def _phase_name(default_name: str, step: BootstrapPhasePlanStep) -> str:
            return step.phase_name or default_name

        def _resolve_step_action(step: BootstrapPhasePlanStep) -> str:
            operation = str(step.operation or "").strip()
            if operation != "run":
                raise ConfigError(
                    "Unknown bootstrap-all phase operation "
                    f"'{operation}' in adapter_hooks.bootstrap_all.phase_plan. "
                    "Supported operation: 'run'."
                )
            params = dict(step.params or {})
            action = str(params.get("action") or "").strip().lower()
            if not action:
                raise ConfigError(
                    "bootstrap_all phase operation 'run' requires params.action "
                    "in adapter_hooks.bootstrap_all.phase_plan."
                )
            return action

        def _resolve_rendered_args(
            *,
            raw_args: object,
            component_key: str = "",
            component_technology: str = "",
        ) -> list[str]:
            args: list[str] = []
            if raw_args is None:
                return args
            if not isinstance(raw_args, list):
                raise ConfigError("Phase params.args must be an array when provided.")
            for value in raw_args:
                args.append(
                    self._render_template_value(
                        value,
                        component_key=component_key,
                        component_technology=component_technology,
                    )
                )
            return args

        def _resolve_rendered_env(
            *,
            raw_env: object,
            component_key: str = "",
            component_technology: str = "",
        ) -> dict[str, str]:
            env: dict[str, str] = {}
            if raw_env is None:
                return env
            if not isinstance(raw_env, dict):
                raise ConfigError("Phase params.env must be an object/map when provided.")
            for key, value in raw_env.items():
                env_key = str(key or "").strip()
                if not env_key:
                    continue
                env[env_key] = self._render_template_value(
                    value,
                    component_key=component_key,
                    component_technology=component_technology,
                )
            return env

        def _run_component_script_step(step: BootstrapPhasePlanStep) -> None:
            params = dict(step.params or {})
            component_key, component_technology = _resolve_component_technology(step)
            enabled = _phase_enabled(step)
            if enabled and not component_key:
                raise ConfigError(
                    "bootstrap_all run action 'component_script' requires "
                    "params.component, params.binding, or params.technology."
                )
            if enabled and not component_technology:
                raise ConfigError(
                    "bootstrap_all run action 'component_script' could not resolve technology "
                    f"for component '{component_key}'. Check adapter_hooks.bootstrap_all.components "
                    "and technology_bindings."
                )
            script_phase = str(params.get("script_phase") or "").strip()
            if not script_phase:
                raise ConfigError(
                    "bootstrap_all run action 'component_script' requires params.script_phase."
                )
            script_name = self._phase_script(script_phase, component_technology)
            if enabled and not script_name:
                raise ConfigError(
                    "bootstrap_all run action 'component_script' could not resolve script "
                    f"for component '{component_key or component_technology}' "
                    f"(technology='{component_technology or 'unbound'}', "
                    f"script_phase='{script_phase}'). "
                    "Declare adapter_hooks.runner_phase_scripts.<script_phase> mapping "
                    "for the bound technology."
                )

            args = _resolve_rendered_args(
                raw_args=params.get("args"),
                component_key=component_key,
                component_technology=component_technology,
            )
            env = _resolve_rendered_env(
                raw_env=params.get("env"),
                component_key=component_key,
                component_technology=component_technology,
            )

            phase_name = self._format_phase_name(
                _phase_name("", step),
                component_key=component_key,
                component_technology=component_technology,
            )
            if not phase_name:
                raise ConfigError(
                    "bootstrap_all run action 'component_script' requires non-empty phase_name."
                )
            self._run_phase(
                phase_name,
                lambda script=script_name, script_args=tuple(args), script_env=dict(
                    env
                ): self._run_script(
                    script,
                    *script_args,
                    env=script_env,
                ),
                enabled=enabled,
            )

        def _run_script_step(step: BootstrapPhasePlanStep) -> None:
            params = dict(step.params or {})
            script_name = self._render_template_value(params.get("script", ""))
            if not script_name:
                raise ConfigError("bootstrap_all run action 'script' requires params.script.")
            enabled = _phase_enabled(step)
            args = _resolve_rendered_args(raw_args=params.get("args"))
            env = _resolve_rendered_env(raw_env=params.get("env"))
            phase_name = self._format_phase_name(
                _phase_name("", step),
            )
            if not phase_name:
                raise ConfigError(
                    "bootstrap_all run action 'script' requires non-empty phase_name."
                )
            self._run_phase(
                phase_name,
                lambda script=script_name, script_args=tuple(args), script_env=dict(
                    env
                ): self._run_script(
                    script,
                    *script_args,
                    env=script_env,
                ),
                enabled=enabled,
            )

        def _run_enable_components_step(step: BootstrapPhasePlanStep) -> None:
            if not _phase_enabled(step):
                return
            components_to_enable = resolve_bootstrap_enable_components(
                plan.config,
                aliases=plan.aliases,
            )
            if not components_to_enable:
                raise ConfigError(
                    "bootstrap_all run action 'enable_components' requires a non-empty "
                    "adapter_hooks.bootstrap_all.enable_components list."
                )
            for component in components_to_enable:
                component_key_sync_script = self._phase_script(
                    "component_key_sync",
                    component,
                )
                if not component_key_sync_script:
                    raise ConfigError(
                        "bootstrap_all run action 'enable_components' could not resolve "
                        f"runner_phase_scripts.component_key_sync for component '{component}'."
                    )
                self._run_phase(
                    f"Sync component integration keys ({component})",
                    lambda script=component_key_sync_script: self._run_script(
                        script,
                        env={"NAMESPACE": self.cfg.namespace},
                    ),
                    enabled=True,
                )
                self._run_phase(
                    f"Enable component deployment ({component})",
                    lambda app=component: self._enable_component_deployment(app),
                    enabled=True,
                )

        action_handlers: dict[str, Callable[[BootstrapPhasePlanStep], None]] = {
            "component_script": _run_component_script_step,
            "script": _run_script_step,
            "enable_components": _run_enable_components_step,
        }

        for step in phase_plan:
            action = _resolve_step_action(step)
            handler = action_handlers.get(action)
            if handler is None:
                raise ConfigError(
                    "Unknown bootstrap-all run action "
                    f"'{action}' in adapter_hooks.bootstrap_all.phase_plan params.action."
                )
            handler(step)

        info("Full bootstrap complete.")
        self.tracker.summary()
        return 0


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_bool_candidates(candidates: tuple[str, ...], default: bool = False) -> bool:
    for name in candidates:
        if not str(name).strip():
            continue
        if name in os.environ:
            return _env_bool(name, default)
    return default


def _parse_args(
    argv: list[str] | None = None,
) -> tuple[argparse.Namespace, tuple[PhaseSkipFlagSpec, ...]]:
    root_dir = Path(__file__).resolve().parents[2]
    default_config = str(root_dir / "bootstrap" / "media-stack.bootstrap.json")

    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("config_file", nargs="?", default=default_config)
    pre_args, _ = pre_parser.parse_known_args(argv)

    config_file = Path(str(pre_args.config_file)).resolve()
    loaded_cfg: dict[str, object] = {}
    if config_file.exists():
        loaded_cfg = resolve_bootstrap_component_plan(config_file).config

    skip_specs = resolve_phase_skip_flag_specs(loaded_cfg, pipeline="bootstrap_all")

    parser = argparse.ArgumentParser(
        prog="scripts/bootstrap-all.sh",
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
        default=_env_bool_candidates(("ENABLE_COMPONENTS", "ENABLE_UNPACKERR"), True),
        help="Enable configured bootstrap component deployments.",
    )
    for spec in skip_specs:
        parser.add_argument(
            *spec.option_strings,
            dest=f"phase_skip_{spec.key}",
            action="store_true",
            default=_env_bool_candidates(spec.env_vars, False),
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


def main(argv: list[str] | None = None) -> int:
    args, skip_specs = _parse_args(argv)
    root_dir = Path(__file__).resolve().parents[2]
    config_file = Path(str(args.config_file)).resolve()
    state_file = (
        Path(args.state_file).resolve()
        if str(args.state_file).strip()
        else root_dir / ".state" / f"bootstrap-all-{args.namespace}.json"
    )
    cfg = BootstrapAllConfig(
        root_dir=root_dir,
        config_file=config_file,
        namespace=str(args.namespace).strip(),
        enable_components=bool(args.enable_components),
        secret_name=str(args.secret_name).strip(),
        prepare_host_root=str(args.prepare_host_root).strip(),
        phase_skip_flags={
            spec.key: bool(getattr(args, f"phase_skip_{spec.key}", False)) for spec in skip_specs
        },
        resume=bool(args.resume),
        state_file=state_file,
    )
    runner = BootstrapAllRunner(cfg)
    try:
        return runner.run()
    except (ConfigError, KubernetesError, RuntimeError) as exc:
        err(str(exc))
        runner.tracker.summary()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
