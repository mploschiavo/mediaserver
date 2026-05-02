"""Repair the controller's ``run-history.jsonl`` file.

This is the canonical home of the repair logic. Two consumers:

  1. ``bin/ops/repair_run_history.py`` — operator CLI wrapper that
     imports ``main()`` and calls it. Lives at ``bin/ops/`` per the
     project's "operator tools live under bin/" convention.
  2. ``application.jobs.close_stale_runs.close_stale_runs`` — the
     auto-heal cycle's promise-style ensurer. Imports
     ``run_repair`` directly.

Single source of truth, importable from both consumers without
importlib spec dancing.

Original module docstring follows.

----

The JobRunner writes one record per run with ``status=running`` at
start and rewrites in place with a terminal status at completion.
If the controller process is killed mid-run (deploy, OOM, SIGTERM)
or a sync handler raises an unhandled exception, the start record
is never finalized — the row stays ``status=running`` forever and
``GET /api/jobs/running`` keeps surfacing it.

This tool finds those zombies and rewrites them with a terminal
status, plus runs a couple of other one-shot data corrections that
keep the on-disk history aligned with reality.

Designed to be re-run any time the same drift recurs. Defaults are
**read-only** — the script reports what it would do and exits 0.
Pass ``--apply`` to mutate the file.

Examples
--------

  # Read-only audit with defaults (10 min staleness threshold).
  python3 bin/ops/repair_run_history.py

  # Apply defaults — close stuck records older than 10 minutes.
  python3 bin/ops/repair_run_history.py --apply

  # Stricter threshold + a different terminal status.
  python3 bin/ops/repair_run_history.py --apply \\
      --older-than-minutes 30 --mark-as cancelled

  # Single scenario only.
  python3 bin/ops/repair_run_history.py --apply \\
      --scenarios fix-stuck-running

  # Custom history file path and JSON output for piping.
  python3 bin/ops/repair_run_history.py --apply \\
      --history-path /srv-config/.controller/run-history.jsonl \\
      --json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence


# Default staleness threshold for the ``fix-stuck-running`` scenario.
# Anything actually running for >10 minutes on this stack is either a
# zombie or a much-bigger problem the operator already knows about;
# either way it's safe to close the record so the UI stops claiming
# 80h runtimes.
DEFAULT_OLDER_THAN_MINUTES = 10

# Run-history.jsonl candidate locations, searched in order. Mirrors
# ``resolve_run_history_path`` in the runtime — kept duplicated so
# this script can run on a host without ``media_stack`` on PYTHONPATH.
DEFAULT_HISTORY_CANDIDATES: tuple[str, ...] = (
    "/srv-config/.controller/run-history.jsonl",
    "/var/lib/media-stack/config/.controller/run-history.jsonl",
    "config/.controller/run-history.jsonl",
)

# Match the ``RunStatus`` Crockford-style constants exactly. We do
# not import from ``media_stack.domain.jobs.run_record`` because the
# script is intentionally runnable on a host without the package
# installed (e.g. via ``docker exec controller python3 -``).
STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"
STATUS_TIMEOUT = "timeout"
STATUS_SKIPPED = "skipped"

TERMINAL_STATUSES: frozenset[str] = frozenset(
    {STATUS_OK, STATUS_ERROR, STATUS_CANCELLED, STATUS_TIMEOUT, STATUS_SKIPPED},
)

ALLOWED_REWRITE_STATUSES: tuple[str, ...] = (
    STATUS_ERROR,
    STATUS_CANCELLED,
    STATUS_TIMEOUT,
)

ERROR_PREFIX = "repair_run_history: closed stale run"

SCENARIO_FIX_STUCK_RUNNING = "fix-stuck-running"
SCENARIO_BACKFILL_ELAPSED = "backfill-elapsed"

ALL_SCENARIOS: tuple[str, ...] = (
    SCENARIO_FIX_STUCK_RUNNING,
    SCENARIO_BACKFILL_ELAPSED,
)
DEFAULT_SCENARIOS: tuple[str, ...] = (SCENARIO_FIX_STUCK_RUNNING,)


@dataclass
class RepairAction:
    """One per-record change the script intends to make. Persisted
    in the JSON output so the operator (or a follow-up script) can
    reconstruct exactly what happened."""

    run_id: str
    job_name: str
    scenario: str
    before: dict[str, Any]
    after: dict[str, Any]


@dataclass
class RepairReport:
    """Aggregated summary returned by ``run_repair`` and serialized
    when ``--json`` is set."""

    history_path: str
    apply: bool
    backup_path: str | None = None
    scenarios: list[str] = field(default_factory=list)
    older_than_seconds: int = DEFAULT_OLDER_THAN_MINUTES * 60
    mark_as: str = STATUS_ERROR
    total_records: int = 0
    actions: list[RepairAction] = field(default_factory=list)
    skipped_recent_running: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "history_path": self.history_path,
            "apply": self.apply,
            "backup_path": self.backup_path,
            "scenarios": list(self.scenarios),
            "older_than_seconds": self.older_than_seconds,
            "mark_as": self.mark_as,
            "total_records": self.total_records,
            "actions_count": len(self.actions),
            "skipped_recent_running": self.skipped_recent_running,
            "actions": [
                {
                    "run_id": a.run_id,
                    "job_name": a.job_name,
                    "scenario": a.scenario,
                    "before": a.before,
                    "after": a.after,
                }
                for a in self.actions
            ],
        }


def resolve_history_path(explicit: str | None) -> Path:
    """Return the explicit path if given, else the first candidate
    that exists. Raises ``FileNotFoundError`` if nothing matches —
    callers convert to a friendly error message."""
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"history path not found: {path}")
        return path
    for cand in DEFAULT_HISTORY_CANDIDATES:
        path = Path(cand).expanduser().resolve()
        if path.is_file():
            return path
    raise FileNotFoundError(
        "no run-history.jsonl found at any default location: "
        + ", ".join(DEFAULT_HISTORY_CANDIDATES)
    )


def read_records(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def write_records_atomic(path: Path, records: Iterable[dict[str, Any]]) -> None:
    """Atomic rewrite — temp file in same dir + rename. The
    controller's reader is single-threaded against the JSONL file so
    a torn read is not a concern, but rename-over keeps the window
    where the file is invalid down to zero bytes."""
    tmp = path.with_suffix(path.suffix + ".repair.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for r in records:
            handle.write(json.dumps(r, ensure_ascii=False))
            handle.write("\n")
    os.replace(tmp, path)


def make_backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak-{stamp}")
    shutil.copy2(path, backup)
    return backup


def is_stale_running(record: dict[str, Any], cutoff: float) -> bool:
    """A record is "stale running" when:
       (a) status is ``running``, AND
       (b) ``started_at`` is older than ``cutoff`` (epoch seconds).
    No completed_at check is needed — if it had a completed_at we
    wouldn't be looking at a status=running row."""
    if record.get("status") != STATUS_RUNNING:
        return False
    try:
        started_at = float(record.get("started_at") or 0.0)
    except (TypeError, ValueError):
        return False
    return started_at > 0 and started_at <= cutoff


def fix_stuck_running(
    records: list[dict[str, Any]],
    *,
    cutoff: float,
    now: float,
    mark_as: str,
    report: RepairReport,
) -> None:
    """Mark each stale ``status=running`` record as terminal. The
    ``elapsed`` field is filled in from the time the row was opened
    so the UI shows a real duration instead of "running forever"."""
    for record in records:
        if record.get("status") != STATUS_RUNNING:
            continue
        if not is_stale_running(record, cutoff):
            report.skipped_recent_running += 1
            continue
        before = {
            "status": record.get("status"),
            "completed_at": record.get("completed_at"),
            "elapsed": record.get("elapsed"),
            "error": record.get("error"),
        }
        try:
            started_at = float(record.get("started_at") or 0.0)
        except (TypeError, ValueError):
            started_at = 0.0
        elapsed = round(now - started_at, 3) if started_at > 0 else None
        record["status"] = mark_as
        record["completed_at"] = now
        if elapsed is not None:
            record["elapsed"] = elapsed
        record["error"] = (
            f"{ERROR_PREFIX}: status=running for >"
            f"{int(report.older_than_seconds)}s, marked {mark_as}"
        )
        after = {
            "status": record.get("status"),
            "completed_at": record.get("completed_at"),
            "elapsed": record.get("elapsed"),
            "error": record.get("error"),
        }
        report.actions.append(
            RepairAction(
                run_id=str(record.get("run_id", "")),
                job_name=str(record.get("job_name", "")),
                scenario=SCENARIO_FIX_STUCK_RUNNING,
                before=before,
                after=after,
            )
        )


def backfill_elapsed(
    records: list[dict[str, Any]],
    *,
    report: RepairReport,
) -> None:
    """For terminal records that have both ``started_at`` and
    ``completed_at`` but a missing ``elapsed`` field, compute it.
    Touches no other fields."""
    for record in records:
        if record.get("status") not in TERMINAL_STATUSES:
            continue
        if record.get("elapsed") is not None:
            continue
        try:
            started_at = float(record.get("started_at") or 0.0)
            completed_at = float(record.get("completed_at") or 0.0)
        except (TypeError, ValueError):
            continue
        if started_at <= 0 or completed_at <= 0 or completed_at < started_at:
            continue
        before = {"elapsed": record.get("elapsed")}
        record["elapsed"] = round(completed_at - started_at, 3)
        report.actions.append(
            RepairAction(
                run_id=str(record.get("run_id", "")),
                job_name=str(record.get("job_name", "")),
                scenario=SCENARIO_BACKFILL_ELAPSED,
                before=before,
                after={"elapsed": record["elapsed"]},
            )
        )


def run_repair(
    *,
    history_path: Path,
    apply: bool,
    older_than_seconds: int,
    mark_as: str,
    scenarios: Sequence[str],
    backup: bool,
    now: float | None = None,
) -> RepairReport:
    """Pure entrypoint — separated from CLI so unit tests can drive
    it without subprocess. Returns a ``RepairReport`` either way; the
    caller is responsible for honoring ``apply``."""
    now_ts = now if now is not None else time.time()
    cutoff = now_ts - older_than_seconds
    report = RepairReport(
        history_path=str(history_path),
        apply=apply,
        scenarios=list(scenarios),
        older_than_seconds=older_than_seconds,
        mark_as=mark_as,
    )
    records = read_records(history_path)
    report.total_records = len(records)

    if SCENARIO_FIX_STUCK_RUNNING in scenarios:
        fix_stuck_running(
            records,
            cutoff=cutoff,
            now=now_ts,
            mark_as=mark_as,
            report=report,
        )
    if SCENARIO_BACKFILL_ELAPSED in scenarios:
        backfill_elapsed(records, report=report)

    if not report.actions:
        return report
    if not apply:
        return report
    if backup:
        report.backup_path = str(make_backup(history_path))
    write_records_atomic(history_path, records)
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="repair_run_history",
        description=(
            "Audit and repair the controller's run-history.jsonl. "
            "Default behavior is dry-run; pass --apply to mutate."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--history-path",
        default=None,
        help=(
            "Path to run-history.jsonl. If omitted, the script searches "
            "the default locations (CONFIG_ROOT-relative)."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually rewrite the file. Without this flag the script is read-only.",
    )
    parser.add_argument(
        "--older-than-minutes",
        type=int,
        default=DEFAULT_OLDER_THAN_MINUTES,
        help=(
            f"Staleness threshold for stuck-running records (default: "
            f"{DEFAULT_OLDER_THAN_MINUTES} min). Records younger than "
            "this are left alone — they may genuinely still be running."
        ),
    )
    parser.add_argument(
        "--mark-as",
        choices=ALLOWED_REWRITE_STATUSES,
        default=STATUS_ERROR,
        help=(
            "Terminal status to write on stuck records. ``error`` "
            "(default) keeps anomaly trackers honest; ``cancelled`` "
            "is appropriate when the operator triggered the cleanup; "
            "``timeout`` matches the deadline-exceeded semantic."
        ),
    )
    parser.add_argument(
        "--scenarios",
        default=",".join(DEFAULT_SCENARIOS),
        help=(
            "Comma-separated list of scenarios to run. Available: "
            + ", ".join(ALL_SCENARIOS)
            + f". Default: {','.join(DEFAULT_SCENARIOS)}"
        ),
    )
    parser.add_argument(
        "--no-backup",
        dest="backup",
        action="store_false",
        default=True,
        help="Skip the timestamped .bak-* backup before mutating.",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit a JSON report on stdout instead of a human summary.",
    )
    return parser.parse_args(argv)


def parse_scenarios(raw: str) -> list[str]:
    out: list[str] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if piece not in ALL_SCENARIOS:
            raise SystemExit(
                f"unknown scenario: {piece!r}. choices: {ALL_SCENARIOS}"
            )
        if piece in out:
            continue
        out.append(piece)
    return out or list(DEFAULT_SCENARIOS)


def print_human_summary(report: RepairReport) -> None:
    print(f"history-path: {report.history_path}")
    print(f"records:      {report.total_records}")
    print(f"scenarios:    {', '.join(report.scenarios)}")
    print(f"apply:        {report.apply}")
    if report.actions:
        print(f"actions:      {len(report.actions)}")
        per_scenario: dict[str, int] = {}
        for action in report.actions:
            per_scenario[action.scenario] = per_scenario.get(action.scenario, 0) + 1
        for scenario, count in sorted(per_scenario.items()):
            print(f"  {scenario}: {count}")
        print()
        print("changes:")
        for action in report.actions[:20]:
            print(
                f"  [{action.scenario}] {action.job_name}"
                f" run_id={action.run_id} {action.before} -> {action.after}"
            )
        if len(report.actions) > 20:
            print(f"  … {len(report.actions) - 20} more (see --json for full list)")
    else:
        print("actions:      0 (history is clean)")
    if report.skipped_recent_running:
        print(
            f"skipped-recent-running: {report.skipped_recent_running} "
            f"(within {report.older_than_seconds}s)"
        )
    if report.backup_path:
        print(f"backup:       {report.backup_path}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        history_path = resolve_history_path(args.history_path)
    except FileNotFoundError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    scenarios = parse_scenarios(args.scenarios)
    report = run_repair(
        history_path=history_path,
        apply=bool(args.apply),
        older_than_seconds=int(args.older_than_minutes) * 60,
        mark_as=str(args.mark_as),
        scenarios=scenarios,
        backup=bool(args.backup),
    )
    if args.json_output:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print_human_summary(report)
    return 0


# NOTE: no ``if __name__ == "__main__"`` block here — this module is
# imported by the operator CLI at ``bin/ops/repair_run_history.py``,
# which exposes the ``main()`` function above as its entrypoint.
