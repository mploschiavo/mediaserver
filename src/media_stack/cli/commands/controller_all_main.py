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

from media_stack.core.exceptions import ConfigError, KubernetesError
from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient
from media_stack.core.state_store import CheckpointStateStore

from media_stack.cli.workflows.controller_component_resolver import (
    ControllerComponentPlan,
    ControllerPhasePlanStep,
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


from media_stack.cli.workflows.cli_common import PhaseTracker, err, info, ts, warn  # noqa: E402


@dataclass(frozen=True)
class ControllerAllConfig:
    root_dir: Path
    config_file: Path
    namespace: str
    enable_components: bool
    secret_name: str
    prepare_host_root: str
    phase_skip_flags: dict[str, bool]
    resume: bool
    state_file: Path


class ControllerAllRunner:
    def __init__(self, cfg: ControllerAllConfig) -> None:
        self.cfg = cfg
        self.kube = KubernetesClient.from_environment()
        self.tracker = PhaseTracker()
        self.state = CheckpointStateStore(cfg.state_file)
        self._plan: ControllerComponentPlan | None = None
        self.state.load()

    def _run_script(self, script_name: str, *args: str, env: dict[str, str] | None = None) -> None:
        call_env = dict(os.environ)
        if env:
            call_env.update({k: str(v) for k, v in env.items()})

        # Python module path (e.g. "media_stack.services.apps.foo.cli.bar_main")
        if "." in script_name and not script_name.endswith(".sh"):
            cmd = [sys.executable, "-m", script_name, *list(args)]
            label = script_name
        else:
            # Legacy shell script fallback
            from media_stack.cli.workflows.controller_script_runner_service import _find_script
            script_path = _find_script(self.cfg.root_dir / "bin", script_name)
            cmd = ["bash", str(script_path), *list(args)]
            label = script_name

        proc = subprocess.run(
            cmd,
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
                f"{label} failed ({proc.returncode}): "
                f"{' '.join(shlex.quote(x) for x in cmd)}"
            )

    def _component_plan(self) -> ControllerComponentPlan:
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
            plan.config, pipeline="bootstrap_all",
        )
        configured_phase_keys = self._configured_phase_keys(plan.config)
        components: dict[str, str] = resolve_pipeline_components(
            plan.config,
            pipeline="bootstrap_all",
            aliases=plan.aliases,
            role_bindings=plan.role_bindings,
        )

        def _resolve_component_technology(step: ControllerPhasePlanStep) -> tuple[str, str]:
            return self._resolve_component_technology(step, plan, components)

        self._seed_components_from_plan(
            phase_plan, components, _resolve_component_technology,
        )
        component_context = self._build_component_context(
            components, configured_phase_keys, plan,
        )
        phase_context = self._build_phase_context(plan, component_context)

        action_handlers = self._build_action_handlers(
            plan=plan,
            components=components,
            phase_context=phase_context,
            resolve_component_technology=_resolve_component_technology,
        )
        self._dispatch_phase_plan(phase_plan, action_handlers)

        info("Full bootstrap complete.")
        self.tracker.summary()
        return 0

    def _build_action_handlers(
        self,
        *,
        plan,
        components: dict[str, str],
        phase_context: dict[str, object],
        resolve_component_technology,
    ) -> dict[str, Callable[[ControllerPhasePlanStep], None]]:
        """Bind the four ``action`` handler closures that ``run`` dispatches over.

        Isolates the closure captures (``self``, ``plan``, ``components``,
        rendering helpers) so the run method stays short and the closure
        wiring is easy to inspect in one place.
        """
        phase_enabled = self._make_phase_enabled(phase_context)
        render_args = self._render_args_closure()
        render_env = self._render_env_closure()
        phase_name = self._phase_name_closure()
        return {
            "component_script": lambda step: _execute_component_script(
                self, step, plan, components, phase_enabled, resolve_component_technology,
                render_args, render_env, phase_name,
            ),
            "script": lambda step: _execute_script(
                self, step, phase_enabled, render_args, render_env, phase_name,
            ),
            "enable_components": lambda step: _execute_enable_components(
                self, step, plan, phase_enabled,
            ),
            "http_action": lambda step: _execute_http_action(
                self, step, phase_enabled,
            ),
        }

    def _make_phase_enabled(
        self, phase_context: dict[str, object],
    ) -> Callable[[ControllerPhasePlanStep], bool]:
        """Build the phase-enabled predicate closed over ``phase_context``.

        Split out so the action-handler builder is just a wiring table
        and the conditional logic has one named home.
        """
        def _phase_enabled(step: ControllerPhasePlanStep) -> bool:
            enabled = bool(step.enabled) and evaluate_phase_condition(
                step.when, context=phase_context
            )
            if enabled and step.skip_flag and self._skip_phase(step.skip_flag):
                enabled = False
            return enabled
        return _phase_enabled

    def _render_args_closure(self):
        """Thin closure that dispatches to ``_resolve_rendered_args`` on self."""
        def _resolve_rendered_args(
            *, raw_args: object, component_key: str = "", component_technology: str = "",
        ) -> list[str]:
            return self._resolve_rendered_args(
                raw_args=raw_args, component_key=component_key,
                component_technology=component_technology,
            )
        return _resolve_rendered_args

    def _render_env_closure(self):
        """Thin closure that dispatches to ``_resolve_rendered_env`` on self."""
        def _resolve_rendered_env(
            *, raw_env: object, component_key: str = "", component_technology: str = "",
        ) -> dict[str, str]:
            return self._resolve_rendered_env(
                raw_env=raw_env, component_key=component_key,
                component_technology=component_technology,
            )
        return _resolve_rendered_env

    @staticmethod
    def _phase_name_closure():
        """Pure-function closure that picks ``step.phase_name`` or the caller's default."""
        def _phase_name(default_name: str, step: ControllerPhasePlanStep) -> str:
            return step.phase_name or default_name
        return _phase_name

    def _dispatch_phase_plan(
        self,
        phase_plan,
        action_handlers: dict[str, Callable[[ControllerPhasePlanStep], None]],
    ) -> None:
        """Walk the phase plan and invoke the matching action handler per step."""
        for step in phase_plan:
            action = self._resolve_step_action(step)
            handler = action_handlers.get(action)
            if handler is None:
                raise ConfigError(
                    "Unknown bootstrap-all run action "
                    f"'{action}' in adapter_hooks.bootstrap_all.phase_plan params.action."
                )
            handler(step)

    @staticmethod
    def _configured_phase_keys(config: dict) -> tuple[str, ...]:
        """Extract the tuple of phase keys declared under runner_phase_scripts."""
        adapter_hooks = config.get("adapter_hooks")
        runner_phase_scripts = (
            (adapter_hooks or {}).get("runner_phase_scripts")
            if isinstance(adapter_hooks, dict)
            else {}
        )
        if isinstance(runner_phase_scripts, dict):
            return tuple(str(key) for key in runner_phase_scripts.keys())
        return ()

    @staticmethod
    def _resolve_component_technology(
        step: ControllerPhasePlanStep,
        plan: "ControllerComponentPlan",
        components: dict[str, str],
    ) -> tuple[str, str]:
        """Pick the (component_key, technology) pair for a phase step.

        Precedence: explicit ``component`` wins, then ``binding`` looked
        up in role_bindings, then raw ``technology`` param.
        """
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

    @staticmethod
    def _seed_components_from_plan(
        phase_plan,
        components: dict[str, str],
        resolve_component_technology,
    ) -> None:
        """Register component_script-referenced keys even if absent from the plan."""
        for step in phase_plan:
            params = dict(step.params or {})
            operation = str(step.operation or "").strip()
            action = str(params.get("action") or "").strip().lower()
            if operation != "run" or action != "component_script":
                continue
            key, technology = resolve_component_technology(step)
            if key and key not in components:
                components[key] = technology

    def _build_component_context(
        self,
        components: dict[str, str],
        configured_phase_keys: tuple[str, ...],
        plan,
    ) -> dict[str, dict[str, object]]:
        """Per-component dict of ``technology``/``scripts``/``selected`` for templates."""
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
        return component_context

    def _build_phase_context(
        self, plan, component_context: dict[str, dict[str, object]],
    ) -> dict[str, object]:
        """Compose the context dict used by phase conditions / template renders."""
        return {
            "config": plan.config,
            "bindings": dict(plan.role_bindings),
            "components": component_context,
            "flags": {"enable_components": self.cfg.enable_components},
        }

    @staticmethod
    def _resolve_step_action(step: ControllerPhasePlanStep) -> str:
        """Validate the step shape and return the normalized action token."""
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
        self, *, raw_args: object, component_key: str = "", component_technology: str = "",
    ) -> list[str]:
        """Render each element of ``raw_args`` through the template engine."""
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
        self, *, raw_env: object, component_key: str = "", component_technology: str = "",
    ) -> dict[str, str]:
        """Render each value of ``raw_env`` through the template engine."""
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


# ---------------------------------------------------------------------------
# Step executors — extracted from ControllerAllRunner.run() closures
# ---------------------------------------------------------------------------

def _execute_component_script(
    runner: ControllerAllRunner, step: ControllerPhasePlanStep,
    plan: ControllerComponentPlan, components: dict,
    phase_enabled: Callable, resolve_tech: Callable,
    resolve_args: Callable, resolve_env: Callable, phase_name_fn: Callable,
) -> None:
    params = dict(step.params or {})
    component_key, component_technology = resolve_tech(step)
    enabled = phase_enabled(step)
    if enabled and not component_key:
        raise ConfigError("bootstrap_all run action 'component_script' requires params.component, params.binding, or params.technology.")
    if enabled and not component_technology:
        raise ConfigError(f"bootstrap_all run action 'component_script' could not resolve technology for component '{component_key}'.")
    script_phase = str(params.get("script_phase") or "").strip()
    if not script_phase:
        raise ConfigError("bootstrap_all run action 'component_script' requires params.script_phase.")
    script_name = runner._phase_script(script_phase, component_technology)
    if enabled and not script_name:
        raise ConfigError(f"bootstrap_all run action 'component_script' could not resolve script for component '{component_key or component_technology}'.")
    args = resolve_args(raw_args=params.get("args"), component_key=component_key, component_technology=component_technology)
    env = resolve_env(raw_env=params.get("env"), component_key=component_key, component_technology=component_technology)
    name = runner._format_phase_name(phase_name_fn("", step), component_key=component_key, component_technology=component_technology)
    if not name:
        raise ConfigError("bootstrap_all run action 'component_script' requires non-empty phase_name.")
    runner._run_phase(name, lambda s=script_name, a=tuple(args), e=dict(env): runner._run_script(s, *a, env=e), enabled=enabled)


def _execute_script(
    runner: ControllerAllRunner, step: ControllerPhasePlanStep,
    phase_enabled: Callable, resolve_args: Callable,
    resolve_env: Callable, phase_name_fn: Callable,
) -> None:
    params = dict(step.params or {})
    script_name = runner._render_template_value(params.get("script", ""))
    if not script_name:
        raise ConfigError("bootstrap_all run action 'script' requires params.script.")
    enabled = phase_enabled(step)
    args = resolve_args(raw_args=params.get("args"))
    env = resolve_env(raw_env=params.get("env"))
    name = runner._format_phase_name(phase_name_fn("", step))
    if not name:
        raise ConfigError("bootstrap_all run action 'script' requires non-empty phase_name.")
    runner._run_phase(name, lambda s=script_name, a=tuple(args), e=dict(env): runner._run_script(s, *a, env=e), enabled=enabled)


def _execute_enable_components(
    runner: ControllerAllRunner, step: ControllerPhasePlanStep,
    plan: ControllerComponentPlan, phase_enabled: Callable,
) -> None:
    if not phase_enabled(step):
        return
    components_to_enable = resolve_bootstrap_enable_components(plan.config, aliases=plan.aliases)
    if not components_to_enable:
        raise ConfigError("bootstrap_all run action 'enable_components' requires a non-empty enable_components list.")
    for component in components_to_enable:
        sync_script = runner._phase_script("component_key_sync", component)
        if not sync_script:
            raise ConfigError(f"Could not resolve runner_phase_scripts.component_key_sync for component '{component}'.")
        runner._run_phase(
            f"Sync component integration keys ({component})",
            lambda s=sync_script: runner._run_script(s, env={"NAMESPACE": runner.cfg.namespace}),
            enabled=True,
        )
        runner._run_phase(
            f"Enable component deployment ({component})",
            lambda app=component: runner._enable_component_deployment(app),
            enabled=True,
        )


def _execute_http_action(
    runner: ControllerAllRunner, step: ControllerPhasePlanStep,
    phase_enabled: Callable,
) -> None:
    """Trigger an action on the bootstrap service via HTTP and poll for completion."""
    if not phase_enabled(step):
        return
    params = step.params or {}
    action_name = str(params.get("action_name", "")).strip()
    svc_port = int(params.get("service_port", 9100))
    namespace = runner._render_template_value(str(params.get("namespace_var", "$namespace")), component_key="")
    if not action_name:
        raise ConfigError("http_action requires params.action_name")

    from media_stack.cli.workflows.controller_job_wait_service import ControllerJobWaitConfig, ControllerJobWaitService
    wait_svc = ControllerJobWaitService(
        cfg=ControllerJobWaitConfig(namespace=namespace, timeout_seconds=600, timeout_raw="10m", heartbeat_interval=15, service_port=svc_port),
        kube=runner.kube, info=info, warn=warn,
    )
    pod_name = wait_svc._find_bootstrap_pod()
    if not pod_name:
        raise ConfigError("Bootstrap service pod not found for http_action")
    info(f"Triggering action '{action_name}' on bootstrap service")
    trigger_result = runner.kube.run(
        ["-n", namespace, "exec", pod_name, "--", "python3", "-c",
         "import urllib.request,json; "
         f"req=urllib.request.Request('http://127.0.0.1:{svc_port}/actions/{action_name}',"
         "data=b'{}',headers={'Content-Type':'application/json'}); "
         "r=urllib.request.urlopen(req); print(r.read().decode())"],
        check=False,
    )
    if trigger_result.returncode != 0:
        raise ConfigError(f"Failed to trigger action '{action_name}': {trigger_result.stderr or trigger_result.stdout}")
    info(f"Action '{action_name}' accepted, waiting for completion...")
    wait_svc.wait_for_bootstrap_service(wait_for_action=action_name)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

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
        default=_env_bool_candidates(("ENABLE_COMPONENTS",), True),
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
    cfg = ControllerAllConfig(
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
    runner = ControllerAllRunner(cfg)
    try:
        return runner.run()
    except (ConfigError, KubernetesError, RuntimeError) as exc:
        err(str(exc))
        runner.tracker.summary()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
