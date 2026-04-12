"""Kubernetes secret persistence for the bootstrap controller."""

from __future__ import annotations

import os

import media_stack.services.runtime_platform as runtime_platform


def _persist_preflight_keys_to_secret(state: object) -> None:
    """Patch discovered API keys from preflights into the K8s secret.

    The controller discovers keys via HTTP during preflights, but downstream
    services (and reconcile CronJobs) need them in the K8s secret.
    """
    namespace = os.environ.get("K8S_NAMESPACE", "")
    secret_name = os.environ.get("K8S_SECRET_NAME", "media-stack-secrets")
    if not namespace:
        runtime_platform.log("[INFO] Not in K8s — skipping secret persistence")
        return

    preflight_results = getattr(state, "preflight_results", {})
    string_data: dict[str, str] = {}
    for _section_name, section in preflight_results.items():
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if key.endswith("_API_KEY") or key.endswith("_USER_ID"):
                val = str(value or "").strip()
                if val:
                    string_data[key] = val

    # Also collect from env vars — keys discovered at startup are in os.environ
    # even if preflight_results is empty (e.g., subprocess stub).
    from media_stack.api.services.registry import SERVICES
    for svc in SERVICES:
        if svc.api_key_env:
            val = os.environ.get(svc.api_key_env, "").strip()
            if val and svc.api_key_env not in string_data:
                string_data[svc.api_key_env] = val
    # Also check JELLYFIN_USER_ID
    jf_uid = os.environ.get("JELLYFIN_USER_ID", "").strip()
    if jf_uid:
        string_data.setdefault("JELLYFIN_USER_ID", jf_uid)

    if not string_data:
        runtime_platform.log("[INFO] No API keys discovered in preflights to persist")
        return

    try:
        from kubernetes import client, config
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        v1 = client.CoreV1Api()
        import base64
        patch_body = {"data": {k: base64.b64encode(v.encode()).decode() for k, v in string_data.items()}}
        v1.patch_namespaced_secret(name=secret_name, namespace=namespace, body=patch_body)
        runtime_platform.log(
            f"[OK] Persisted {len(string_data)} keys to secret {namespace}/{secret_name}: "
            + ", ".join(sorted(string_data.keys()))
        )
    except Exception as exc:
        runtime_platform.log(f"[WARN] Failed to persist keys to K8s secret: {exc}")
