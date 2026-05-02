"""Job handler: ensure Jellyfin's API key is discoverable.

Bootstrap-phase ensurer. The preflight handlers that run during
``container_preflight_handlers`` may execute before Jellyfin is
ready and time out at ``_wait_ready`` without retrying; this
handler retries until the key actually lands.

Design (idempotent + self-healing, ~50 LOC):

  1. PROBE: if ``discover_api_keys()`` already includes jellyfin → skip
     (status: ``skipped``, reason: ``already_minted``).
  2. PROBE: if Jellyfin's HTTP endpoint isn't responsive → skip
     (status: ``skipped``, reason: ``service_not_ready``).
     Lets the auto-heal cycle keep trying without making noise.
  3. MINT: call the canonical ``http_preflight.run_preflight``. The
     preflight is itself idempotent — if a key already exists for
     ``app=media-stack-controller`` it returns it instead of minting.
  4. PERSIST: write the minted token into ``os.environ`` (so callers
     within the same controller process see it immediately) and into
     the K8s secret via ``_persist_preflight_keys_to_secret_safe``
     (no-op on compose).

In continuous mode this invariant is owned by the orchestrator's
``jellyfin-api-key-discoverable`` promise (which dispatches
``JellyfinLifecycle.mint_api_key`` — same underlying preflight).
This handler stays registered as the bootstrap-phase entry point;
see ADR-0003 for the orchestrator design.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from media_stack.application.jobs.framework import JobContext


logger = logging.getLogger(__name__)


def ensure_jellyfin_api_key(_ctx: JobContext) -> dict[str, Any]:
    """One evaluation cycle. Returns a framework-expected result dict.

    JobRunner records terminal status from the dict's ``skipped`` /
    ``status`` keys. Exceptions propagate and become terminal
    ``error`` runs — desirable: a recurring error is the operator
    signal that something deeper is wrong (Jellyfin login broken,
    admin password drift, etc.).
    """
    from media_stack.api.services.health import discover_api_keys
    from media_stack.api.services.registry import service_internal_url

    keys = discover_api_keys()
    if "jellyfin" in keys and keys["jellyfin"]:
        return {"skipped": "already_minted", "key_length": len(keys["jellyfin"])}

    # Probe Jellyfin's public-info endpoint. Don't even attempt the
    # mint flow if Jellyfin isn't responsive — we'd just time out
    # in ``_wait_ready`` like the original 31-stampede.
    jellyfin_url = service_internal_url("jellyfin")
    if not _jellyfin_reachable(jellyfin_url):
        return {
            "skipped": "service_not_ready",
            "url": jellyfin_url,
            "detail": "Jellyfin not responding at /System/Info/Public; will retry next cycle",
        }

    from media_stack.infrastructure.jellyfin.http_preflight import run_preflight

    logger.info("[INFO] jellyfin:ensure-api-key: minting via http_preflight")
    result = run_preflight(jellyfin_url=jellyfin_url, log=lambda m: logger.info(m))

    minted = (result or {}).get("JELLYFIN_API_KEY", "")
    if not minted:
        # Preflight returned without raising but didn't yield a key —
        # treat as a soft failure so the framework records ``error`` and
        # the operator gets a real signal.
        raise RuntimeError(
            "jellyfin http_preflight returned without an API key; "
            f"keys present in result={list((result or {}).keys())}"
        )

    os.environ["JELLYFIN_API_KEY"] = minted
    if user_id := (result or {}).get("JELLYFIN_USER_ID", ""):
        os.environ["JELLYFIN_USER_ID"] = user_id

    persist_summary = _persist_to_secret_if_possible(
        {"JELLYFIN_API_KEY": minted, **({"JELLYFIN_USER_ID": user_id} if user_id else {})},
    )
    _bust_runtime_keys_cache()

    return {
        "status": "minted",
        "key_length": len(minted),
        "persist": persist_summary,
    }


def _jellyfin_reachable(jellyfin_url: str, timeout_seconds: int = 5) -> bool:
    """Cheap pre-check — single HTTP GET, no retry. Caller's job is to
    re-run on the auto-heal cycle if Jellyfin isn't up yet."""
    import urllib.error
    import urllib.request

    url = f"{jellyfin_url.rstrip('/')}/System/Info/Public"
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _persist_to_secret_if_possible(payload: dict[str, str]) -> str:
    """Best-effort persist into the controller's secret store. On
    compose this is a no-op; on k8s it patches ``media-stack-secrets``.
    Failure is reported, never raised — the env var alone is enough
    for the running process."""
    try:
        from media_stack.services.apps.core.job_adapters import (
            _persist_preflight_keys_to_secret_safe,
            _stub_state,
        )
        result = _persist_preflight_keys_to_secret_safe(_stub_state(), payload)
        return str(result.get("status") or result)
    except Exception as exc:  # noqa: BLE001
        return f"persist_skipped: {exc}"


def _bust_runtime_keys_cache() -> None:
    """Invalidate the 30s runtime_keys cache so the next
    ``read_service_api_key('jellyfin')`` from a dashboard handler
    sees the freshly-minted key without waiting for the TTL."""
    try:
        from media_stack.api.services.runtime_keys import invalidate_cache
        invalidate_cache()
    except Exception as exc:  # noqa: BLE001
        logger.debug("runtime_keys cache invalidation failed: %s", exc)
