"""ControllerK8sSecretWriter — persist discovered API keys to a K8s Secret.

ADR-0015 Phase 7k. Pre-Phase-7k this class lived in
``cli/commands/controller_k8s.py``. The ADR's initial draft
grouped it under "controller HTTP server glue" because it's
imported by the controller's PID-1 boot chain (``controller_main``
→ ``controller_k8s``); Phase 7k revisits that judgement and
notes the class is workflow material (K8s API client
operations against ``media-stack-secrets``), not HTTP-server
glue. The commands-tier file survives as a re-export shim
for the historical import surface.

The controller discovers keys via HTTP during preflights; this
class persists them so downstream services + reconcile CronJobs
can read them out of the Secret rather than re-discovering on
every pod boot. On non-K8s targets (compose, dev) the call is a
no-op driven by the absence of ``K8S_NAMESPACE``.
"""

from __future__ import annotations

import base64
import os

import media_stack.services.runtime_platform as runtime_platform


_DEFAULT_SECRET_NAME = "media-stack-secrets"
_API_KEY_SUFFIXES = ("_API_KEY", "_USER_ID")


class ControllerK8sSecretWriter:
    """Patches discovered API keys + user IDs into the K8s Secret."""

    def _persist_preflight_keys_to_secret(self, state: object) -> None:
        """Patch discovered API keys from preflights into the K8s secret."""
        namespace = os.environ.get("K8S_NAMESPACE", "")
        secret_name = os.environ.get("K8S_SECRET_NAME", _DEFAULT_SECRET_NAME)
        if not namespace:
            runtime_platform.log("[INFO] Not in K8s — skipping secret persistence")
            return

        string_data = self._collect_preflight_keys(state)
        self._collect_env_keys(string_data)
        self._collect_user_id_keys(string_data)

        if not string_data:
            runtime_platform.log(
                "[INFO] No API keys discovered in preflights to persist"
            )
            return

        self._patch_secret(namespace, secret_name, string_data)

    def _collect_preflight_keys(self, state: object) -> dict[str, str]:
        """Pull ``*_API_KEY`` / ``*_USER_ID`` values out of
        ``state.preflight_results``."""
        preflight_results = getattr(state, "preflight_results", {})
        string_data: dict[str, str] = {}
        for _section_name, section in preflight_results.items():
            if not isinstance(section, dict):
                continue
            for key, value in section.items():
                if key.endswith(_API_KEY_SUFFIXES):
                    val = str(value or "").strip()
                    if val:
                        string_data[key] = val
        return string_data

    def _collect_env_keys(self, string_data: dict[str, str]) -> None:
        """Also collect from env vars — keys discovered at startup are
        in ``os.environ`` even if ``preflight_results`` is empty (e.g.,
        subprocess stub)."""
        from media_stack.core.service_registry.registry import SERVICES
        for svc in SERVICES:
            if svc.api_key_env:
                val = os.environ.get(svc.api_key_env, "").strip()
                if val and svc.api_key_env not in string_data:
                    string_data[svc.api_key_env] = val

    def _collect_user_id_keys(self, string_data: dict[str, str]) -> None:
        """Persist user IDs from env — derived from service registry
        ``user_id_env`` fields, with a ``<SERVICE>_USER_ID`` fallback."""
        from media_stack.core.service_registry.registry import SERVICES
        user_id_env_keys = {
            env_key
            for svc in SERVICES
            for env_key in [getattr(svc, "user_id_env", None)]
            if env_key
        }
        if not user_id_env_keys:
            for svc in SERVICES:
                candidate = f"{svc.id.upper()}_USER_ID"
                if os.environ.get(candidate, "").strip():
                    user_id_env_keys.add(candidate)
        for uid_key in user_id_env_keys:
            uid_val = os.environ.get(uid_key, "").strip()
            if uid_val:
                string_data.setdefault(uid_key, uid_val)

    def _patch_secret(
        self,
        namespace: str,
        secret_name: str,
        string_data: dict[str, str],
    ) -> None:
        """Issue ``patch_namespaced_secret`` with base64-encoded values.

        Wraps the kubernetes client load + patch in a single broad
        ``except`` because the optional ``kubernetes`` package may not
        be installed on dev / compose hosts.
        """
        try:
            from kubernetes import client, config
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()
            v1 = client.CoreV1Api()
            patch_body = {
                "data": {
                    k: base64.b64encode(v.encode()).decode()
                    for k, v in string_data.items()
                }
            }
            v1.patch_namespaced_secret(
                name=secret_name, namespace=namespace, body=patch_body,
            )
            runtime_platform.log(
                f"[OK] Persisted {len(string_data)} keys to secret "
                f"{namespace}/{secret_name}: "
                + ", ".join(sorted(string_data.keys()))
            )
        except Exception as exc:  # noqa: BLE001 — kubernetes optional + many failure modes
            runtime_platform.log(
                f"[WARN] Failed to persist keys to K8s secret: {exc}"
            )


__all__ = ["ControllerK8sSecretWriter"]
