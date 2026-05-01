"""Per-run telemetry records — Jobs Phase 2 / parent-id work.

The legacy ``job-history.json`` (in ``framework.py``) stores BATCH
results: one entry per orchestrator pass with a flat ``jobs: {name:
{status, elapsed}}`` map. That's enough for "how long did the last
bootstrap take?" but doesn't answer:

  * "Tell me about the last run of ``envoy-config``."
  * "What were its child runs?"
  * "What did it print to stdout while it ran?"
  * "Was this run triggered by cron, by the operator, or by a parent?"

``RunRecord`` is the per-job-run entity that fills those gaps.
Append-only persistence in ``run-history.jsonl`` with a 50,000-line
cap (matching ``LOG_LINES_HARD_CAP`` so operators don't need to ssh
to read history). At ~250 bytes per record, 50,000 records is ~12 MB
on disk — small enough to scan linearly without indexing.

Design notes
------------
* **IDs are ULIDs**, lexically sortable by time. Avoids the
  random-UUID problem of "I have to query timestamps separately to
  order them." A run's ID encodes its start time at second
  granularity.
* **``parent_run_id``** is optional. Top-level batch runs have
  ``parent_run_id=None``; jobs spawned by a batch carry that
  batch's ID; sub-jobs carry the parent job's ID. Tree of runs.
* **``batch_id``** is denormalized — every record carries the
  top-level batch ID it belongs to. Lets the UI ask "show me
  everything from batch X" with a single linear scan.
* **``stdout_tail``** caps at 16 KB. Full output goes to the
  controller's main log (which the Logs page can pull); the tail
  is for the at-a-glance "what crashed?" view.
* **``log_anchor``** carries the params to deep-link into the
  Logs page, so the Jobs UI can render a "View logs for this run"
  button without itself owning log knowledge.
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


# Hard cap matches LOG_LINES_HARD_CAP — operators want to read run
# history at the same scale they read logs, without ssh.
RUN_HISTORY_HARD_CAP = 50000

# Tail capture cap per run. 16 KB is enough to see the last few
# screens of stdout for a stuck/crashed job; the full output lives
# in the controller's main log.
RUN_STDOUT_TAIL_CAP = 16 * 1024


# Crockford's Base32 alphabet — used by ULIDs. Excludes I, L, O, U
# to avoid visual ambiguity. The result is a 26-char string that
# sorts lexicographically by time.
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def make_run_id(now_ms: int | None = None) -> str:
    """Produce a 26-char ULID. Sortable by creation time, globally
    unique with overwhelming probability (80 bits of randomness)."""
    ts = int(now_ms if now_ms is not None else time.time() * 1000)
    # 48-bit timestamp, 80-bit randomness.
    rand_bits = secrets.token_bytes(10)
    rand_int = int.from_bytes(rand_bits, byteorder="big")
    # Encode timestamp (48 bits → 10 chars) + random (80 bits → 16
    # chars).
    full = (ts << 80) | rand_int
    out = []
    for _ in range(26):
        out.append(_ULID_ALPHABET[full & 0x1F])
        full >>= 5
    return "".join(reversed(out))


# Run lifecycle states. ``running`` records are written when the
# job starts; the same record is rewritten with a terminal state
# when it completes.
class RunStatus:
    RUNNING = "running"
    OK = "ok"
    SKIPPED = "skipped"
    ERROR = "error"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"

    TERMINAL = frozenset({"ok", "skipped", "error", "cancelled", "timeout"})


@dataclass
class LogAnchor:
    """Deep-link parameters into the Logs page for the time window
    a run executed in. The Jobs UI's "View logs" button passes
    these through to the existing ``/api/logs/<source>`` filter
    set."""
    source: str
    since_iso: str
    until_iso: Optional[str] = None
    action: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class RunRecord:
    """One execution of one job. Append-only — the same ``run_id``
    is written twice during normal operation: once at start
    (``status=running``), once at completion (terminal status). The
    persistence layer overwrites in place by ``run_id`` so the file
    grows by one logical record per run, not two."""

    run_id: str
    job_name: str
    status: str  # one of RunStatus values
    started_at: float
    parent_run_id: Optional[str] = None
    batch_id: Optional[str] = None
    completed_at: Optional[float] = None
    elapsed: Optional[float] = None
    triggered_by: str = "unknown"
    actor: Optional[str] = None
    attempts: int = 1
    error: Optional[str] = None
    stdout_tail: Optional[str] = None
    log_anchor: Optional[LogAnchor] = None
    child_run_ids: list[str] = field(default_factory=list)
    # Z-score relative to the rolling mean of this job's recent
    # durations (Welford-tracked in ``application/jobs/runtime_
    # stats.py``). ``None`` until enough history exists; the UI
    # tints the row red when ``> 2`` and amber when ``> 1``.
    anomaly_score: Optional[float] = None
    # ADR-0003 Phase 4b: when the orchestrator emits a record (probe
    # call, ensurer call, or full satisfy_promises tick), this carries
    # the promise id so operators can query "every evaluation of
    # promise X" via the existing run-history API. Optional + None by
    # default — legacy job records don't have a promise id, and that
    # stays additive: missing field on disk maps to None on read.
    promise_id: Optional[str] = None

    def __post_init__(self) -> None:
        # Cap the stdout tail defensively — callers should already
        # truncate before constructing, but the file has a 50K-line
        # cap and one runaway record shouldn't blow the budget.
        if self.stdout_tail and len(self.stdout_tail) > RUN_STDOUT_TAIL_CAP:
            self.stdout_tail = self.stdout_tail[-RUN_STDOUT_TAIL_CAP:]
        # Cap error similar to framework's existing pattern.
        if self.error and len(self.error) > 500:
            self.error = self.error[:500]

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "run_id": self.run_id,
            "job_name": self.job_name,
            "status": self.status,
            "started_at": self.started_at,
            "triggered_by": self.triggered_by,
            "attempts": self.attempts,
            "child_run_ids": list(self.child_run_ids),
        }
        if self.parent_run_id is not None:
            out["parent_run_id"] = self.parent_run_id
        if self.batch_id is not None:
            out["batch_id"] = self.batch_id
        if self.completed_at is not None:
            out["completed_at"] = self.completed_at
        if self.elapsed is not None:
            out["elapsed"] = self.elapsed
        if self.actor is not None:
            out["actor"] = self.actor
        if self.error is not None:
            out["error"] = self.error
        if self.stdout_tail is not None:
            out["stdout_tail"] = self.stdout_tail
        if self.log_anchor is not None:
            out["log_anchor"] = self.log_anchor.to_dict()
        if self.anomaly_score is not None:
            out["anomaly_score"] = self.anomaly_score
        if self.promise_id is not None:
            out["promise_id"] = self.promise_id
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunRecord":
        anchor_raw = data.get("log_anchor")
        anchor = (
            LogAnchor(
                source=str(anchor_raw.get("source", "")),
                since_iso=str(anchor_raw.get("since_iso", "")),
                until_iso=anchor_raw.get("until_iso"),
                action=anchor_raw.get("action"),
            )
            if isinstance(anchor_raw, dict)
            else None
        )
        return cls(
            run_id=str(data.get("run_id", "")),
            job_name=str(data.get("job_name", "")),
            status=str(data.get("status", RunStatus.RUNNING)),
            started_at=float(data.get("started_at", 0.0)),
            parent_run_id=data.get("parent_run_id"),
            batch_id=data.get("batch_id"),
            completed_at=(
                float(data["completed_at"])
                if data.get("completed_at") is not None
                else None
            ),
            elapsed=(
                float(data["elapsed"])
                if data.get("elapsed") is not None
                else None
            ),
            triggered_by=str(data.get("triggered_by", "unknown")),
            actor=data.get("actor"),
            attempts=int(data.get("attempts", 1)),
            error=data.get("error"),
            stdout_tail=data.get("stdout_tail"),
            log_anchor=anchor,
            child_run_ids=list(data.get("child_run_ids") or []),
            anomaly_score=(
                float(data["anomaly_score"])
                if data.get("anomaly_score") is not None
                else None
            ),
            promise_id=data.get("promise_id"),
        )


def truncate_stdout_tail(text: str) -> str:
    """Convenience for callers that capture stdout before
    constructing a record. Trims to ``RUN_STDOUT_TAIL_CAP`` from
    the END of the buffer."""
    if not text:
        return ""
    if len(text) <= RUN_STDOUT_TAIL_CAP:
        return text
    return text[-RUN_STDOUT_TAIL_CAP:]


def resolve_run_history_path() -> "os.PathLike[str]":
    """``config/.controller/run-history.jsonl`` — same dir as the
    legacy job-history.json."""
    from pathlib import Path
    config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
    return Path(config_root) / ".controller" / "run-history.jsonl"
