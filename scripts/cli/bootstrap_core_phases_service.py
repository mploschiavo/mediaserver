from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class BootstrapCorePhasesConfig:
    namespace: str
    prepare_host_root: str
    skip_qbit_ensure: bool
    skip_sab_ensure: bool


class BootstrapCorePhasesService:
    def __init__(self, cfg: BootstrapCorePhasesConfig) -> None:
        self.cfg = cfg

    def run(
        self,
        *,
        run_phase: Callable[..., None],
        run_script: Callable[..., None],
        resolve_bootstrap_config: Callable[[], None],
        ensure_bootstrap_pvc_prereqs: Callable[[], None],
        prime_servarr_api_keys_secret: Callable[[], None],
        prime_sab_api_key_secret: Callable[[], None],
        prime_jellyseerr_api_key_secret: Callable[[], None],
        prime_tautulli_api_key_secret: Callable[[], None],
        update_bootstrap_configmaps: Callable[[], None],
        recreate_bootstrap_job: Callable[[], None],
        wait_for_bootstrap_job: Callable[[], None],
        print_bootstrap_job_logs: Callable[[], None],
    ) -> None:
        run_phase(
            "Ensure torrent client credentials",
            lambda: run_script(
                "ensure-qbit-credentials.sh",
                env={
                    "NAMESPACE": self.cfg.namespace,
                    "PREPARE_HOST_ROOT": self.cfg.prepare_host_root,
                },
            ),
            enabled=not self.cfg.skip_qbit_ensure,
        )
        run_phase(
            "Ensure usenet client API access",
            lambda: run_script(
                "ensure-sabnzbd-api-access.sh",
                env={"NAMESPACE": self.cfg.namespace},
            ),
            enabled=not self.cfg.skip_sab_ensure,
        )
        run_phase("Resolve bootstrap config", resolve_bootstrap_config)
        run_phase("Ensure bootstrap PVC prerequisites", ensure_bootstrap_pvc_prereqs)
        run_phase("Prime Arr API keys into secret", prime_servarr_api_keys_secret)
        run_phase("Prime usenet API key into secret", prime_sab_api_key_secret)
        run_phase("Prime Jellyseerr API key into secret", prime_jellyseerr_api_key_secret)
        run_phase("Prime Tautulli API key into secret", prime_tautulli_api_key_secret)
        run_phase("Update bootstrap ConfigMaps", update_bootstrap_configmaps)
        run_phase("Recreate bootstrap Job", recreate_bootstrap_job)
        run_phase("Wait for bootstrap Job completion", wait_for_bootstrap_job)
        run_phase("Print bootstrap Job logs", print_bootstrap_job_logs)
