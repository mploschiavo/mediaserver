"""Detection logic for qB queue guardrail checks (over-limit, over-budget, stale)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ._guardrail_config import STATE_RANK



class GuardrailCheckService:
    def find_over_limit_torrents(self, 
        queue_by_category: dict[str, list[dict[str, Any]]],
        cfg: dict[str, Any],
        summary: dict[str, Any],
    ) -> list[str]:
        """Identify torrents that exceed per-category count limits.
    
        Returns a list of torrent hashes to delete.
        """
        max_by_category: dict[str, int] = cfg["max_by_category"]
        default_max_queued: int | None = cfg["default_max_queued"]
        prune_states: set[str] = cfg["prune_states"]
        over_limit_max_delete_per_category: int = cfg["over_limit_max_delete_per_category"]
    
        over_limit_hashes: list[str] = []
        over_limit_seen: set[str] = set()
    
        if not cfg["prune_when_over_limit"]:
            return over_limit_hashes
    
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
                    STATE_RANK.get(x.get("state") or "", 50),
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
    
        return over_limit_hashes
    
    
    def find_over_budget_torrents(self, 
        records: list[dict[str, Any]],
        cfg: dict[str, Any],
        summary: dict[str, Any],
    ) -> list[str]:
        """Identify torrents that exceed per-category size/weight budgets.
    
        Returns a list of torrent hashes to delete.
        """
        max_size_bytes_by_category: dict[str, int] = cfg["max_size_bytes_by_category"]
        max_weight_percent_by_category: dict[str, float] = cfg["max_weight_percent_by_category"]
        budget_prune_states: set[str] = cfg["budget_prune_states"]
        over_budget_max_delete_per_category: int = cfg["over_budget_max_delete_per_category"]
        include_uncategorized: bool = cfg["include_uncategorized"]
    
        over_budget_hashes: list[str] = []
        over_budget_seen: set[str] = set()
    
        if not max_size_bytes_by_category and not max_weight_percent_by_category:
            return over_budget_hashes
    
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
    
        return over_budget_hashes
    
    
    def find_stale_torrents(self, 
        records: list[dict[str, Any]],
        cfg: dict[str, Any],
    ) -> list[str]:
        """Identify stale or slow torrents eligible for pruning.
    
        Returns a list of torrent hashes to delete.
        """
        if not cfg["stale_enabled"]:
            return []
    
        stale_states: set[str] = cfg["stale_states"]
        stale_min_progress: float = cfg["stale_min_progress"]
        stale_max_download_speed_bps: int | None = cfg["stale_max_download_speed_bps"]
        stale_max_age_hours: float = cfg["stale_max_age_hours"]
        stale_max_stalled_hours: float = cfg["stale_max_stalled_hours"]
        stale_max_eta_seconds: int | None = cfg["stale_max_eta_seconds"]
        stale_max_delete_per_run: int = cfg["stale_max_delete_per_run"]
        include_uncategorized: bool = cfg["include_uncategorized"]
    
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
    
        stale_hashes: list[str] = []
        stale_seen: set[str] = set()
        for rec in stale_pool:
            if len(stale_hashes) >= stale_max_delete_per_run:
                break
            thash = str(rec.get("hash") or "").strip()
            if not thash or thash in stale_seen:
                continue
            stale_hashes.append(thash)
            stale_seen.add(thash)
    
        return stale_hashes


_instance = GuardrailCheckService()
find_over_limit_torrents = _instance.find_over_limit_torrents
find_over_budget_torrents = _instance.find_over_budget_torrents
find_stale_torrents = _instance.find_stale_torrents
