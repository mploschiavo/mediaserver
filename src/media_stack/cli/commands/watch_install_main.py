#!/usr/bin/env python3
"""Entry-point shim for ``bin/watch-install.sh``.

ADR-0015 Phase 7h. Pre-Phase-7h this module held a 231-LoC
``WatchInstallCommand`` (10 methods) doing per-tick install
snapshots + argparse. Phase 7h moved the workflow onto
:class:`WatchInstallRunner` under workflows/; what remains is
argparse + main + back-compat aliases.
"""

from __future__ import annotations

import argparse
import os
import sys

from media_stack.cli.workflows.watch_install_runner import (
    WatchInstallConfig,
    WatchInstallRunner,
)
from media_stack.core.exceptions import ConfigError, MediaStackError


class WatchInstallEntryPoint:
    """Per-ADR-0012 entry-point: argparse → runner.run."""

    def __init__(self) -> None:
        self._runner = WatchInstallRunner()

    @property
    def runner(self) -> WatchInstallRunner:
        return self._runner

    def parse_config(self, argv: list[str] | None = None) -> WatchInstallConfig:
        parser = argparse.ArgumentParser(
            prog="bin/watch-install.sh",
            description=(
                "Live install/bootstrap watcher for media-stack "
                "(pods/deployments/events/bootstrap-job logs)."
            ),
        )
        parser.add_argument(
            "--namespace", default=os.environ.get("NAMESPACE", "media-stack"),
        )
        parser.add_argument(
            "--interval", type=int, default=int(os.environ.get("INTERVAL", "10")),
        )
        parser.add_argument(
            "--event-lines", type=int,
            default=int(os.environ.get("EVENT_LINES", "15")),
        )
        parser.add_argument(
            "--job-log-lines", type=int,
            default=int(os.environ.get("JOB_LOG_LINES", "20")),
        )
        parser.add_argument("--once", action="store_true", default=False)
        args = parser.parse_args(argv)

        if args.interval < 1:
            raise ConfigError("--interval must be >= 1")
        if args.event_lines < 1:
            raise ConfigError("--event-lines must be >= 1")
        if args.job_log_lines < 1:
            raise ConfigError("--job-log-lines must be >= 1")

        return WatchInstallConfig(
            namespace=str(args.namespace or "").strip() or "media-stack",
            interval_seconds=int(args.interval),
            event_lines=int(args.event_lines),
            job_log_lines=int(args.job_log_lines),
            once=bool(args.once),
        )

    def main(self, argv: list[str] | None = None) -> int:
        try:
            return self._runner.run(self.parse_config(argv))
        except KeyboardInterrupt:
            return 130
        except (ConfigError, MediaStackError, OSError, ValueError) as exc:
            print(f"[{self._runner.ts()}] [ERR] {exc}", file=sys.stderr)
            return 1


# Module-level singleton + back-compat aliases.
_INSTANCE = WatchInstallEntryPoint()
parse_config = _INSTANCE.parse_config
run = _INSTANCE.runner.run
main = _INSTANCE.main


__all__ = [
    "WatchInstallConfig",
    "WatchInstallEntryPoint",
    "main",
    "parse_config",
    "run",
]


if __name__ == "__main__":
    raise SystemExit(main())
