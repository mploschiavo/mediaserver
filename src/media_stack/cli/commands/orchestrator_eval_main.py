#!/usr/bin/env python3
"""Operator CLI for ``satisfy_promises``.

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
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Sequence

from media_stack.application.services.orchestrator import satisfy_promises


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bin/ops/orchestrator-eval.sh",
        description="Run one orchestrator tick and print results.",
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
        "--workers", type=int, default=8,
        help="ThreadPoolExecutor size for parallel probes (default: 8)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Set log level to DEBUG (default: INFO)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    summary = satisfy_promises(
        platform=args.platform,
        dry_run=not args.apply,
        workers=args.workers,
    )

    if args.json_out:
        for attempt in summary.attempts:
            print(json.dumps(attempt.to_dict()))
        print(json.dumps({
            "summary": {
                "total": summary.total,
                "ok": summary.ok,
                "failed_transient": summary.failed_transient,
                "failed_permanent": summary.failed_permanent,
                "dep_failed": summary.dep_failed,
                "skipped_cooldown": summary.skipped_cooldown,
                "skipped_platform": summary.skipped_platform,
                "unknown": summary.unknown,
                "elapsed_seconds": summary.elapsed_seconds,
            },
        }))
    else:
        _print_table(summary)

    return 0 if not summary.has_failures else 1


def _print_table(summary) -> None:  # noqa: ANN001
    name_width = max(
        (len(a.promise_id) for a in summary.attempts),
        default=20,
    )
    name_width = min(max(name_width, 20), 50)
    status_width = 18
    elapsed_width = 8
    print(
        f"{'PROMISE':<{name_width}}  "
        f"{'STATUS':<{status_width}}  "
        f"{'ELAPSED':>{elapsed_width}}  "
        "DETAIL",
    )
    print("-" * (name_width + status_width + elapsed_width + 30))
    for a in sorted(summary.attempts, key=lambda x: (x.status != "ok", x.promise_id)):
        elapsed_str = f"{int(a.elapsed_seconds * 1000)}ms"
        detail = (a.detail or "")[:80]
        print(
            f"{a.promise_id:<{name_width}}  "
            f"{a.status:<{status_width}}  "
            f"{elapsed_str:>{elapsed_width}}  "
            f"{detail}",
        )
    print()
    print(
        f"summary: {summary.summary_line()} "
        f"({summary.elapsed_seconds:.2f}s wall)",
    )


if __name__ == "__main__":
    raise SystemExit(main())
