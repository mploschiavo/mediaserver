"""Disk usage guardrails and qB cleanup policy operations."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from media_stack.services.apps.download_clients.registry_helpers import default_torrent_client_url

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
ToIntFn = Callable[[Any, Any], Any]
ToFloatFn = Callable[[Any, Any], Any]
NormalizeUrlFn = Callable[[str], str]
DiskUsagePercentFn = Callable[[str], tuple[float, int, int]]
FmtBytesFn = Callable[[int], str]
QbitLoginFn = Callable[[str, str, str], Any]
QbitListCompletedFn = Callable[[Any, str], list[dict[str, Any]]]
QbitDeleteFn = Callable[[Any, str, list[str], bool], None]


@dataclass
class DiskGuardrailsService:
    log: LogFn
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    to_int: ToIntFn
    to_float: ToFloatFn
    normalize_url: NormalizeUrlFn
    disk_usage_percent: DiskUsagePercentFn
    fmt_bytes: FmtBytesFn
    qbit_login: QbitLoginFn
    qbit_list_completed_torrents: QbitListCompletedFn
    qbit_delete_torrents: QbitDeleteFn

    def enforce(
        self,
        cfg: dict[str, Any],
        config_root: str,
        qbit_cfg: dict[str, Any],
        qb_username: str,
        qb_password: str,
    ) -> None:
        guard_cfg = cfg.get("disk_guardrails") or {}
        if not self.bool_cfg(guard_cfg, "enabled", False):
            return

        monitor_path = str(guard_cfg.get("monitor_path") or "").strip()
        if monitor_path and not Path(monitor_path).exists():
            self.log(
                "[WARN] Disk guardrails: configured monitor path does not exist; "
                f"resolving fallback path (configured={monitor_path})."
            )
            monitor_path = ""
        if not monitor_path:
            candidates = [
                str(os.environ.get("DISK_GUARDRAILS_MONITOR_PATH", "")).strip(),
                str(os.environ.get("STACK_ROOT", "")).strip(),
                str(Path("/srv-stack")),
                str(Path("/srv-stack/media")),
                str(Path("/srv-stack/data")),
                str(Path("/srv-stack/data/torrents")),
                str(Path("/srv-stack/data/usenet")),
                str(os.environ.get("MEDIA_ROOT", "")).strip(),
                str(os.environ.get("DATA_ROOT", "")).strip(),
                str(config_root),
            ]
            for candidate in candidates:
                if candidate and Path(candidate).exists():
                    monitor_path = candidate
                    break
        if not monitor_path:
            monitor_path = config_root

        max_used_percent = self.to_float(guard_cfg.get("max_used_percent"), 65.0)
        target_used_percent = self.to_float(guard_cfg.get("target_used_percent"), 58.0)
        if target_used_percent is None:
            target_used_percent = 58.0
        if max_used_percent is None:
            max_used_percent = 65.0
        target_used_percent = max(0.0, min(float(target_used_percent), 99.0))
        max_used_percent = max(target_used_percent, min(float(max_used_percent), 99.0))

        try:
            used_pct, total, avail = self.disk_usage_percent(monitor_path)
        except Exception as exc:
            raise RuntimeError(
                f"Disk guardrails: failed reading filesystem usage at '{monitor_path}': {exc}"
            ) from exc

        self.log(
            "[INFO] Disk guardrails: usage check "
            f"(path={monitor_path}, used={used_pct:.2f}%, total={self.fmt_bytes(total)}, "
            f"available={self.fmt_bytes(avail)}, max={max_used_percent:.2f}%, "
            f"target={target_used_percent:.2f}%)"
        )
        if used_pct <= max_used_percent:
            self.log("[OK] Disk guardrails: usage is within threshold.")
            return

        qbit_cleanup_cfg = guard_cfg.get("qbit_cleanup")
        if not isinstance(qbit_cleanup_cfg, dict):
            qbit_cleanup_cfg = {}
        if not self.bool_cfg(qbit_cleanup_cfg, "enabled", True):
            self.log(
                "[WARN] Disk guardrails: usage above threshold but qB cleanup is disabled "
                "(disk_guardrails.qbit_cleanup.enabled=false)."
            )
            return

        qbit_url = self.normalize_url(qbit_cfg.get("url", default_torrent_client_url()))
        min_age_hours = (
            self.to_float(qbit_cleanup_cfg.get("min_completion_age_hours"), 36.0) or 36.0
        )
        min_ratio = self.to_float(qbit_cleanup_cfg.get("min_ratio"), 1.0)
        min_seed_minutes = self.to_int(qbit_cleanup_cfg.get("min_seeding_time_minutes"), 720)
        max_delete_per_run = self.to_int(qbit_cleanup_cfg.get("max_delete_per_run"), 80)
        categories = [
            str(item).strip()
            for item in self.coerce_list(qbit_cleanup_cfg.get("categories"))
            if str(item).strip()
        ]
        delete_files = self.bool_cfg(qbit_cleanup_cfg, "delete_files", True)

        opener = self.qbit_login(qbit_url, qb_username, qb_password)
        torrents = self.qbit_list_completed_torrents(opener, qbit_url)
        now = int(time.time())

        candidates: list[dict[str, Any]] = []
        for item in torrents:
            if not isinstance(item, dict):
                continue
            thash = str(item.get("hash") or "").strip()
            if not thash:
                continue
            cat = str(item.get("category") or "").strip()
            if categories and cat not in categories:
                continue

            completion_on = self.to_int(item.get("completion_on"), 0) or 0
            age_hours = 0.0
            if completion_on > 0:
                age_hours = max(0.0, float(now - completion_on) / 3600.0)
            if age_hours < float(min_age_hours):
                continue

            ratio = self.to_float(item.get("ratio"), 0.0) or 0.0
            seeding_time_minutes = int((self.to_int(item.get("seeding_time"), 0) or 0) / 60)
            meets_ratio = (min_ratio is None) or (ratio >= float(min_ratio))
            meets_seed = (min_seed_minutes is None) or (
                seeding_time_minutes >= int(min_seed_minutes)
            )
            if not (meets_ratio or meets_seed):
                continue

            size_bytes = self.to_int(item.get("size"), 0) or 0
            candidates.append(
                {
                    "hash": thash,
                    "category": cat,
                    "completion_on": completion_on,
                    "size": size_bytes,
                }
            )

        candidates.sort(
            key=lambda item: (item.get("completion_on") or 0, item.get("size") or 0),
            reverse=False,
        )
        if max_delete_per_run is not None and max_delete_per_run > 0:
            candidates = candidates[:max_delete_per_run]

        if not candidates:
            self.log(
                "[WARN] Disk guardrails: usage above threshold but no qB torrents matched cleanup "
                f"criteria (min_age_hours={min_age_hours}, min_ratio={min_ratio}, "
                f"min_seeding_time_minutes={min_seed_minutes}, categories={categories or 'all'})."
            )
            return

        to_delete = [c["hash"] for c in candidates if c.get("hash")]
        reclaimed_est = sum(c.get("size") or 0 for c in candidates)
        self.qbit_delete_torrents(opener, qbit_url, to_delete, delete_files=delete_files)
        self.log(
            "[OK] Disk guardrails: deleted completed qB torrents "
            f"(count={len(to_delete)}, delete_files={delete_files}, "
            f"estimated_bytes={self.fmt_bytes(reclaimed_est)})"
        )

        try:
            used_after, _, avail_after = self.disk_usage_percent(monitor_path)
            self.log(
                "[INFO] Disk guardrails: usage after cleanup "
                f"(used={used_after:.2f}%, available={self.fmt_bytes(avail_after)}, "
                f"target={target_used_percent:.2f}%)"
            )
            if used_after > target_used_percent:
                self.log(
                    "[WARN] Disk guardrails: still above target after cleanup. "
                    "Consider stronger retention rules or larger storage."
                )
        except Exception as exc:
            import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
