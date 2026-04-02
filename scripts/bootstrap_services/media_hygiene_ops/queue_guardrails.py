"""qB queue guardrail helpers for media hygiene operations."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any


def run_qbit_queue_guardrails(
    ops,
    qbit_cfg: dict[str, Any],
    qb_username: str,
    qb_password: str,
) -> dict[str, Any]:
    queue_cfg = (qbit_cfg or {}).get("queue_guardrails") or {}
    enabled = ops.bool_cfg(queue_cfg, "enabled", False)
    summary = {
        "enabled": enabled,
        "dry_run": ops.bool_cfg(queue_cfg, "dry_run", False),
        "total": 0,
        "over_limit_candidates": 0,
        "stale_candidates": 0,
        "over_budget_candidates": 0,
        "over_limit_deleted": 0,
        "stale_deleted": 0,
        "over_budget_deleted": 0,
        "by_category": {},
        "by_category_budget": {},
    }
    if not enabled:
        return summary

    if not str(qb_username or "").strip() or not str(qb_password or "").strip():
        raise RuntimeError(
            "qB queue guardrails requires qB credentials "
            "(STACK_ADMIN_USERNAME/STACK_ADMIN_PASSWORD)."
        )

    qbit_url = ops.normalize_url((qbit_cfg or {}).get("url", "http://qbittorrent:8080"))
    dry_run = bool(summary["dry_run"])
    now = int(time.time())

    default_count_states = [
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
    count_states = {
        str(x).strip().lower()
        for x in ops.coerce_list(queue_cfg.get("count_states"))
        if str(x).strip()
    } or {x.lower() for x in default_count_states}

    default_prune_states = [
        "queuedDL",
        "stalledDL",
        "metaDL",
        "pausedDL",
        "error",
        "missingFiles",
    ]
    prune_states = {
        str(x).strip().lower()
        for x in ops.coerce_list(queue_cfg.get("prune_states"))
        if str(x).strip()
    } or {x.lower() for x in default_prune_states}

    include_uncategorized = ops.bool_cfg(queue_cfg, "include_uncategorized", False)
    default_max_queued = ops.to_int(queue_cfg.get("default_max_queued"))
    prune_when_over_limit = ops.bool_cfg(queue_cfg, "prune_when_over_limit", True)
    over_limit_delete_files = ops.bool_cfg(queue_cfg, "over_limit_delete_files", True)
    over_limit_max_delete_per_category = ops.to_int(
        queue_cfg.get("over_limit_max_delete_per_category"), 15
    )
    if over_limit_max_delete_per_category is None or over_limit_max_delete_per_category <= 0:
        over_limit_max_delete_per_category = 15

    max_by_category_raw = queue_cfg.get("max_queued_by_category") or {}
    max_by_category = {}
    if isinstance(max_by_category_raw, dict):
        for key, value in max_by_category_raw.items():
            norm_key = str(key or "").strip().lower()
            if not norm_key:
                continue
            parsed = ops.to_int(value)
            if parsed is None or parsed < 0:
                continue
            max_by_category[norm_key] = int(parsed)

    max_size_gib_raw = queue_cfg.get("max_total_size_gib_by_category") or {}
    max_size_bytes_by_category: dict[str, int] = {}
    if isinstance(max_size_gib_raw, dict):
        for key, value in max_size_gib_raw.items():
            norm_key = str(key or "").strip().lower()
            if not norm_key:
                continue
            parsed = ops.to_float(value)
            if parsed is None or parsed <= 0:
                continue
            max_size_bytes_by_category[norm_key] = int(float(parsed) * (1024**3))

    max_weight_percent_raw = queue_cfg.get("max_weight_percent_by_category") or {}
    max_weight_percent_by_category: dict[str, float] = {}
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
    } or {
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

    opener = ops.qbit_login(qbit_url, qb_username, qb_password)
    torrents = ops.qbit_list_torrents(opener, qbit_url, "all")
    summary["total"] = len(torrents)

    def parse_record(item: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        thash = str(item.get("hash") or "").strip()
        if not thash:
            return None
        state = str(item.get("state") or "").strip().lower()
        category_raw = str(item.get("category") or "").strip()
        category = category_raw.lower() or "uncategorized"
        progress = ops.to_float(item.get("progress"), 0.0) or 0.0
        added_on = ops.to_int(item.get("added_on"), 0) or 0
        completion_on = ops.to_int(item.get("completion_on"), 0) or 0
        last_activity = ops.to_int(item.get("last_activity"), 0) or 0
        dlspeed = ops.to_int(item.get("dlspeed"), 0) or 0
        eta = ops.to_int(item.get("eta"), -1) or -1
        reference_on = completion_on if completion_on > 0 else added_on
        age_hours = 0.0
        if reference_on > 0:
            age_hours = max(0.0, float(now - reference_on) / 3600.0)
        stalled_hours = 0.0
        if last_activity > 0:
            stalled_hours = max(0.0, float(now - last_activity) / 3600.0)
        return {
            "hash": thash,
            "name": str(item.get("name") or "").strip(),
            "category": category,
            "state": state,
            "size": ops.to_int(item.get("size"), 0) or 0,
            "progress": progress,
            "added_on": added_on,
            "completion_on": completion_on,
            "last_activity": last_activity,
            "age_hours": age_hours,
            "stalled_hours": stalled_hours,
            "dlspeed": dlspeed,
            "eta": eta,
        }

    records: list[dict[str, Any]] = []
    queue_by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in torrents:
        rec = parse_record(item)
        if not rec:
            continue
        records.append(rec)
        if rec["state"] not in count_states or rec["progress"] >= 1.0:
            continue
        if rec["category"] == "uncategorized" and not include_uncategorized:
            continue
        queue_by_category[rec["category"]].append(rec)

    state_rank = {
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

    over_limit_hashes: list[str] = []
    over_limit_seen: set[str] = set()
    if prune_when_over_limit:
        for category, items in queue_by_category.items():
            category_limit = max_by_category.get(category, default_max_queued)
            if category_limit is None or category_limit < 0:
                continue
            queue_count = len(items)
            if queue_count <= category_limit:
                continue
            over_by = queue_count - category_limit
            prune_pool = [x for x in items if x.get("state") in prune_states]
            prune_pool.sort(
                key=lambda x: (
                    state_rank.get(x.get("state") or "", 50),
                    x.get("progress") or 0.0,
                    x.get("dlspeed") or 0,
                    -(x.get("eta") if (x.get("eta") or -1) > 0 else 0),
                    x.get("added_on") or 0,
                )
            )

            chosen: list[str] = []
            for rec in prune_pool:
                if len(chosen) >= over_by:
                    break
                if len(chosen) >= over_limit_max_delete_per_category:
                    break
                thash = str(rec.get("hash") or "").strip()
                if not thash or thash in over_limit_seen:
                    continue
                chosen.append(thash)
                over_limit_seen.add(thash)
                over_limit_hashes.append(thash)

            summary["by_category"][category] = {
                "limit": int(category_limit),
                "queue_count": queue_count,
                "over_by": over_by,
                "selected": len(chosen),
            }

    over_budget_hashes: list[str] = []
    over_budget_seen: set[str] = set()
    if max_size_bytes_by_category or max_weight_percent_by_category:
        category_size_bytes: dict[str, int] = defaultdict(int)
        category_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for rec in records:
            category = str(rec.get("category") or "uncategorized")
            if category == "uncategorized" and not include_uncategorized:
                continue
            size_bytes = int(rec.get("size") or 0)
            if size_bytes <= 0:
                continue
            category_size_bytes[category] += size_bytes
            category_records[category].append(rec)

        managed_total_bytes = sum(category_size_bytes.values())

        for category, current_size_bytes in category_size_bytes.items():
            max_by_size = max_size_bytes_by_category.get(category)
            max_by_weight = max_weight_percent_by_category.get(category)
            target_weight_bytes: int | None = None
            if max_by_weight is not None and managed_total_bytes > 0:
                target_weight_bytes = int((float(max_by_weight) / 100.0) * managed_total_bytes)
            over_by_size = max(0, current_size_bytes - (max_by_size or current_size_bytes))
            over_by_weight = (
                max(0, current_size_bytes - target_weight_bytes)
                if target_weight_bytes is not None
                else 0
            )
            bytes_to_free = max(over_by_size, over_by_weight)
            if bytes_to_free <= 0:
                continue

            prune_pool = [
                item
                for item in category_records.get(category) or []
                if str(item.get("state") or "").lower() in budget_prune_states
            ]
            prune_pool.sort(
                key=lambda x: (
                    x.get("completion_on") or x.get("added_on") or 0,
                    x.get("size") or 0,
                ),
                reverse=False,
            )

            chosen_hashes: list[str] = []
            reclaimed_bytes = 0
            for rec in prune_pool:
                if len(chosen_hashes) >= over_budget_max_delete_per_category:
                    break
                if reclaimed_bytes >= bytes_to_free:
                    break
                thash = str(rec.get("hash") or "").strip()
                if not thash or thash in over_budget_seen:
                    continue
                size_bytes = int(rec.get("size") or 0)
                if size_bytes <= 0:
                    continue
                chosen_hashes.append(thash)
                over_budget_seen.add(thash)
                over_budget_hashes.append(thash)
                reclaimed_bytes += size_bytes

            summary["by_category_budget"][category] = {
                "size_bytes": int(current_size_bytes),
                "managed_total_bytes": int(managed_total_bytes),
                "max_size_bytes": int(max_by_size) if max_by_size is not None else None,
                "max_weight_percent": float(max_by_weight) if max_by_weight is not None else None,
                "bytes_to_free": int(bytes_to_free),
                "selected": len(chosen_hashes),
                "selected_bytes": int(reclaimed_bytes),
            }

    stale_hashes: list[str] = []
    stale_seen: set[str] = set()
    if stale_enabled:
        stale_pool: list[dict[str, Any]] = []
        for rec in records:
            category = rec.get("category") or ""
            if category == "uncategorized" and not include_uncategorized:
                continue
            if rec.get("state") not in stale_states:
                continue
            progress = float(rec.get("progress") or 0.0)
            if progress >= float(stale_min_progress):
                continue
            dlspeed = int(rec.get("dlspeed") or 0)
            if stale_max_download_speed_bps is not None and dlspeed > int(
                stale_max_download_speed_bps
            ):
                continue
            age_trigger = float(rec.get("age_hours") or 0.0) >= float(stale_max_age_hours)
            stalled_trigger = float(rec.get("stalled_hours") or 0.0) >= float(
                stale_max_stalled_hours
            )
            eta_val = int(rec.get("eta") or -1)
            eta_trigger = bool(
                stale_max_eta_seconds is not None and eta_val > int(stale_max_eta_seconds)
            )
            if age_trigger or stalled_trigger or eta_trigger:
                stale_pool.append(rec)

        stale_pool.sort(
            key=lambda x: (
                x.get("progress") or 0.0,
                x.get("dlspeed") or 0,
                -(x.get("eta") if (x.get("eta") or -1) > 0 else 0),
                -(x.get("age_hours") or 0.0),
                -(x.get("stalled_hours") or 0.0),
            )
        )
        for rec in stale_pool:
            if len(stale_hashes) >= stale_max_delete_per_run:
                break
            thash = str(rec.get("hash") or "").strip()
            if not thash or thash in stale_seen:
                continue
            stale_hashes.append(thash)
            stale_seen.add(thash)

    summary["over_limit_candidates"] = len(over_limit_hashes)
    summary["stale_candidates"] = len(stale_hashes)
    summary["over_budget_candidates"] = len(over_budget_hashes)

    if dry_run:
        for thash in over_limit_hashes:
            ops.log(f"[INFO] qB queue guardrails over-limit candidate (dry-run): {thash}")
        for thash in over_budget_hashes:
            ops.log(f"[INFO] qB queue guardrails over-budget candidate (dry-run): {thash}")
        for thash in stale_hashes:
            ops.log(f"[INFO] qB queue guardrails stale candidate (dry-run): {thash}")
        ops.log(
            "[OK] qB queue guardrails: dry-run complete "
            f"(over_limit_candidates={len(over_limit_hashes)}, "
            f"over_budget_candidates={len(over_budget_hashes)}, "
            f"stale_candidates={len(stale_hashes)})."
        )
        return summary

    budget_to_delete = [x for x in over_budget_hashes if x not in set(over_limit_hashes)]
    if budget_to_delete:
        ops.qbit_delete_torrents(
            opener,
            qbit_url,
            budget_to_delete,
            delete_files=over_budget_delete_files,
        )
        summary["over_budget_deleted"] = len(budget_to_delete)
        ops.log(
            "[OK] qB queue guardrails: pruned over-budget category torrents "
            f"(deleted={len(budget_to_delete)}, delete_files={over_budget_delete_files})."
        )
    else:
        ops.log("[OK] qB queue guardrails: no over-budget category pruning required.")

    if over_limit_hashes:
        ops.qbit_delete_torrents(
            opener,
            qbit_url,
            over_limit_hashes,
            delete_files=over_limit_delete_files,
        )
        summary["over_limit_deleted"] = len(over_limit_hashes)
        ops.log(
            "[OK] qB queue guardrails: pruned over-limit queued torrents "
            f"(deleted={len(over_limit_hashes)}, delete_files={over_limit_delete_files})."
        )
    else:
        ops.log("[OK] qB queue guardrails: no over-limit queue pruning required.")

    stale_to_delete = [
        x
        for x in stale_hashes
        if x not in set(over_limit_hashes) and x not in set(budget_to_delete)
    ]
    if stale_to_delete:
        ops.qbit_delete_torrents(
            opener,
            qbit_url,
            stale_to_delete,
            delete_files=stale_delete_files,
        )
        summary["stale_deleted"] = len(stale_to_delete)
        ops.log(
            "[OK] qB queue guardrails: pruned stale/slow torrents "
            f"(deleted={len(stale_to_delete)}, delete_files={stale_delete_files})."
        )
    else:
        ops.log("[OK] qB queue guardrails: no stale/slow torrent pruning required.")
    return summary
