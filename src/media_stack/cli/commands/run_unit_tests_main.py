#!/usr/bin/env python3
"""Run unit tests with per-test resource telemetry."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from media_stack.cli.workflows.unit_test_runner_service import UnitTestRunnerConfig, run_discovered_unit_tests










class RunUnitTestsCommand:
    """Wraps unit test runner CLI entrypoint."""

    def build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Run unit tests with per-test timing and memory telemetry.")
        parser.add_argument("--start-dir", default=os.environ.get("UNIT_TEST_START_DIR", "tests/unit"))
        parser.add_argument("--pattern", default=os.environ.get("UNIT_TEST_PATTERN", "test_*.py"))
        parser.add_argument("--top-n", type=int, default=_env_int("UNIT_TEST_TOP_N", 10))
        parser.add_argument("--verbosity", type=int, default=_env_int("UNIT_TEST_VERBOSITY", 1))
        parser.add_argument("--failfast", action="store_true", default=_env_bool("UNIT_TEST_FAILFAST", False))
        parser.add_argument("--timeout-seconds", type=float, default=_env_float("UNIT_TEST_TIMEOUT_SECONDS", None))
        return parser

    def main(self, argv: list[str] | None = None) -> int:
        parser = self.build_parser()
        args = parser.parse_args(argv)
        root_dir = Path(__file__).resolve().parents[4]
        _ensure_test_import_paths(root_dir)
        cfg = UnitTestRunnerConfig(
            root_dir=root_dir, start_dir=args.start_dir, pattern=args.pattern,
            top_n=max(1, int(args.top_n)), verbosity=int(args.verbosity),
            failfast=bool(args.failfast),
            timeout_seconds=None if args.timeout_seconds is None or float(args.timeout_seconds) <= 0 else float(args.timeout_seconds),
        )
        exit_code, _ = run_discovered_unit_tests(cfg=cfg, stream=sys.stdout)
        return exit_code


    @staticmethod
    def _env_int(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        return int(raw) if raw else default

    @staticmethod
    def _env_float(name: str, default: float | None) -> float | None:
        raw = os.environ.get(name, "").strip()
        return float(raw) if raw else default

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.environ.get(name, "").strip().lower()
        return raw in {"1", "true", "yes", "on"} if raw else default

    @staticmethod
    def _ensure_test_import_paths(root_dir: Path) -> None:
        for candidate in (str(root_dir), str(root_dir / "src")):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)


_instance = RunUnitTestsCommand()
build_parser = _instance.build_parser
main = _instance.main

if __name__ == "__main__":
    raise SystemExit(main())
_env_int = _instance._env_int
_env_float = _instance._env_float
_env_bool = _instance._env_bool
_ensure_test_import_paths = _instance._ensure_test_import_paths
