from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from cli.bootstrap_component_resolver import (
    BootstrapComponentPlan,
    BootstrapPhasePlanStep,
    evaluate_phase_condition,
    normalize_flag_token,
    resolve_bootstrap_component_plan,
    resolve_bootstrap_job_phase_plan,
    resolve_runner_phase_script,
)


@dataclass(frozen=True)
class BootstrapCorePhasesConfig:
    config_file: Path
    namespace: str
    prepare_host_root: str
    phase_skip_flags: dict[str, bool]


class BootstrapCorePhasesService:
    def __init__(self, cfg: BootstrapCorePhasesConfig) -> None:
        self.cfg = cfg
        self.plan: BootstrapComponentPlan = resolve_bootstrap_component_plan(self.cfg.config_file)

    def _role_binding(self, role_key: str) -> str:
        return str(self.plan.role_bindings.get(role_key) or "").strip()

    def _phase_script(self, phase_key: str, technology: str) -> str:
        return resolve_runner_phase_script(
            self.plan.config,
            phase_key=phase_key,
            technology=technology,
            aliases=self.plan.aliases,
        )

    def _skip_phase(self, flag_key: str) -> bool:
        token = normalize_flag_token(flag_key)
        if not token:
            return False
        return bool(self.cfg.phase_skip_flags.get(token, False))

    def _selected_download_client(self, role_key: str) -> dict[str, object]:
        technology = self._role_binding(role_key)
        selected = self.plan.download_clients.get(technology)
        if isinstance(selected, dict):
            return selected
        return {}

    def run(
        self,
        *,
        run_phase: Callable[..., None],
        run_script: Callable[..., None],
        resolve_bootstrap_config: Callable[[], None],
        ensure_bootstrap_pvc_prereqs: Callable[[], None],
        prime_servarr_api_keys_secret: Callable[[], None],
        prime_usenet_client_api_key_secret: Callable[[], None],
        prime_request_manager_api_key_secret: Callable[[], None],
        prime_tautulli_api_key_secret: Callable[[], None],
        update_bootstrap_configmaps: Callable[[], None],
        recreate_bootstrap_job: Callable[[], None],
        wait_for_bootstrap_job: Callable[[], None],
        print_bootstrap_job_logs: Callable[[], None],
    ) -> None:
        torrent_client = self._role_binding("torrent_client")
        usenet_client = self._role_binding("usenet_client")
        request_manager = self._role_binding("request_manager")
        torrent_script = self._phase_script("torrent_client_credentials", torrent_client)
        usenet_script = self._phase_script("usenet_client_api_access", usenet_client)
        phase_plan = resolve_bootstrap_job_phase_plan(self.plan.config)
        phase_context: dict[str, object] = {
            "config": self.plan.config,
            "bindings": {
                "torrent_client": torrent_client,
                "usenet_client": usenet_client,
                "request_manager": request_manager,
            },
            "scripts": {
                "torrent_client_credentials": torrent_script,
                "usenet_client_api_access": usenet_script,
            },
            "selected": {
                "torrent_client": self._selected_download_client("torrent_client"),
                "usenet_client": self._selected_download_client("usenet_client"),
            },
        }

        def _phase_enabled(step: BootstrapPhasePlanStep, default_enabled: bool) -> bool:
            enabled = (
                bool(step.enabled)
                and bool(default_enabled)
                and evaluate_phase_condition(step.when, context=phase_context)
            )
            if enabled and step.skip_flag and self._skip_phase(step.skip_flag):
                enabled = False
            return enabled

        def _phase_name(default_name: str, step: BootstrapPhasePlanStep) -> str:
            return step.phase_name or default_name

        for step in phase_plan:
            operation = step.operation

            if operation == "ensure_torrent_client_access":
                run_phase(
                    _phase_name(
                        f"Ensure torrent client bootstrap access ({torrent_client or 'unbound'})",
                        step,
                    ),
                    lambda: run_script(
                        torrent_script,
                        env={
                            "NAMESPACE": self.cfg.namespace,
                            "PREPARE_HOST_ROOT": self.cfg.prepare_host_root,
                        },
                    ),
                    enabled=_phase_enabled(
                        step,
                        bool(torrent_script),
                    ),
                )
                continue

            if operation == "ensure_usenet_client_access":
                run_phase(
                    _phase_name(
                        f"Ensure usenet client API access ({usenet_client or 'unbound'})",
                        step,
                    ),
                    lambda: run_script(
                        usenet_script,
                        env={"NAMESPACE": self.cfg.namespace},
                    ),
                    enabled=_phase_enabled(
                        step,
                        bool(usenet_script),
                    ),
                )
                continue

            if operation == "resolve_bootstrap_config":
                run_phase(_phase_name("Resolve bootstrap config", step), resolve_bootstrap_config)
                continue

            if operation == "ensure_bootstrap_pvc_prereqs":
                run_phase(
                    _phase_name("Ensure bootstrap PVC prerequisites", step),
                    ensure_bootstrap_pvc_prereqs,
                )
                continue

            if operation == "prime_servarr_api_keys_secret":
                run_phase(
                    _phase_name("Prime Arr API keys into secret", step),
                    prime_servarr_api_keys_secret,
                )
                continue

            if operation == "prime_usenet_client_api_key_secret":
                run_phase(
                    _phase_name(
                        f"Prime usenet client API key into secret ({usenet_client or 'unbound'})",
                        step,
                    ),
                    prime_usenet_client_api_key_secret,
                    enabled=_phase_enabled(step, True),
                )
                continue

            if operation == "prime_request_manager_api_key_secret":
                run_phase(
                    _phase_name(
                        f"Prime request manager API key into secret ({request_manager or 'unbound'})",
                        step,
                    ),
                    prime_request_manager_api_key_secret,
                    enabled=_phase_enabled(step, True),
                )
                continue

            if operation == "prime_tautulli_api_key_secret":
                run_phase(
                    _phase_name("Prime Tautulli API key into secret", step),
                    prime_tautulli_api_key_secret,
                    enabled=_phase_enabled(step, True),
                )
                continue

            if operation == "update_bootstrap_configmaps":
                run_phase(
                    _phase_name("Update bootstrap ConfigMaps", step),
                    update_bootstrap_configmaps,
                )
                continue

            if operation == "recreate_bootstrap_job":
                run_phase(_phase_name("Recreate bootstrap Job", step), recreate_bootstrap_job)
                continue

            if operation == "wait_for_bootstrap_job":
                run_phase(
                    _phase_name("Wait for bootstrap Job completion", step),
                    wait_for_bootstrap_job,
                )
                continue

            if operation == "print_bootstrap_job_logs":
                run_phase(_phase_name("Print bootstrap Job logs", step), print_bootstrap_job_logs)
                continue

            raise ValueError(
                "Unknown bootstrap-job phase operation "
                f"'{operation}' in adapter_hooks.bootstrap_job.phase_plan."
            )
