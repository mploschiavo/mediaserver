"""Disk usage guardrails and qB cleanup policy operations."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from media_stack.core.events import StorageCleanupInvoked
from media_stack.services.apps.download_clients.registry_helpers import default_torrent_client_url


_log = logging.getLogger("media_stack.disk_guardrails")


# Cleanup-ordering strategies. Single dispatch table — no inheritance
# hierarchy needed. Each entry is a sort-key callable ``c -> tuple``
# applied to a candidate dict carrying ``completion_on``, ``size``,
# ``ratio``, and (for ``watched_first``) a ``_watched`` boolean
# decoration injected by the watched-first lookup pass.
#
# ``oldest_first``: FIFO by completion timestamp, tie-break by size.
#   Same shape the legacy lines 173-176 produced.
# ``largest_first``: descending by size, tie-break by completion time.
# ``poor_ratio_first``: ascending by ratio (lowest seed-ratio first),
#   tie-break by completion time so identical-ratio peers come out
#   in FIFO order.
# ``watched_first``: True-first by ``_watched``, falls through to
#   oldest_first ordering for both watched and unwatched groups.
_ORDER_STRATEGIES: dict[str, Callable[[dict[str, Any]], tuple]] = {
    "oldest_first": (
        lambda c: (c.get("completion_on") or 0, c.get("size") or 0)
    ),
    "largest_first": (
        lambda c: (-(c.get("size") or 0), c.get("completion_on") or 0)
    ),
    "poor_ratio_first": (
        lambda c: (
            float(c.get("ratio") or 0.0), c.get("completion_on") or 0,
        )
    ),
    "watched_first": (
        lambda c: (
            0 if c.get("_watched") else 1,
            c.get("completion_on") or 0,
            c.get("size") or 0,
        )
    ),
}

_DEFAULT_ORDER_STRATEGY = "oldest_first"

# ADR-0008 Phase 4: cleanup-policy override file. Re-exported so
# existing import sites that read ``CLEANUP_POLICY_FILE`` from this
# module keep working after the class moved to its own module.
from media_stack.services.cleanup_policy_file import (  # noqa: E402
    CleanupPolicyFile,
    CLEANUP_POLICY_FILE,
)
from media_stack.services.storage_event_publisher import (  # noqa: E402
    STORAGE_EVENT_PUBLISHER,
)


# Re-exported so existing import sites keep working after the class
# moved to its own module (Phase 4 refactor for the 400-line ratchet).
from media_stack.services.watched_lookup_adapter import (  # noqa: E402
    WatchedLookupAdapter,
)


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
    watched_lookup: WatchedLookupAdapter | None = None

    def enforce(
        self,
        cfg: dict[str, Any],
        config_root: str,
        qbit_cfg: dict[str, Any],
        qb_username: str,
        qb_password: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Run the cleanup pass.

        ``force=True`` bypasses the disk-percentage threshold and
        runs cleanup regardless of usage. The manual
        ``POST /api/disk-guardrails/cleanup`` route uses this flag.
        Returns a report dict with ``deleted``, ``freed_gb``,
        ``kept``, ``candidates_evaluated``, ``strategy``.
        """
        empty_report: dict[str, Any] = {
            "deleted": 0,
            "freed_gb": 0.0,
            "kept": 0,
            "candidates_evaluated": 0,
            "strategy": _DEFAULT_ORDER_STRATEGY,
        }
        guard_cfg = cfg.get("disk_guardrails") or {}
        if not self.bool_cfg(guard_cfg, "enabled", False):
            return empty_report

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
        if not force and used_pct <= max_used_percent:
            self.log("[OK] Disk guardrails: usage is within threshold.")
            return empty_report

        qbit_cleanup_cfg = guard_cfg.get("qbit_cleanup")
        if not isinstance(qbit_cleanup_cfg, dict):
            qbit_cleanup_cfg = {}
        # Phase 4: overlay the on-disk cleanup-policy file (if any).
        # Operator writes through ``POST /api/disk-guardrails/cleanup-policy``
        # land here as a selective overlay over the controller defaults.
        try:
            policy_overrides = CLEANUP_POLICY_FILE.load()
        except (OSError, ValueError) as exc:
            log_swallowed(exc, context="cleanup-policy-load")
            policy_overrides = {}
        if policy_overrides:
            merged = dict(qbit_cleanup_cfg)
            for key, value in policy_overrides.items():
                merged[key] = value
            qbit_cleanup_cfg = merged
        if not self.bool_cfg(qbit_cleanup_cfg, "enabled", True):
            self.log(
                "[WARN] Disk guardrails: usage above threshold but qB cleanup is disabled "
                "(disk_guardrails.qbit_cleanup.enabled=false)."
            )
            return empty_report

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
        order_strategy = str(
            qbit_cleanup_cfg.get("order_strategy") or _DEFAULT_ORDER_STRATEGY
        ).strip().lower()
        if order_strategy not in _ORDER_STRATEGIES:
            order_strategy = _DEFAULT_ORDER_STRATEGY

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
                    "ratio": ratio,
                }
            )

        candidates_evaluated = len(candidates)

        # Apply the chosen ordering strategy. ``watched_first`` may
        # decorate candidates with a ``_watched`` flag; on lookup
        # failure it logs INFO and silently falls back to the
        # ``oldest_first`` sort key so the cleanup pass still runs.
        active_strategy = order_strategy
        if active_strategy == "watched_first":
            adapter = self.watched_lookup or WatchedLookupAdapter(
                log_fn=self.log,
            )
            candidates, ok = adapter.decorate(candidates)
            if not ok:
                active_strategy = _DEFAULT_ORDER_STRATEGY
        sort_key = _ORDER_STRATEGIES[active_strategy]
        candidates.sort(key=sort_key)
        if max_delete_per_run is not None and max_delete_per_run > 0:
            candidates = candidates[:max_delete_per_run]

        if not candidates:
            self.log(
                "[WARN] Disk guardrails: usage above threshold but no qB torrents matched cleanup "
                f"criteria (min_age_hours={min_age_hours}, min_ratio={min_ratio}, "
                f"min_seeding_time_minutes={min_seed_minutes}, categories={categories or 'all'})."
            )
            return {
                "deleted": 0,
                "freed_gb": 0.0,
                "kept": 0,
                "candidates_evaluated": candidates_evaluated,
                "strategy": active_strategy,
            }

        to_delete = [c["hash"] for c in candidates if c.get("hash")]
        reclaimed_est = sum(c.get("size") or 0 for c in candidates)
        self.qbit_delete_torrents(opener, qbit_url, to_delete, delete_files=delete_files)
        self.log(
            "[OK] Disk guardrails: deleted completed qB torrents "
            f"(count={len(to_delete)}, delete_files={delete_files}, "
            f"estimated_bytes={self.fmt_bytes(reclaimed_est)})"
        )
        # Phase 4: publish a ``storage.cleanup_invoked`` event so the
        # UI's storage card refreshes on every cleanup pass without
        # waiting on its 30 s poll. Failure isolation: any
        # bus/event-type wiring problem must NOT block a successful
        # cleanup — log + continue.
        kept_count = max(0, candidates_evaluated - len(to_delete))
        self._publish_cleanup_invoked(
            StorageCleanupInvoked(
                deleted=len(to_delete),
                freed_bytes=int(reclaimed_est),
                kept=kept_count,
                strategy=active_strategy,
                force=bool(force),
            ),
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
            log_swallowed(exc)

        return {
            "deleted": len(to_delete),
            "freed_gb": round(float(reclaimed_est) / (1024.0 ** 3), 3),
            "kept": max(0, candidates_evaluated - len(to_delete)),
            "candidates_evaluated": candidates_evaluated,
            "strategy": active_strategy,
        }

    def _publish_cleanup_invoked(
        self,
        event: "StorageCleanupInvoked",
    ) -> None:
        """Delegate to the shared ``StorageEventPublisher`` (Phase 4)."""
        STORAGE_EVENT_PUBLISHER.publish_cleanup_invoked(event)
