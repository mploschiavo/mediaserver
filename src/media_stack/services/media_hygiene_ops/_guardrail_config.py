"""Configuration parsing and torrent record helpers for qB queue guardrails."""

from __future__ import annotations

from typing import Any


# Priority order for choosing which torrents to prune first.
STATE_RANK: dict[str, int] = {
    "error": 0,
    "missingfiles": 1,
    "stalleddl": 2,
    "metadl": 3,
    "queueddl": 4,
    "pauseddl": 5,
    "checkingdl": 6,
    "downloading": 7,
    "forceddl": 8,
    "allocating": 9,
    "checkingresumedata": 10,
}

DEFAULT_COUNT_STATES: list[str] = [
    "downloading",
    "queuedDL",
    "stalledDL",
    "metaDL",
    "forcedDL",
    "checkingDL",
    "pausedDL",
    "allocating",
    "checkingResumeData",
]

DEFAULT_PRUNE_STATES: list[str] = [
    "queuedDL",
    "stalledDL",
    "metaDL",
    "pausedDL",
    "error",
    "missingFiles",
]

DEFAULT_BUDGET_PRUNE_STATES: set[str] = {
    "queueddl",
    "stalleddl",
    "metadl",
    "pauseddl",
    "error",
    "missingfiles",
    "uploading",
    "stalledup",
    "queuedup",
    "pausedup",
}


def parse_guardrail_config(ops: Any, queue_cfg: dict[str, Any]) -> dict[str, Any]:
    """Parse all guardrail configuration values into a flat dict."""
    count_states = {
        str(x).strip().lower()
        for x in ops.coerce_list(queue_cfg.get("count_states"))
        if str(x).strip()
    } or {x.lower() for x in DEFAULT_COUNT_STATES}

    prune_states = {
        str(x).strip().lower()
        for x in ops.coerce_list(queue_cfg.get("prune_states"))
        if str(x).strip()
    } or {x.lower() for x in DEFAULT_PRUNE_STATES}

    include_uncategorized = ops.bool_cfg(queue_cfg, "include_uncategorized", False)
    default_max_queued = ops.to_int(queue_cfg.get("default_max_queued"))
    prune_when_over_limit = ops.bool_cfg(queue_cfg, "prune_when_over_limit", True)
    over_limit_delete_files = ops.bool_cfg(queue_cfg, "over_limit_delete_files", True)
    over_limit_max_delete_per_category = ops.to_int(
        queue_cfg.get("over_limit_max_delete_per_category"), 15
    )
    if over_limit_max_delete_per_category is None or over_limit_max_delete_per_category <= 0:
        over_limit_max_delete_per_category = 15

    max_by_category: dict[str, int] = {}
    max_by_category_raw = queue_cfg.get("max_queued_by_category") or {}
    if isinstance(max_by_category_raw, dict):
        for key, value in max_by_category_raw.items():
            norm_key = str(key or "").strip().lower()
            if not norm_key:
                continue
            parsed = ops.to_int(value)
            if parsed is None or parsed < 0:
                continue
            max_by_category[norm_key] = int(parsed)

    max_size_bytes_by_category: dict[str, int] = {}
    max_size_gib_raw = queue_cfg.get("max_total_size_gib_by_category") or {}
    if isinstance(max_size_gib_raw, dict):
        for key, value in max_size_gib_raw.items():
            norm_key = str(key or "").strip().lower()
            if not norm_key:
                continue
            parsed = ops.to_float(value)
            if parsed is None or parsed <= 0:
                continue
            max_size_bytes_by_category[norm_key] = int(float(parsed) * (1024**3))

    max_weight_percent_by_category: dict[str, float] = {}
    max_weight_percent_raw = queue_cfg.get("max_weight_percent_by_category") or {}
    if isinstance(max_weight_percent_raw, dict):
        for key, value in max_weight_percent_raw.items():
            norm_key = str(key or "").strip().lower()
            if not norm_key:
                continue
            parsed = ops.to_float(value)
            if parsed is None:
                continue
            percent = max(0.0, min(float(parsed), 100.0))
            if percent <= 0:
                continue
            max_weight_percent_by_category[norm_key] = percent

    over_budget_max_delete_per_category = ops.to_int(
        queue_cfg.get("over_budget_max_delete_per_category"), 20
    )
    if over_budget_max_delete_per_category is None or over_budget_max_delete_per_category <= 0:
        over_budget_max_delete_per_category = 20
    over_budget_delete_files = ops.bool_cfg(queue_cfg, "over_budget_delete_files", True)
    budget_prune_states = {
        str(x).strip().lower()
        for x in ops.coerce_list(queue_cfg.get("budget_prune_states"))
        if str(x).strip()
    } or set(DEFAULT_BUDGET_PRUNE_STATES)

    stale_cfg = queue_cfg.get("stale_prune") or {}
    stale_enabled = ops.bool_cfg(stale_cfg, "enabled", True)
    stale_max_age_hours = ops.to_float(stale_cfg.get("max_age_hours"), 168.0) or 168.0
    stale_max_stalled_hours = ops.to_float(stale_cfg.get("max_stalled_hours"), 24.0) or 24.0
    stale_max_eta_seconds = ops.to_int(stale_cfg.get("max_eta_seconds"), 14 * 24 * 3600)
    stale_min_progress = ops.to_float(stale_cfg.get("min_progress"), 0.98)
    if stale_min_progress is None:
        stale_min_progress = 0.98
    stale_max_download_speed_bps = ops.to_int(stale_cfg.get("max_download_speed_bps"), 32768)
    stale_max_delete_per_run = ops.to_int(stale_cfg.get("max_delete_per_run"), 25)
    if stale_max_delete_per_run is None or stale_max_delete_per_run <= 0:
        stale_max_delete_per_run = 25
    stale_delete_files = ops.bool_cfg(stale_cfg, "delete_files", True)
    stale_states = {
        str(x).strip().lower() for x in ops.coerce_list(stale_cfg.get("states")) if str(x).strip()
    } or set(prune_states)

    return {
        "count_states": count_states,
        "prune_states": prune_states,
        "include_uncategorized": include_uncategorized,
        "default_max_queued": default_max_queued,
        "prune_when_over_limit": prune_when_over_limit,
        "over_limit_delete_files": over_limit_delete_files,
        "over_limit_max_delete_per_category": over_limit_max_delete_per_category,
        "max_by_category": max_by_category,
        "max_size_bytes_by_category": max_size_bytes_by_category,
        "max_weight_percent_by_category": max_weight_percent_by_category,
        "over_budget_max_delete_per_category": over_budget_max_delete_per_category,
        "over_budget_delete_files": over_budget_delete_files,
        "budget_prune_states": budget_prune_states,
        "stale_enabled": stale_enabled,
        "stale_max_age_hours": stale_max_age_hours,
        "stale_max_stalled_hours": stale_max_stalled_hours,
        "stale_max_eta_seconds": stale_max_eta_seconds,
        "stale_min_progress": stale_min_progress,
        "stale_max_download_speed_bps": stale_max_download_speed_bps,
        "stale_max_delete_per_run": stale_max_delete_per_run,
        "stale_delete_files": stale_delete_files,
        "stale_states": stale_states,
    }
