"""qB duplicate-prune helpers for media hygiene operations."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from media_stack.services.apps.download_clients.registry_helpers import default_torrent_client_url


def run_qbit_duplicate_prune(
    ops,
    hygiene_cfg: dict[str, Any],
    qbit_cfg: dict[str, Any],
    qb_username: str,
    qb_password: str,
) -> dict[str, Any]:
    prune_cfg = hygiene_cfg.get("qbit_duplicate_prune") or {}
    enabled = ops.bool_cfg(prune_cfg, "enabled", False)
    summary: dict[str, Any] = {
        "enabled": enabled,
        "dry_run": ops.bool_cfg(prune_cfg, "dry_run", False),
        "groups": 0,
        "candidates": 0,
        "deleted": 0,
    }
    if not enabled:
        return summary

    if not str(qb_username or "").strip() or not str(qb_password or "").strip():
        raise RuntimeError(
            "qB duplicate prune requires qB credentials (STACK_ADMIN_USERNAME/STACK_ADMIN_PASSWORD)."
        )

    qbit_url = ops.normalize_url((qbit_cfg or {}).get("url", default_torrent_client_url()))
    dry_run = bool(summary["dry_run"])
    delete_files = ops.bool_cfg(prune_cfg, "delete_files", False)
    max_delete_per_run = ops.to_int(prune_cfg.get("max_delete_per_run"), 30)
    if max_delete_per_run is None or max_delete_per_run <= 0:
        max_delete_per_run = 30
    min_completion_age_hours = ops.to_float(prune_cfg.get("min_completion_age_hours"), 24.0)
    if min_completion_age_hours is None:
        min_completion_age_hours = 24.0
    keep_strategy = str(prune_cfg.get("keep", "oldest") or "oldest").strip().lower()
    if keep_strategy not in ("oldest", "newest"):
        keep_strategy = "oldest"
    include_category = ops.bool_cfg(prune_cfg, "include_category_in_key", True)
    match_on_hash = ops.bool_cfg(prune_cfg, "match_on_hash", True)
    match_on_name_size = ops.bool_cfg(prune_cfg, "match_on_name_size", True)
    if not match_on_hash and not match_on_name_size:
        match_on_name_size = True

    raw_categories = [
        str(x).strip() for x in ops.coerce_list(prune_cfg.get("categories")) if str(x).strip()
    ]
    if not raw_categories:
        raw_categories = [
            str(v).strip()
            for v in ((qbit_cfg or {}).get("categories") or {}).values()
            if str(v).strip()
        ]
    categories = sorted(set(raw_categories))

    opener = ops.qbit_login(qbit_url, qb_username, qb_password)
    torrents = ops.qbit_list_completed_torrents(opener, qbit_url)
    now = int(time.time())
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)

    for item in torrents:
        if not isinstance(item, dict):
            continue
        thash = str(item.get("hash") or "").strip()
        if not thash:
            continue
        category = str(item.get("category") or "").strip()
        if categories and category not in categories:
            continue

        completion_on = ops.to_int(item.get("completion_on"), 0) or 0
        added_on = ops.to_int(item.get("added_on"), 0) or 0
        reference_on = completion_on if completion_on > 0 else added_on
        age_hours = 0.0
        if reference_on > 0:
            age_hours = max(0.0, float(now - reference_on) / 3600.0)
        if age_hours < min_completion_age_hours:
            continue

        normalized_name = ops.normalize_token(item.get("name") or "")
        size_bytes = ops.to_int(item.get("size"), 0) or 0
        record = {
            "hash": thash,
            "name": str(item.get("name") or "").strip(),
            "normalized_name": normalized_name,
            "size": size_bytes,
            "category": category,
            "completion_on": completion_on,
            "added_on": added_on,
        }

        if match_on_hash:
            groups[("hash", thash)].append(record)
        if match_on_name_size and normalized_name and size_bytes > 0:
            if include_category:
                groups[("name_size", category.lower(), normalized_name, size_bytes)].append(record)
            else:
                groups[("name_size", normalized_name, size_bytes)].append(record)

    delete_hashes: list[str] = []
    delete_seen: set[str] = set()
    duplicate_groups = 0
    for group_items in groups.values():
        if len(group_items) <= 1:
            continue
        duplicate_groups += 1
        sorted_items = sorted(
            group_items,
            key=lambda x: (
                x.get("completion_on") or x.get("added_on") or 0,
                x.get("hash") or "",
            ),
            reverse=(keep_strategy == "newest"),
        )
        keep_hash = str(sorted_items[0].get("hash") or "").strip()
        for candidate in sorted_items[1:]:
            candidate_hash = str(candidate.get("hash") or "").strip()
            if not candidate_hash or candidate_hash == keep_hash or candidate_hash in delete_seen:
                continue
            delete_hashes.append(candidate_hash)
            delete_seen.add(candidate_hash)
            if len(delete_hashes) >= max_delete_per_run:
                break
        if len(delete_hashes) >= max_delete_per_run:
            break

    summary["groups"] = duplicate_groups
    summary["candidates"] = len(delete_hashes)
    if not delete_hashes:
        ops.log(
            "[OK] Media hygiene qB duplicate prune: no duplicate completed torrents found "
            f"(groups={duplicate_groups}, categories={categories or 'all'})."
        )
        return summary

    if dry_run:
        for thash in delete_hashes:
            ops.log(f"[INFO] Media hygiene qB duplicate prune candidate (dry-run): {thash}")
        ops.log(
            "[OK] Media hygiene qB duplicate prune: dry-run complete "
            f"(groups={duplicate_groups}, candidates={len(delete_hashes)})."
        )
        return summary

    ops.qbit_delete_torrents(opener, qbit_url, delete_hashes, delete_files=delete_files)
    summary["deleted"] = len(delete_hashes)
    ops.log(
        "[OK] Media hygiene qB duplicate prune: removed duplicate torrents "
        f"(deleted={len(delete_hashes)}, groups={duplicate_groups}, delete_files={delete_files})."
    )
    return summary
