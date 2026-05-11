"""BootstrapPrimingPhase — Command set for secret priming + deployment ops.

ADR-0015 Phase 7c. Pre-Phase-7c these methods lived on
:class:`_RunBootstrapJobPrimingMixin` (a 75-LoC mixin in
commands/) that :class:`RunBootstrapJobRunner` inherited. The
mixin pattern is exactly the anti-pattern this ADR is retiring;
Phase 7c collapses the mixin onto a proper Command-set class in
workflows/, constructor-injected with the service bundle.

The eight ``prime_*`` and ``update_bootstrap_configmaps`` methods
are one-line delegates to the underlying workflow services; the
class exists to give the operation-handler dispatch dict in
:class:`RunBootstrapJobPipeline` a single object to hand stable
method references to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from media_stack.cli.workflows.run_controller_job_orchestration.service_factory_bundle import (
        BootstrapJobServiceBundle,
    )


_BOOTSTRAP_JOB_NAME = "media-stack-controller"
_BOOTSTRAP_SELECTOR = "app=media-stack-controller"
_DEPLOYMENT_RESTART_TIMEOUT_SECONDS = 180


class BootstrapPrimingPhase:
    """Command set: prime secrets + manage bootstrap deployment lifecycle."""

    def __init__(self, services: "BootstrapJobServiceBundle") -> None:
        self._services = services

    # -- secret priming -------------------------------------------------

    def prime_servarr_api_keys_secret(self) -> None:
        self._services.secret_priming_service().prime_servarr_api_keys()

    def prime_usenet_client_api_key_secret(self) -> None:
        self._services.secret_priming_service().prime_usenet_client_api_key()

    def prime_request_manager_api_key_secret(self) -> None:
        self._services.secret_priming_service().prime_request_manager_api_key()

    def prime_analytics_api_key_secret(self) -> None:
        self._services.secret_priming_service().prime_analytics_api_key()

    def prime_media_server_api_key_secret(self) -> None:
        self._services.secret_priming_service().prime_media_server_api_key()

    def prime_media_server_user_id_secret(self) -> None:
        self._services.secret_priming_service().prime_media_server_user_id()

    # -- manifest / job lifecycle --------------------------------------

    def update_bootstrap_configmaps(self) -> None:
        self._services.manifest_service().update_bootstrap_configmaps()

    def recreate_bootstrap_job(self) -> None:
        self._services.manifest_service().recreate_bootstrap_job()

    def ensure_bootstrap_deployment(self) -> None:
        self._services.manifest_service().ensure_bootstrap_deployment()

    def wait_for_bootstrap_job(self) -> None:
        self._services.job_wait_service().wait_for_job(
            job_name=_BOOTSTRAP_JOB_NAME,
            selector=_BOOTSTRAP_SELECTOR,
        )

    def print_bootstrap_job_logs(self) -> None:
        self._services.job_logs_service().capture_logs()

    # -- introspection helpers (used by the hook_context) --------------

    def log_contains(self, marker: str) -> bool:
        return self._services.job_logs_service().log_contains(marker)

    def deployment_exists(self, deployment: str) -> bool:
        return self._services.deployment_ops_service().deployment_exists(deployment)

    def restart_deployment(
        self, deployment: str, *, timeout_seconds: int = _DEPLOYMENT_RESTART_TIMEOUT_SECONDS,
    ) -> None:
        self._services.deployment_ops_service().restart_deployment(
            deployment, timeout_seconds=timeout_seconds,
        )

    def restart_deployment_if_exists(
        self, deployment: str, *, timeout_seconds: int = _DEPLOYMENT_RESTART_TIMEOUT_SECONDS,
    ) -> None:
        self._services.deployment_ops_service().restart_deployment_if_exists(
            deployment, timeout_seconds=timeout_seconds,
        )

    def read_secret_key(self, secret: str, key_name: str) -> str:
        return self._services.secret_reader_service().read_secret_key(secret, key_name)


__all__ = ["BootstrapPrimingPhase"]
