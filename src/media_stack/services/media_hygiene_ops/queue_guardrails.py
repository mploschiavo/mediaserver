"""qB queue guardrail helpers for media hygiene operations."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from media_stack.services.apps.download_clients.registry_helpers import default_torrent_client_url

from ._guardrail_checks import find_over_budget_torrents, find_over_limit_torrents, find_stale_torrents
from ._guardrail_config import parse_guardrail_config



class QueueGuardrailsService:
    @staticmethod
    def _parse_torrent_record(ops, item: dict[str, Any], now: int) -> dict[str, Any] | None:
        """Parse a raw qBittorrent torrent dict into a normalized record."""
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
    
    
    def run_qbit_queue_guardrails(self, 
        ops,
        qbit_cfg: dict[str, Any],
        qb_username: str,
        qb_password: str,
    ) -> dict[str, Any]:
        queue_cfg = (qbit_cfg or {}).get("queue_guardrails") or {}
        enabled = ops.bool_cfg(queue_cfg, "enabled", False)
        summary: dict[str, Any] = {
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
    
        qbit_url = ops.normalize_url((qbit_cfg or {}).get("url", default_torrent_client_url()))
        dry_run = bool(summary["dry_run"])
        now = int(time.time())
    
        cfg = parse_guardrail_config(ops, queue_cfg)
    
        opener = ops.qbit_login(qbit_url, qb_username, qb_password)
        torrents = ops.qbit_list_torrents(opener, qbit_url, "all")
        summary["total"] = len(torrents)
    
        records: list[dict[str, Any]] = []
        queue_by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in torrents:
            rec = _parse_torrent_record(ops, item, now)
            if not rec:
                continue
            records.append(rec)
            if rec["state"] not in cfg["count_states"] or rec["progress"] >= 1.0:
                continue
            if rec["category"] == "uncategorized" and not cfg["include_uncategorized"]:
                continue
            queue_by_category[rec["category"]].append(rec)
    
        over_limit_hashes = find_over_limit_torrents(queue_by_category, cfg, summary)
        over_budget_hashes = find_over_budget_torrents(records, cfg, summary)
        stale_hashes = find_stale_torrents(records, cfg)
    
        summary["over_limit_candidates"] = len(over_limit_hashes)
        summary["stale_candidates"] = len(stale_hashes)
        summary["over_budget_candidates"] = len(over_budget_hashes)
    
        if dry_run:
            _log_dry_run(ops, over_limit_hashes, over_budget_hashes, stale_hashes)
            return summary
    
        _execute_deletions(
            ops, opener, qbit_url, summary, cfg,
            over_limit_hashes, over_budget_hashes, stale_hashes,
        )
        return summary
    
    
    @staticmethod
    def _log_dry_run(
        ops,
        over_limit_hashes: list[str],
        over_budget_hashes: list[str],
        stale_hashes: list[str],
    ) -> None:
        """Log candidates without deleting (dry-run mode)."""
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
    
    
    @staticmethod
    def _execute_deletions(
        ops,
        opener,
        qbit_url: str,
        summary: dict[str, Any],
        cfg: dict[str, Any],
        over_limit_hashes: list[str],
        over_budget_hashes: list[str],
        stale_hashes: list[str],
    ) -> None:
        """Execute the actual torrent deletions."""
        over_budget_delete_files = cfg["over_budget_delete_files"]
        over_limit_delete_files = cfg["over_limit_delete_files"]
        stale_delete_files = cfg["stale_delete_files"]
    
        budget_to_delete = [x for x in over_budget_hashes if x not in set(over_limit_hashes)]
        if budget_to_delete:
            ops.qbit_delete_torrents(
                opener, qbit_url, budget_to_delete, delete_files=over_budget_delete_files,
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
                opener, qbit_url, over_limit_hashes, delete_files=over_limit_delete_files,
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
                opener, qbit_url, stale_to_delete, delete_files=stale_delete_files,
            )
            summary["stale_deleted"] = len(stale_to_delete)
            ops.log(
                "[OK] qB queue guardrails: pruned stale/slow torrents "
                f"(deleted={len(stale_to_delete)}, delete_files={stale_delete_files})."
            )
        else:
            ops.log("[OK] qB queue guardrails: no stale/slow torrent pruning required.")


_instance = QueueGuardrailsService()
run_qbit_queue_guardrails = _instance.run_qbit_queue_guardrails
_execute_deletions = _instance._execute_deletions
_log_dry_run = _instance._log_dry_run
_parse_torrent_record = _instance._parse_torrent_record
