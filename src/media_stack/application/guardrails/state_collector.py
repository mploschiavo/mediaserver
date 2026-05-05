"""State collector — single tick-time snapshot of every input the
rules need.

Rules MUST NOT do their own I/O; they take the dict this module
returns and read fields out of it. Centralising the collection here
gives the evaluation loop a single place to set timeouts, swallow
flaky upstream calls, and keep test fixtures small.

Telemetry that isn't wired yet (egress GB, OpenSubtitles call counts
etc.) returns empty / zero values; the corresponding guardrail rules
treat that as "no data, no fire".
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

_log = logging.getLogger("media_stack.guardrails")


# In-process counter snapshots populated by the audit / analytics
# layer. The state collector reads these at tick time. Updaters live
# elsewhere — the indexer 429 logger calls
# ``record_indexer_429(indexer_id)`` which appends to the rolling
# window, etc.

_indexer_429_window: list[tuple[str, float]] = []
_indexer_429_lock = threading.Lock()


def record_indexer_429(indexer_id: str, *, now: float | None = None) -> None:
    """Append a 429 event. Trims to the last hour on each call so
    the list stays bounded."""
    ts = now if now is not None else time.time()
    cutoff = ts - 3600.0
    with _indexer_429_lock:
        _indexer_429_window.append((str(indexer_id or "?"), ts))
        # Trim from the head while the oldest is past the cutoff.
        i = 0
        for i, (_, then) in enumerate(_indexer_429_window):
            if then >= cutoff:
                break
        if i > 0:
            del _indexer_429_window[:i]


def _indexer_429_snapshot(*, now: float | None = None) -> list[dict[str, Any]]:
    ts = now if now is not None else time.time()
    cutoff = ts - 300.0  # last 5 min for the bandwidth rule
    with _indexer_429_lock:
        return [
            {"indexer": ind, "ts": then}
            for ind, then in _indexer_429_window
            if then >= cutoff
        ]


def _indexer_429_hour(*, now: float | None = None) -> list[dict[str, Any]]:
    ts = now if now is not None else time.time()
    cutoff = ts - 3600.0
    with _indexer_429_lock:
        return [
            {"indexer": ind, "ts": then}
            for ind, then in _indexer_429_window
            if then >= cutoff
        ]


def collect_state(
    *,
    now: float | None = None,
    failed_login_tracker: Any | None = None,
) -> dict[str, Any]:
    """Build the snapshot. Each section is wrapped in try/except so a
    failing upstream service can't break the whole tick — the
    affected rule sees an empty dict and stays silent.

    ``failed_login_tracker`` is injectable so unit tests can provide
    a fake without booting the auth subsystem.
    """
    ts = now if now is not None else time.time()
    state: dict[str, Any] = {"_collected_at": ts}

    # Disk usage — reuse the existing service so the collector
    # benefits from its filesystem-detection logic.
    try:
        from media_stack.api.services import disk as disk_svc
        disk_payload = disk_svc.get_disk()
        disks = disk_payload.get("disk") if isinstance(disk_payload, dict) else None
        if isinstance(disks, dict):
            state["disk"] = disks
    except Exception as exc:  # noqa: BLE001
        _log.debug("guardrails: disk collection failed: %s", exc)

    # Per-content-type breakdown.
    try:
        from media_stack.api.services import disk as disk_svc
        breakdown_payload = disk_svc.get_storage_breakdown()
        items = (breakdown_payload or {}).get("breakdown") or []
        breakdown = {}
        for item in items:
            if isinstance(item, dict):
                breakdown[str(item.get("name") or "")] = int(item.get("bytes") or 0)
        state["storage_breakdown"] = breakdown
    except Exception as exc:  # noqa: BLE001
        _log.debug("guardrails: breakdown collection failed: %s", exc)

    # Inode usage per mount — best-effort via os.statvfs.
    try:
        import os
        mounts = (state.get("disk") or {})
        inodes: dict[str, float] = {}
        for label, info in mounts.items():
            if not isinstance(info, dict):
                continue
            path = info.get("path")
            if not path:
                continue
            try:
                st = os.statvfs(path)
                if st.f_files:
                    used = st.f_files - st.f_ffree
                    inodes[label] = round(used / st.f_files * 100, 2)
            except OSError:
                continue
        if inodes:
            state["mount_inodes"] = inodes
    except Exception as exc:  # noqa: BLE001
        _log.debug("guardrails: inode collection failed: %s", exc)

    # Job history — used by job_health rules.
    try:
        from media_stack.services.jobs.framework import get_job_history
        state["job_history"] = list(get_job_history() or [])
    except Exception as exc:  # noqa: BLE001
        _log.debug("guardrails: job history collection failed: %s", exc)
        state["job_history"] = []

    # Auth-tracker snapshot.
    try:
        if failed_login_tracker is not None:
            state["auth"] = {
                "failed_login_tracker": failed_login_tracker.snapshot(),
            }
        else:
            state["auth"] = {"failed_login_tracker": {}}
    except Exception as exc:  # noqa: BLE001
        _log.debug("guardrails: auth collection failed: %s", exc)
        state["auth"] = {"failed_login_tracker": {}}

    # Bandwidth + external API counters — the in-process trackers
    # populate these. Fields default to zero so quotas stay quiet
    # when telemetry isn't wired.
    state["bandwidth"] = {
        "upload_gb_today": 0.0,
        "concurrent_downloads": 0,
        "indexer_429s": _indexer_429_snapshot(now=ts),
    }
    state["external_api"] = {
        "opensubtitles_used": 0,
        "tmdb_calls_today": 0,
        "indexer_429s": _indexer_429_hour(now=ts),
    }

    state.setdefault("media_quality", {})
    state.setdefault("dependency", {})
    state.setdefault("cost", {})
    state.setdefault("auto_heal", {})
    state.setdefault("snapshots", {})
    state.setdefault("arr_recycle_bins", [])
    state.setdefault("unpacker_scratch", {})

    # Lockdown state — read directly from the persisted state file
    # (no service instance is wired here, and we deliberately don't
    # build the full DownloadLockdownService just to read state since
    # that would require all per-client adapters in scope). Use the
    # same path resolution the service does so the rule sees the
    # truth a release/engage just wrote.
    try:
        from media_stack.services.download_lockdown_service import (
            LOCKDOWN_STATE_FILE,
        )
        import json as _json
        path = LOCKDOWN_STATE_FILE.default_path()
        if path.is_file():
            try:
                raw = _json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    merged = LOCKDOWN_STATE_FILE.empty_state()
                    for key in merged:
                        if key in raw:
                            merged[key] = raw[key]
                    state["_lockdown_state"] = merged
                else:
                    state["_lockdown_state"] = LOCKDOWN_STATE_FILE.empty_state()
            except (OSError, _json.JSONDecodeError) as exc:
                _log.debug(
                    "guardrails: lockdown-state read failed: %s", exc,
                )
                state["_lockdown_state"] = LOCKDOWN_STATE_FILE.empty_state()
        else:
            state["_lockdown_state"] = LOCKDOWN_STATE_FILE.empty_state()
    except ImportError as exc:  # pragma: no cover - defensive
        _log.debug("guardrails: lockdown service module missing: %s", exc)
        state.setdefault("_lockdown_state", {
            "engaged": False, "trigger": None, "paused_clients": [],
        })

    return state
