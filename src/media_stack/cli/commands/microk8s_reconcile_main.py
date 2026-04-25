#!/usr/bin/env python3
"""Reconcile manifests and rollout deployments from declarative event plan."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from media_stack.services.enums import RunnerEvent
from media_stack.core.exceptions import ConfigError, MediaStackError

from media_stack.services.controller_component_resolver import evaluate_phase_condition
from media_stack.core.cli_common import kube_cmd, repo_root_from_script_file, run_command


@dataclass(frozen=True)
class ConditionalManifestRule:
    deployment: str
    manifest_path: Path
    message: str = ""


@dataclass(frozen=True)
class ReconcilePhaseStep:
    phase_name: str
    event: RunnerEvent
    handler: str
    enabled: bool = True
    when: Any = True


@dataclass(frozen=True)
class Microk8sReconcileConfig:
    namespace: str
    wait_timeout: str
    include_optional: bool
    root_dir: Path
    optional_deployments: tuple[str, ...]
    optional_manifest_paths: tuple[Path, ...]
    conditional_manifest_rules: tuple[ConditionalManifestRule, ...]
    phase_plan: tuple[ReconcilePhaseStep, ...]


@dataclass
class Microk8sReconcileState:
    optional_deployments_present: bool = False
    rollout_failures: int = 0


def _load_reconcile_hooks(config_file: Path) -> dict[str, object]:
    if not config_file.exists():
        raise ConfigError(f"Bootstrap config not found: {config_file}")
    try:
        payload = json.loads(config_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in bootstrap config {config_file}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"Bootstrap config root must be an object: {config_file}")

    # Merge platform-specific adapter hooks (e.g. adapter-hooks.k8s.yaml)
    from media_stack.services.controller_component_resolver import _merge_platform_adapter_hooks
    payload = _merge_platform_adapter_hooks(payload, config_file.parent)

    adapter_hooks = payload.get("adapter_hooks")
    if not isinstance(adapter_hooks, dict):
        raise ConfigError("adapter_hooks must be an object in bootstrap config")

    reconcile_hooks = adapter_hooks.get("microk8s_reconcile")
    if not isinstance(reconcile_hooks, dict):
        raise ConfigError("adapter_hooks.microk8s_reconcile must be an object")
    return reconcile_hooks


def _parse_phase_plan(raw_plan: object) -> tuple[ReconcilePhaseStep, ...]:
    if not isinstance(raw_plan, list) or not raw_plan:
        raise ConfigError("adapter_hooks.microk8s_reconcile.phase_plan must be a non-empty array")
    steps: list[ReconcilePhaseStep] = []
    for idx, item in enumerate(raw_plan):
        if not isinstance(item, dict):
            raise ConfigError(
                "adapter_hooks.microk8s_reconcile.phase_plan" f"[{idx}] must be an object"
            )
        event_raw = str(item.get("event") or "").strip()
        handler = str(item.get("handler") or "").strip()
        phase_name = str(item.get("phase_name") or "").strip() or handler
        if not event_raw:
            raise ConfigError(
                "adapter_hooks.microk8s_reconcile.phase_plan" f"[{idx}].event is required"
            )
        if not handler:
            raise ConfigError(
                "adapter_hooks.microk8s_reconcile.phase_plan" f"[{idx}].handler is required"
            )
        try:
            event = RunnerEvent.from_value(event_raw)
        except ValueError as exc:
            raise ConfigError(
                "adapter_hooks.microk8s_reconcile.phase_plan"
                f"[{idx}].event '{event_raw}' is not a valid RunnerEvent"
            ) from exc

        steps.append(
            ReconcilePhaseStep(
                phase_name=phase_name,
                event=event,
                handler=handler,
                enabled=bool(item.get("enabled", True)),
                when=item.get("when", True),
            )
        )
    return tuple(steps)


def parse_config(argv: list[str] | None = None) -> Microk8sReconcileConfig:
    parser = argparse.ArgumentParser(
        prog="bin/microk8s-reconcile.sh",
        description="Reconcile manifests from config-defined RECONCILE phase plan.",
    )
    parser.add_argument("--include-optional", action="store_true", default=False)
    args = parser.parse_args(argv)

    root_dir = repo_root_from_script_file(__file__)
    config_file = Path(
        str(os.environ.get("CONFIG_FILE") or root_dir / "contracts" / "media-stack.config.json")
    ).resolve()
    hooks = _load_reconcile_hooks(config_file)

    raw_optional_deployments = hooks.get("optional_deployments")
    if not isinstance(raw_optional_deployments, list):
        raise ConfigError("adapter_hooks.microk8s_reconcile.optional_deployments must be an array")
    optional_deployments = tuple(
        str(item or "").strip() for item in raw_optional_deployments if str(item or "").strip()
    )

    raw_optional_manifests = hooks.get("optional_manifest_paths")
    if not isinstance(raw_optional_manifests, list):
        raise ConfigError(
            "adapter_hooks.microk8s_reconcile.optional_manifest_paths must be an array"
        )
    optional_manifest_paths = tuple(
        (root_dir / str(item or "").strip()).resolve()
        for item in raw_optional_manifests
        if str(item or "").strip()
    )

    raw_conditional_manifests = hooks.get("conditional_manifests") or []
    if not isinstance(raw_conditional_manifests, list):
        raise ConfigError("adapter_hooks.microk8s_reconcile.conditional_manifests must be an array")
    conditional_manifest_rules: list[ConditionalManifestRule] = []
    for idx, item in enumerate(raw_conditional_manifests):
        if not isinstance(item, dict):
            raise ConfigError(
                "adapter_hooks.microk8s_reconcile.conditional_manifests"
                f"[{idx}] must be an object"
            )
        deployment = str(item.get("deployment") or "").strip()
        manifest = str(item.get("manifest_path") or "").strip()
        message = str(item.get("message") or "").strip()
        if not deployment or not manifest:
            raise ConfigError(
                "adapter_hooks.microk8s_reconcile.conditional_manifests"
                f"[{idx}] requires deployment and manifest_path"
            )
        conditional_manifest_rules.append(
            ConditionalManifestRule(
                deployment=deployment,
                manifest_path=(root_dir / manifest).resolve(),
                message=message,
            )
        )

    phase_plan = _parse_phase_plan(hooks.get("phase_plan"))

    return Microk8sReconcileConfig(
        namespace=os.environ.get("NAMESPACE", "media-stack").strip() or "media-stack",
        wait_timeout=os.environ.get("WAIT_TIMEOUT", "20m").strip() or "20m",
        include_optional=bool(args.include_optional),
        root_dir=root_dir,
        optional_deployments=optional_deployments,
        optional_manifest_paths=optional_manifest_paths,
        conditional_manifest_rules=tuple(conditional_manifest_rules),
        phase_plan=phase_plan,
    )


class Microk8sReconcileRunner:
    def __init__(self, cfg: Microk8sReconcileConfig) -> None:
        self.cfg = cfg
        self.kubectl = kube_cmd()
        self.state = Microk8sReconcileState()

    def _run(self, args: list[str], *, check: bool = True):
        return run_command([*self.kubectl, *args], check=check)

    def _get_optional_deployments(self) -> list[str]:
        proc = self._run(
            ["-n", self.cfg.namespace, "get", "deploy", "-o", "name"],
            check=False,
        )
        if proc.returncode != 0:
            return []
        allowed = {str(name).strip() for name in self.cfg.optional_deployments if str(name).strip()}
        names: list[str] = []
        for row in (proc.stdout or "").splitlines():
            token = row.strip()
            if not token:
                continue
            short = token.removeprefix("deploy/")
            if short in allowed:
                names.append(short)
        return names

    def _condition_context(self) -> dict[str, object]:
        return {
            "flags": {
                "include_optional": self.cfg.include_optional,
            },
            "state": {
                "optional_deployments_present": self.state.optional_deployments_present,
                "rollout_failures": self.state.rollout_failures,
            },
            "config": {
                "namespace": self.cfg.namespace,
                "wait_timeout": self.cfg.wait_timeout,
            },
        }

    def _handle_apply_base_kustomize(self) -> None:
        k8s_dir = self.cfg.root_dir / "k8s"
        if not k8s_dir.is_dir():
            raise ConfigError(f"k8s directory not found: {k8s_dir}")
        print(f"[INFO] Applying core manifests from {k8s_dir}")
        self._run(["apply", "-k", str(k8s_dir)])

    def _handle_apply_optional_manifests(self) -> None:
        for manifest_path in self.cfg.optional_manifest_paths:
            print(f"[INFO] Applying optional manifests from {manifest_path}")
            self._run(["apply", "-f", str(manifest_path)])

    def _handle_apply_conditional_manifests(self) -> None:
        for rule in self.cfg.conditional_manifest_rules:
            deployment_probe = self._run(
                ["-n", self.cfg.namespace, "get", "deploy", rule.deployment],
                check=False,
            )
            if deployment_probe.returncode != 0:
                continue
            if not rule.manifest_path.is_file():
                raise ConfigError(
                    "Conditional manifest configured but file not found: " f"{rule.manifest_path}"
                )
            if rule.message:
                print(f"[INFO] {rule.message}")
            self._run(["apply", "-f", str(rule.manifest_path)])

    def _handle_restart_all_deployments(self) -> None:
        print(f"[INFO] Restarting all deployments in namespace {self.cfg.namespace}")
        self._run(["-n", self.cfg.namespace, "rollout", "restart", "deploy", "--all"])

    def _handle_wait_all_rollouts(self) -> None:
        deploy_proc = self._run(
            [
                "-n",
                self.cfg.namespace,
                "get",
                "deploy",
                "-o",
                "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}",
            ],
            check=False,
        )
        deploys = [line.strip() for line in (deploy_proc.stdout or "").splitlines() if line.strip()]
        failed = 0
        for deploy in deploys:
            print(f"[INFO] Waiting for deploy/{deploy}")
            status_proc = self._run(
                [
                    "-n",
                    self.cfg.namespace,
                    "rollout",
                    "status",
                    f"deploy/{deploy}",
                    f"--timeout={self.cfg.wait_timeout}",
                ],
                check=False,
            )
            sys.stdout.write(status_proc.stdout or "")
            sys.stderr.write(status_proc.stderr or "")
            if status_proc.returncode != 0:
                print(
                    f"[WARN] deploy/{deploy} did not become ready in {self.cfg.wait_timeout}",
                    file=sys.stderr,
                )
                failed += 1
        self.state.rollout_failures = failed

    def _handle_print_pod_state(self) -> None:
        print("\n[INFO] Current pod state:")
        pods_proc = self._run(["-n", self.cfg.namespace, "get", "pods"], check=False)
        sys.stdout.write(pods_proc.stdout or "")
        sys.stderr.write(pods_proc.stderr or "")

    def _handle_fail_if_rollout_failed(self) -> None:
        if self.state.rollout_failures <= 0:
            return
        joined = " ".join(self.kubectl)
        print(
            f"\n[WARN] {self.state.rollout_failures} deployment(s) still not ready.",
            file=sys.stderr,
        )
        print("[WARN] Inspect with:", file=sys.stderr)
        print(
            f"  {joined} -n {self.cfg.namespace} get events --sort-by=.lastTimestamp | tail -n 200",
            file=sys.stderr,
        )
        print(
            f"  {joined} -n {self.cfg.namespace} logs deploy/<name> --tail=200",
            file=sys.stderr,
        )
        raise MediaStackError("One or more deployments did not become ready")

    def run(self) -> int:
        self.state.optional_deployments_present = bool(self._get_optional_deployments())

        handlers: dict[str, Callable[[], None]] = {
            "apply_base_kustomize": self._handle_apply_base_kustomize,
            "apply_optional_manifests": self._handle_apply_optional_manifests,
            "apply_conditional_manifests": self._handle_apply_conditional_manifests,
            "restart_all_deployments": self._handle_restart_all_deployments,
            "wait_all_rollouts": self._handle_wait_all_rollouts,
            "print_pod_state": self._handle_print_pod_state,
            "fail_if_rollout_failed": self._handle_fail_if_rollout_failed,
        }

        for step in self.cfg.phase_plan:
            if not step.enabled:
                continue
            if not evaluate_phase_condition(step.when, context=self._condition_context()):
                continue
            action = handlers.get(step.handler)
            if not callable(action):
                raise ConfigError(
                    "adapter_hooks.microk8s_reconcile.phase_plan references unknown handler "
                    f"'{step.handler}'"
                )
            print(f"[INFO] [{step.event.value}] {step.phase_name}")
            action()

        print("\n[OK] Reconcile complete.")
        return 0


def main(argv: list[str] | None = None) -> int:
    try:
        cfg = parse_config(argv)
        return Microk8sReconcileRunner(cfg).run()
    except (ConfigError, MediaStackError, OSError, ValueError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
