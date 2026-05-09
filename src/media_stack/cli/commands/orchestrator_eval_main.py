#!/usr/bin/env python3
"""Operator CLI for ``PromiseOrchestrator.tick``.

Run one orchestration tick and print a per-promise table. Useful for:

  * Operators eyeballing what the orchestrator sees right now without
    waiting for the next 60s auto-heal tick
  * Reproducing a probe failure interactively when triaging

Usage:

    bin/ops/orchestrator-eval                       # default: dry_run, compose
    bin/ops/orchestrator-eval --platform k8s        # eval against k8s registry
    bin/ops/orchestrator-eval --apply               # actually run ensurers
    bin/ops/orchestrator-eval --json                # machine-readable output

Output (table mode):

    PROMISE                       STATUS          ELAPSED  DETAIL
    jellyfin-running              ok                 12ms  responsive at http://...
    jellyfin-api-key-discoverable ok                  4ms  api key discoverable
    sonarr-jellyfin-notifier      failed_transient   831ms HTTP 401 from /api/v3/notification

Exit code: 0 when no promises are in failed/dep_failed/unknown
states; 1 otherwise. Suitable for ``&& echo OK || echo FAIL``.

Implementation note: the heavy lifting lives on
:class:`OrchestratorEvalCommand` so the CLI's argument parsing,
output rendering, and orchestrator wiring are all instance methods
testable in isolation. The module-level :data:`main` is a thin entry
point bound to the singleton's ``main`` method, which constructs a
fresh command against the real ``sys.stdout`` / ``sys.stderr``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Sequence, TextIO

from media_stack.application.services.orchestrator import (
    PromiseOrchestrator,
    satisfy_promises,
)
from media_stack.domain.services.promises import TickSummary


_DEFAULT_WORKERS = 8


class OrchestratorEvalCommand:
    """One-tick orchestrator CLI.

    Constructed with an output stream + a tick callable; the default
    callable invokes :func:`satisfy_promises` (the module-level shim
    around :class:`PromiseOrchestrator`). Tests inject a fake
    callable + a ``StringIO`` stream so behavior is exercised without
    touching the real orchestrator or stdout.
    """

    _PROG = "bin/ops/orchestrator-eval.sh"
    _DESCRIPTION = "Run one orchestrator tick and print results."

    def __init__(
        self,
        *,
        out: TextIO | None = None,
        err: TextIO | None = None,
        tick_callable: Any = None,
        configure_logging: bool = True,
    ) -> None:
        self._out: TextIO = out if out is not None else sys.stdout
        self._err: TextIO = err if err is not None else sys.stderr
        self._tick_callable = (
            tick_callable if tick_callable is not None else satisfy_promises
        )
        self._configure_logging = configure_logging

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self, argv: Sequence[str] | None = None) -> int:
        args = self._parse_args(argv)
        if self._configure_logging:
            self._setup_logging(verbose=args.verbose)

        summary: TickSummary = self._tick_callable(
            platform=args.platform,
            dry_run=not args.apply,
            workers=args.workers,
        )

        if args.json_out:
            self._print_json(summary)
        else:
            self._print_table(summary)

        return 0 if not summary.has_failures else 1

    def main(self, argv: Sequence[str] | None = None) -> int:
        """Module-level entry point. Constructs a fresh
        :class:`OrchestratorEvalCommand` against the real
        stdout/stderr and delegates so each invocation gets a clean
        I/O surface (the singleton this method is bound to is configured
        for argv-driven CLI use)."""
        return OrchestratorEvalCommand().run(argv)

    # ------------------------------------------------------------------
    # Argparse + logging setup
    # ------------------------------------------------------------------

    def _parse_args(self, argv: Sequence[str] | None) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            prog=self._PROG,
            description=self._DESCRIPTION,
        )
        parser.add_argument(
            "--platform", default="compose", choices=("compose", "k8s"),
            help="Filter promises by platform (default: compose)",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually run ensurers when probes fail. Default is dry-run.",
        )
        parser.add_argument(
            "--json", dest="json_out", action="store_true",
            help="Print one JSON object per promise + a summary line. "
                 "Default is a human-readable table.",
        )
        parser.add_argument(
            "--workers", type=int, default=_DEFAULT_WORKERS,
            help=f"ThreadPoolExecutor size for parallel probes "
                 f"(default: {_DEFAULT_WORKERS})",
        )
        parser.add_argument(
            "--verbose", "-v", action="store_true",
            help="Set log level to DEBUG (default: INFO)",
        )
        return parser.parse_args(argv)

    def _setup_logging(self, *, verbose: bool) -> None:
        logging.basicConfig(
            level=logging.DEBUG if verbose else logging.INFO,
            format="%(levelname)s %(name)s: %(message)s",
            stream=self._err,
        )

    # ------------------------------------------------------------------
    # Output rendering
    # ------------------------------------------------------------------

    def _print_json(self, summary: TickSummary) -> None:
        for attempt in summary.attempts:
            self._out.write(json.dumps(attempt.to_dict()) + "\n")
        self._out.write(
            json.dumps({"summary": self._summary_dict(summary)}) + "\n",
        )

    def _print_table(self, summary: TickSummary) -> None:
        name_width = max(
            (len(a.promise_id) for a in summary.attempts),
            default=20,
        )
        name_width = min(max(name_width, 20), 50)
        status_width = 18
        elapsed_width = 8
        header = (
            f"{'PROMISE':<{name_width}}  "
            f"{'STATUS':<{status_width}}  "
            f"{'ELAPSED':>{elapsed_width}}  "
            "DETAIL"
        )
        self._out.write(header + "\n")
        self._out.write(
            "-" * (name_width + status_width + elapsed_width + 30) + "\n",
        )
        for a in sorted(
            summary.attempts,
            key=lambda x: (x.status != "ok", x.promise_id),
        ):
            elapsed_str = f"{int(a.elapsed_seconds * 1000)}ms"
            detail = (a.detail or "")[:80]
            row = (
                f"{a.promise_id:<{name_width}}  "
                f"{a.status:<{status_width}}  "
                f"{elapsed_str:>{elapsed_width}}  "
                f"{detail}"
            )
            self._out.write(row + "\n")
        self._out.write("\n")
        self._out.write(
            f"summary: {summary.summary_line()} "
            f"({summary.elapsed_seconds:.2f}s wall)\n",
        )

    def _summary_dict(self, summary: TickSummary) -> dict[str, Any]:
        return {
            "total": summary.total,
            "ok": summary.ok,
            "failed_transient": summary.failed_transient,
            "failed_permanent": summary.failed_permanent,
            "dep_failed": summary.dep_failed,
            "skipped_cooldown": summary.skipped_cooldown,
            "skipped_platform": summary.skipped_platform,
            "unknown": summary.unknown,
            "elapsed_seconds": summary.elapsed_seconds,
        }


_INSTANCE = OrchestratorEvalCommand()
main = _INSTANCE.main


# Re-export for callers that want to programmatically configure a
# different orchestrator (test harnesses, CI smoke checks).
__all__ = [
    "OrchestratorEvalCommand",
    "PromiseOrchestrator",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
