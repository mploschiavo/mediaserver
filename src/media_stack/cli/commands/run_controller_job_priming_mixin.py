"""Thin-wrapper secret-priming methods for ``RunBootstrapJobRunner``.

Split out of ``run_controller_job_main.py`` so the main runner class
stays under the 500-line god-class ratchet. Each method here is a
one-line delegation to ``self._secret_priming_service()``; keeping
them as separate named methods matches the controller entry points
expected by ``bootstrap`` hooks.
"""

from __future__ import annotations


class _RunBootstrapJobPrimingMixin:
    """Pre-bootstrap secret-priming + deployment convenience wrappers."""

    def prime_servarr_api_keys_secret(self) -> None:
        self._secret_priming_service().prime_servarr_api_keys()

    def prime_usenet_client_api_key_secret(self) -> None:
        self._secret_priming_service().prime_usenet_client_api_key()

    def prime_request_manager_api_key_secret(self) -> None:
        self._secret_priming_service().prime_request_manager_api_key()

    def prime_analytics_api_key_secret(self) -> None:
        self._secret_priming_service().prime_analytics_api_key()

    def prime_media_server_api_key_secret(self) -> None:
        self._secret_priming_service().prime_media_server_api_key()

    def prime_media_server_user_id_secret(self) -> None:
        self._secret_priming_service().prime_media_server_user_id()

    def update_bootstrap_configmaps(self) -> None:
        self._manifest_service().update_bootstrap_configmaps()

    def recreate_bootstrap_job(self) -> None:
        self._manifest_service().recreate_bootstrap_job()

    # Deployment-based aliases (preferred for new deploys).
    def ensure_bootstrap_deployment(self) -> None:
        self._manifest_service().ensure_bootstrap_deployment()

    def wait_for_bootstrap_job(self) -> None:
        self._job_wait_service().wait_for_job(
            job_name="media-stack-controller",
            selector="app=media-stack-controller",
        )

    def print_bootstrap_job_logs(self) -> None:
        self._job_logs_service().capture_logs()

    def _log_contains(self, marker: str) -> bool:
        return self._job_logs_service().log_contains(marker)

    def deployment_exists(self, deployment: str) -> bool:
        return self._deployment_ops_service().deployment_exists(deployment)

    def restart_deployment(self, deployment: str, *, timeout_seconds: int) -> None:
        self._deployment_ops_service().restart_deployment(
            deployment,
            timeout_seconds=timeout_seconds,
        )

    def restart_deployment_if_exists(self, deployment: str, *, timeout_seconds: int) -> None:
        self._deployment_ops_service().restart_deployment_if_exists(
            deployment,
            timeout_seconds=timeout_seconds,
        )

    def _read_secret_key(self, secret: str, key_name: str) -> str:
        return self._secret_reader_service().read_secret_key(secret, key_name)


__all__ = ["_RunBootstrapJobPrimingMixin"]
