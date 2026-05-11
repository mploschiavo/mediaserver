"""Entry-point shim for ``dup-burndown``.

ADR-0015 Phase 7g. Pre-Phase-7g this module held the full
``DupBurndownDetector`` + ``DupBurndownCommand`` (281 LoC).
Phase 7g moved the workflow logic into
:mod:`media_stack.cli.workflows.dup_burndown`; what remains here
is argparse + main + module-level aliases for the historical
test-patch surface (8 detector helpers + 4 command helpers +
``main``).

Subcommands:

* ``report``  — print AST + PMD counts and top clusters.
* ``tighten`` — lower the baseline if duplication dropped.
* ``check``   — exit 1 if duplication regressed (CI gate).
"""

from __future__ import annotations

import argparse
from typing import Iterable

from media_stack.cli.workflows.dup_burndown import (
    DupBurndownDetector,
    DupBurndownRunner,
)


class DupBurndownEntryPoint:
    """Per-ADR-0012 entry-point: argparse → runner dispatch."""

    def __init__(self) -> None:
        self._detector = DupBurndownDetector()
        self._runner = DupBurndownRunner(self._detector)

    @property
    def detector(self) -> DupBurndownDetector:
        return self._detector

    @property
    def runner(self) -> DupBurndownRunner:
        return self._runner

    def build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="dup-burndown",
            description="Duplicate-code burn-down measurement + tightening",
        )
        sub = parser.add_subparsers(dest="subcommand")

        rep = sub.add_parser("report", help="Print AST + PMD counts and top clusters")
        rep.add_argument(
            "--top", type=int, default=10,
            help="How many largest clusters to print (default 10)",
        )
        rep.add_argument(
            "--pmd-tokens", type=int, default=100,
            help="PMD CPD --minimum-tokens (default 100)",
        )

        sub.add_parser("tighten", help="Lower baseline if duplication dropped")
        sub.add_parser("check", help="Exit 1 if duplication regressed")

        return parser

    def main(self, argv: Iterable[str] | None = None) -> int:
        args = self.build_parser().parse_args(
            list(argv) if argv is not None else None,
        )
        sub = args.subcommand or "report"
        if sub == "tighten":
            return self._runner.cmd_tighten(args)
        if sub == "check":
            return self._runner.cmd_check(args)
        return self._runner.cmd_report(args)


# Module-level singletons + back-compat aliases for the historical
# test-patch surface (the eight detector helpers + four command
# helpers + ``main`` were all importable by name pre-Phase-7g).
_INSTANCE = DupBurndownEntryPoint()
_DETECTOR = _INSTANCE.detector
_RUNNER = _INSTANCE.runner

_load_ratchet_module = _DETECTOR.load_ratchet_module
_repo_root = _DETECTOR.repo_root
_baseline_file = _DETECTOR.baseline_file
_ast_dup_count = _DETECTOR.ast_dup_count
_find_pmd = _DETECTOR.find_pmd
_run_pmd_cpd = _DETECTOR.run_pmd_cpd
_read_baseline = _DETECTOR.read_baseline
_write_baseline = _DETECTOR.write_baseline
_print_top_clusters = _RUNNER.print_top_clusters
cmd_report = _RUNNER.cmd_report
cmd_tighten = _RUNNER.cmd_tighten
cmd_check = _RUNNER.cmd_check
_build_parser = _INSTANCE.build_parser
main = _INSTANCE.main


__all__ = [
    "DupBurndownEntryPoint",
    "_ast_dup_count",
    "_baseline_file",
    "_build_parser",
    "_find_pmd",
    "_load_ratchet_module",
    "_print_top_clusters",
    "_read_baseline",
    "_repo_root",
    "_run_pmd_cpd",
    "_write_baseline",
    "cmd_check",
    "cmd_report",
    "cmd_tighten",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
