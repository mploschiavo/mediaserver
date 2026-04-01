from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from cli.bootstrap_component_resolver import (
    BootstrapComponentPlan,
    resolve_bootstrap_component_plan,
    resolve_runner_phase_script,
)


@dataclass(frozen=True)
class BootstrapCorePhasesConfig:
    config_file: Path
    namespace: str
    prepare_host_root: str
    skip_qbit_ensure: bool
    skip_sab_ensure: bool


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

    def _selected_download_client(self, role_key: str) -> dict[str, object]:
        technology = self._role_binding(role_key)
        selected = self.plan.download_clients.get(technology)
        if isinstance(selected, dict):
            return selected
        return {}

    def _should_run_torrent_client_ensure(self) -> bool:
        selected = self._selected_download_client("torrent_client")
        return bool(
            selected.get("configure_arr_clients")
            or selected.get("set_categories_in_qbit")
            or selected.get("set_categories")
        )

    def _should_run_usenet_client_ensure(self) -> bool:
        selected = self._selected_download_client("usenet_client")
        return bool(selected.get("configure_arr_clients"))

    def _supports_usenet_secret_priming(self) -> bool:
        usenet_client = self._role_binding("usenet_client")
        return usenet_client in {"sabnzbd"}

    def _supports_request_manager_secret_priming(self) -> bool:
        request_manager = self._role_binding("request_manager")
        return request_manager in {"jellyseerr"}

    def _tautulli_integration_enabled(self) -> bool:
        maintainerr = self.plan.config.get("maintainerr")
        if not isinstance(maintainerr, dict):
            return False
        integrations = maintainerr.get("integrations")
        if not isinstance(integrations, dict):
            return False
        tautulli = integrations.get("tautulli")
        if not isinstance(tautulli, dict):
            return False
        return bool(tautulli.get("enabled"))

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

        run_phase(
            f"Ensure torrent client bootstrap access ({torrent_client or 'unbound'})",
            lambda: run_script(
                torrent_script,
                env={
                    "NAMESPACE": self.cfg.namespace,
                    "PREPARE_HOST_ROOT": self.cfg.prepare_host_root,
                },
            ),
            enabled=(
                not self.cfg.skip_qbit_ensure
                and self._should_run_torrent_client_ensure()
                and bool(torrent_script)
            ),
        )
        run_phase(
            f"Ensure usenet client API access ({usenet_client or 'unbound'})",
            lambda: run_script(
                usenet_script,
                env={"NAMESPACE": self.cfg.namespace},
            ),
            enabled=(
                not self.cfg.skip_sab_ensure
                and self._should_run_usenet_client_ensure()
                and bool(usenet_script)
            ),
        )
        run_phase("Resolve bootstrap config", resolve_bootstrap_config)
        run_phase("Ensure bootstrap PVC prerequisites", ensure_bootstrap_pvc_prereqs)
        run_phase("Prime Arr API keys into secret", prime_servarr_api_keys_secret)
        run_phase(
            f"Prime usenet client API key into secret ({usenet_client or 'unbound'})",
            prime_usenet_client_api_key_secret,
            enabled=self._supports_usenet_secret_priming(),
        )
        run_phase(
            f"Prime request manager API key into secret ({request_manager or 'unbound'})",
            prime_request_manager_api_key_secret,
            enabled=self._supports_request_manager_secret_priming(),
        )
        run_phase(
            "Prime Tautulli API key into secret",
            prime_tautulli_api_key_secret,
            enabled=self._tautulli_integration_enabled(),
        )
        run_phase("Update bootstrap ConfigMaps", update_bootstrap_configmaps)
        run_phase("Recreate bootstrap Job", recreate_bootstrap_job)
        run_phase("Wait for bootstrap Job completion", wait_for_bootstrap_job)
        run_phase("Print bootstrap Job logs", print_bootstrap_job_logs)
