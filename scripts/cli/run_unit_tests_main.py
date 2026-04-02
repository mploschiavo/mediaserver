#!/usr/bin/env python3
"""Run unit tests with per-test resource telemetry."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cli.unit_test_runner_service import UnitTestRunnerConfig, run_discovered_unit_tests


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw)


def _env_float(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return float(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _ensure_test_import_paths(root_dir: Path) -> None:
    root_dir_text = str(root_dir)
    scripts_dir_text = str(root_dir / "scripts")
    for candidate in (root_dir_text, scripts_dir_text):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run unit tests with per-test timing and memory telemetry."
    )
    parser.add_argument(
        "--start-dir",
        default=os.environ.get("UNIT_TEST_START_DIR", "tests/unit"),
        help="Start directory for unittest discovery (default: tests/unit).",
    )
    parser.add_argument(
        "--pattern",
        default=os.environ.get("UNIT_TEST_PATTERN", "test_*.py"),
        help="Glob pattern for unittest discovery (default: test_*.py).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=_env_int("UNIT_TEST_TOP_N", 10),
        help="Number of slowest/highest-memory tests to summarize (default: 10).",
    )
    parser.add_argument(
        "--verbosity",
        type=int,
        default=_env_int("UNIT_TEST_VERBOSITY", 1),
        help="unittest verbosity level (default: 1).",
    )
    parser.add_argument(
        "--failfast",
        action="store_true",
        default=_env_bool("UNIT_TEST_FAILFAST", False),
        help="Stop after first failure/error.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=_env_float("UNIT_TEST_TIMEOUT_SECONDS", None),
        help=(
            "Optional per-test timeout budget in seconds. "
            "When exceeded, the test is marked as timeout."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root_dir = Path(__file__).resolve().parents[2]
    _ensure_test_import_paths(root_dir)
    cfg = UnitTestRunnerConfig(
        root_dir=root_dir,
        start_dir=args.start_dir,
        pattern=args.pattern,
        top_n=max(1, int(args.top_n)),
        verbosity=int(args.verbosity),
        failfast=bool(args.failfast),
        timeout_seconds=(
            None
            if args.timeout_seconds is None or float(args.timeout_seconds) <= 0
            else float(args.timeout_seconds)
        ),
    )
    exit_code, _ = run_discovered_unit_tests(cfg=cfg, stream=sys.stdout)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
