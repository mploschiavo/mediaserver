"""Append-only persistence for ``RunRecord`` entries.

JSONL at ``config/.controller/run-history.jsonl`` — one record per
line. Capped at ``RUN_HISTORY_HARD_CAP`` (50,000) lines so operators
can read the buffer at the same scale they read logs without ssh.

Why JSONL instead of a single JSON array
----------------------------------------
* **Crash-safe append**: ``open(mode="a")`` + ``write(line + "\n")``
  is atomic for short writes on POSIX, so a controller crash mid-
  write loses at most one record. With a JSON array we'd have to
  rewrite the whole file — a big window.
* **Streamable read**: line-at-a-time parsing means no need to
  load 12 MB into memory just to query the last 100 records.
* **Append cap is cheap**: when the line count exceeds the cap, we
  rewrite the file once with the tail. The frequency of that
  rewrite is bounded by the rate of new records, so amortized cost
  is fine.

This module is the ONLY layer that writes to the JSONL file.
Callers go through ``record_run_start`` / ``record_run_complete`` /
``record_skip`` to enforce the start→terminal lifecycle.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

from media_stack.application.jobs import runtime_stats
from media_stack.core.events import (
    JobCompleted,
    JobStarted,
    get_default_bus,
)
from media_stack.core.logging_utils import log_swallowed
from media_stack.domain.jobs.run_record import (
    RUN_HISTORY_HARD_CAP,
    RunRecord,
    RunStatus,
    make_run_id,
    resolve_run_history_path,
)

_log = logging.getLogger("media_stack.run_history")

# Single mutex for the whole file. Writes are short and infrequent
# (one per job run, not per log line), so a process-wide lock is
# fine and we don't need per-file fcntl semantics.
_LOCK = threading.RLock()


def _path() -> Path:
    return Path(resolve_run_history_path())


def _ensure_parent() -> Path:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read_all_records() -> list[RunRecord]:
    """Linear scan of the file. Returns oldest → newest. Errors on
    a single malformed line are logged + skipped; the rest of the
    file remains usable."""
    path = _path()
    if not path.is_file():
        return []
    out: list[RunRecord] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        continue
                    out.append(RunRecord.from_dict(data))
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    _log.debug(
                        "run-history line %d malformed: %s", line_no, exc,
                    )
    except OSError as exc:
        log_swallowed(exc)
    return out


def _write_all_records(records: list[RunRecord]) -> None:
    """Rewrite the whole file. Used only when:
      (a) updating an existing record (start → complete)
      (b) trimming past the hard cap.
    Both happen at coarse cadence; the I/O cost is acceptable.
    """
    path = _ensure_parent()
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r.to_dict(), separators=(",", ":")))
                f.write("\n")
        os.replace(tmp, path)
    except OSError as exc:
        log_swallowed(exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _append_record(record: RunRecord) -> None:
    """Append a brand-new record. Trims to the cap if needed."""
    path = _ensure_parent()
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(), separators=(",", ":")))
            f.write("\n")
    except OSError as exc:
        log_swallowed(exc)
        return
    # Trim opportunistically — count lines without holding them all
    # in memory.
    _trim_to_cap_if_needed()


def _trim_to_cap_if_needed() -> None:
    path = _path()
    if not path.is_file():
        return
    try:
        with path.open("r", encoding="utf-8") as f:
            line_count = sum(1 for _ in f)
    except OSError as exc:
        log_swallowed(exc)
        return
    if line_count <= RUN_HISTORY_HARD_CAP:
        return
    # Read the tail block and rewrite. Keeping a 5% headroom (47.5k
    # records after trim) avoids trim-on-every-write for active
    # stacks.
    keep = int(RUN_HISTORY_HARD_CAP * 0.95)
    records = _read_all_records()
    if len(records) > keep:
        _write_all_records(records[-keep:])


# ---------------------------------------------------------------------------
# Public API — start/complete lifecycle
# ---------------------------------------------------------------------------


def record_run_start(
    job_name: str,
    *,
    parent_run_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    triggered_by: str = "unknown",
    actor: Optional[str] = None,
    promise_id: Optional[str] = None,
) -> RunRecord:
    """Persist a ``status=running`` record for a job that just
    started. Returns the record (the caller wants the run_id for
    the eventual ``record_run_complete`` call).

    ``promise_id`` is set when the orchestrator emits the record so
    operators can query "every evaluation of promise X" through the
    existing run-history API."""
    record = RunRecord(
        run_id=make_run_id(),
        job_name=job_name,
        status=RunStatus.RUNNING,
        started_at=time.time(),
        parent_run_id=parent_run_id,
        batch_id=batch_id,
        triggered_by=triggered_by,
        actor=actor,
        promise_id=promise_id,
    )
    with _LOCK:
        _append_record(record)
    # Fire-and-forget publish: a slow/raising subscriber must never
    # block the run-recording path. ``EventBus._safe_invoke`` already
    # swallows handler exceptions; we only guard against the bus
    # itself being unreachable (shouldn't happen, but cheap to
    # defend).
    try:
        get_default_bus().publish(
            JobStarted(
                run_id=record.run_id,
                job_name=record.job_name,
                parent_run_id=record.parent_run_id or "",
                batch_id=record.batch_id or "",
                triggered_by=record.triggered_by,
                actor=record.actor or "",
            ),
        )
    except Exception as exc:  # noqa: BLE001 - defensive
        log_swallowed(exc)
    return record


def record_run_complete(
    run_id: str,
    *,
    status: str,
    error: Optional[str] = None,
    stdout_tail: Optional[str] = None,
    attempts: int = 1,
    log_anchor_source: Optional[str] = None,
    log_anchor_since_iso: Optional[str] = None,
    log_anchor_until_iso: Optional[str] = None,
    log_anchor_action: Optional[str] = None,
) -> Optional[RunRecord]:
    """Update an existing run's record with terminal state +
    payload. No-op if the run_id isn't found (e.g. we crashed
    before persisting the start record)."""
    if status not in RunStatus.TERMINAL:
        raise ValueError(
            f"record_run_complete requires a terminal status, got "
            f"{status!r}",
        )
    with _LOCK:
        records = _read_all_records()
        target_idx = -1
        for i, r in enumerate(records):
            if r.run_id == run_id:
                target_idx = i
                break
        if target_idx < 0:
            return None
        record = records[target_idx]
        now = time.time()
        record.status = status
        record.completed_at = now
        record.elapsed = round(now - record.started_at, 4)
        record.error = error
        record.stdout_tail = stdout_tail
        record.attempts = attempts
        # Update the rolling Welford stats for this job_name and
        # stamp the resulting z-score on the record. Anomaly score
        # reflects how this run compares to the prior baseline (the
        # stats add happens inside ``add_run``, but z is computed
        # *before* the fold so the very-recent value isn't diluted
        # by the mean it just shifted).
        try:
            score = runtime_stats.add_run(
                record.job_name, record.elapsed or 0.0,
            )
            record.anomaly_score = score
        except Exception as exc:  # noqa: BLE001 - defensive
            log_swallowed(exc)
        # Build the log anchor if the caller passed source + since.
        if log_anchor_source and log_anchor_since_iso:
            from media_stack.domain.jobs.run_record import LogAnchor
            record.log_anchor = LogAnchor(
                source=log_anchor_source,
                since_iso=log_anchor_since_iso,
                until_iso=log_anchor_until_iso,
                action=log_anchor_action,
            )
        # Append the parent's child_run_ids list if the parent is
        # tracked in this same file.
        if record.parent_run_id:
            for r in records:
                if (
                    r.run_id == record.parent_run_id
                    and run_id not in r.child_run_ids
                ):
                    r.child_run_ids.append(run_id)
                    break
        _write_all_records(records)
    # Publish outside the lock — handler dispatch can be slow and
    # must never serialise unrelated run-record writes.
    try:
        get_default_bus().publish(
            JobCompleted(
                run_id=record.run_id,
                job_name=record.job_name,
                status=record.status,
                elapsed=record.elapsed or 0.0,
                error=record.error or "",
            ),
        )
    except Exception as exc:  # noqa: BLE001 - defensive
        log_swallowed(exc)
    return record


# ---------------------------------------------------------------------------
# Public API — read paths
# ---------------------------------------------------------------------------


def get_runs(
    *,
    job_name: Optional[str] = None,
    since_ts: Optional[float] = None,
    parent_run_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    limit: int = 100,
    newest_first: bool = True,
) -> list[RunRecord]:
    """Filtered list. All filters AND together; no filter passes
    everything in the buffer."""
    with _LOCK:
        records = _read_all_records()
    out: list[RunRecord] = []
    for r in records:
        if job_name is not None and r.job_name != job_name:
            continue
        if since_ts is not None and r.started_at < since_ts:
            continue
        if parent_run_id is not None and r.parent_run_id != parent_run_id:
            continue
        if batch_id is not None and r.batch_id != batch_id:
            continue
        out.append(r)
    if newest_first:
        out.reverse()
    return out[: max(1, limit)]


def get_run(run_id: str) -> Optional[RunRecord]:
    """Single record by ID, or None."""
    with _LOCK:
        for r in reversed(_read_all_records()):
            if r.run_id == run_id:
                return r
    return None


def get_latest_run(job_name: str) -> Optional[RunRecord]:
    """Most-recent run for a given job name, or None."""
    with _LOCK:
        for r in reversed(_read_all_records()):
            if r.job_name == job_name:
                return r
    return None


def get_children(parent_run_id: str) -> list[RunRecord]:
    """Every run whose ``parent_run_id`` points at the given run.
    Sorted by ``started_at`` ascending (presentation order)."""
    with _LOCK:
        out = [
            r
            for r in _read_all_records()
            if r.parent_run_id == parent_run_id
        ]
    out.sort(key=lambda r: r.started_at)
    return out


# Default staleness threshold: 5 minutes. Anything actually running
# longer than this on a healthy controller is either a wedged handler
# or a zombie from a SIGTERM mid-run. The repair tool's CLI default
# is 10 min for safety; the auto-heal cycle is more aggressive
# because it runs every 60s and self-heals continuously.
_STALE_RUNNING_THRESHOLD_SECONDS = 5 * 60


def count_stale_running(
    threshold_seconds: int = _STALE_RUNNING_THRESHOLD_SECONDS,
) -> int:
    """Number of run records still at status=running with started_at
    older than ``threshold_seconds`` ago. Used as the probe for the
    ``run-history-no-stale-running`` promise.

    Zero is the steady-state expectation. Any positive count means
    either (a) a handler is genuinely taking >5min (rare; a job that
    long should be non_blocking), OR (b) zombies from a controller
    process that died mid-run before the v1.0.293 try/finally fix
    landed. The auto-heal cycle's ensurer reads this number and
    runs ``repair_run_history.run_repair`` to close them."""
    import time

    cutoff = time.time() - threshold_seconds
    with _LOCK:
        records = list(_read_all_records())
    return sum(
        1
        for r in records
        if r.status == RunStatus.RUNNING and r.started_at and r.started_at <= cutoff
    )


def iter_records() -> Iterable[RunRecord]:
    """Generator interface for tests + admin tooling. Returns
    records in oldest → newest order."""
    with _LOCK:
        return list(_read_all_records())


def get_running_tree() -> list[dict]:
    """Tree of in-flight roots with their full descendant tree.

    Used by ``GET /api/jobs/running`` to power the onboarding
    banner's "N done of M steps" counter and the Jobs page
    "Currently running" card (design doc §3 lines 168-174 —
    bootstrap with sub-step glyphs and per-step elapsed).

    Top-level roots are gated to ``status=running`` so the card
    auto-empties as the parent settles (this preserves the
    "currently running" semantic — when bootstrap finishes, it
    stops surfacing here). But descendants beneath a running root
    include settled children too, with their terminal status
    intact. Without that, the onboarding banner's counter is
    forever stuck at "0 done of N running" because completed
    sub-jobs vanish from the tree the instant they finish, taking
    the "done" tally with them instead of contributing to it.

    Each node is a dict (not a RunRecord) so the API serializer
    doesn't have to know about LogAnchor + dataclass nesting; the
    UI receives a flat structure ready to render. Children are
    inlined under each parent in ``started_at`` order; orphan
    children (parent already settled) are surfaced as top-level
    nodes so the operator still sees the work in flight.
    """
    with _LOCK:
        records = list(_read_all_records())
    running = [r for r in records if r.status == RunStatus.RUNNING]
    by_id = {r.run_id: r for r in running}

    # Index every record (running + settled) by parent so the
    # descendant walk is O(1) per node and naturally bounded to the
    # subtree under whichever root we start from.
    by_parent: dict[str, list[RunRecord]] = {}
    for r in records:
        if r.parent_run_id:
            by_parent.setdefault(r.parent_run_id, []).append(r)

    now = time.time()

    def _elapsed(record: RunRecord) -> float:
        if record.status == RunStatus.RUNNING:
            return round(now - record.started_at, 2)
        # Settled: prefer the stored elapsed so the value stops
        # advancing once the job finished. Fall back to
        # completed_at - started_at, then to 0 for malformed rows.
        if record.elapsed is not None:
            return round(record.elapsed, 2)
        if record.completed_at is not None:
            return round(record.completed_at - record.started_at, 2)
        return 0.0

    def _node(record: RunRecord) -> dict:
        child_records = sorted(
            by_parent.get(record.run_id, []),
            key=lambda c: c.started_at,
        )
        return {
            "run_id": record.run_id,
            "job_name": record.job_name,
            "status": record.status,
            "started_at": record.started_at,
            "elapsed_seconds": _elapsed(record),
            "triggered_by": record.triggered_by,
            "actor": record.actor or "",
            "parent_run_id": record.parent_run_id or "",
            "batch_id": record.batch_id or "",
            "children": [_node(c) for c in child_records],
        }

    # Top-level nodes: records whose parent isn't itself running
    # (either no parent, or parent already settled). Sort by
    # started_at ascending so the bootstrap row appears above its
    # spawned children visually.
    tops = sorted(
        (
            r
            for r in running
            if not r.parent_run_id or r.parent_run_id not in by_id
        ),
        key=lambda r: r.started_at,
    )
    return [_node(r) for r in tops]
