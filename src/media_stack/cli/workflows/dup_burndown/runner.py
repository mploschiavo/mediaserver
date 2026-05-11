"""DupBurndownRunner — workflow runner for the dup-burndown subcommands.

ADR-0015 Phase 7g. Pre-Phase-7g the runner methods (``cmd_report``,
``cmd_tighten``, ``cmd_check``, ``print_top_clusters``) lived on
``DupBurndownCommand`` in commands/. Phase 7g moves them onto this
workflows-tier class; the commands shim shrinks to argparse + main.
"""

from __future__ import annotations

import argparse
import sys

from media_stack.cli.workflows.dup_burndown.detector import DupBurndownDetector


_DEFAULT_TOP_CLUSTERS = 10
_DEFAULT_PMD_TOKENS = 100


class DupBurndownRunner:
    """Workflow runner: 3 subcommand handlers (report / tighten / check)."""

    def __init__(self, detector: DupBurndownDetector | None = None) -> None:
        self._detector = detector or DupBurndownDetector()

    @property
    def detector(self) -> DupBurndownDetector:
        return self._detector

    def print_top_clusters(
        self, groups: dict[str, list[str]], top: int = _DEFAULT_TOP_CLUSTERS,
    ) -> None:
        sorted_groups = sorted(groups.values(), key=lambda g: -len(g))
        if not sorted_groups:
            print("  (no duplicate clusters)")
            return
        for i, locations in enumerate(sorted_groups[:top], start=1):
            print(f"  {i}. {len(locations)} copies:")
            for loc in locations:
                print(f"       {loc}")

    def cmd_report(self, args: argparse.Namespace) -> int:
        print("Duplicate-code report")
        print("=" * 60)

        ast_count, groups = self._detector.ast_dup_count()
        baseline = self._detector.read_baseline()
        print(f"AST function-body groups: {ast_count}")
        if baseline >= 0:
            delta = baseline - ast_count
            sign = "" if delta == 0 else ("-" if delta > 0 else "+")
            print(f"  vs. baseline ({baseline}): {sign}{abs(delta)}")

        pmd_count, _raw = self._detector.run_pmd_cpd(min_tokens=args.pmd_tokens)
        if pmd_count < 0:
            print(
                "PMD CPD: not installed (set PMD_HOME or install at "
                "~/Downloads/pmd/pmd-bin-*).",
            )
        else:
            print(f"PMD CPD blocks (≥{args.pmd_tokens} tokens): {pmd_count}")

        print()
        print(f"Top {args.top} largest AST clusters:")
        self.print_top_clusters(groups, top=args.top)
        return 0

    def cmd_tighten(self, args: argparse.Namespace) -> int:
        """If AST count is below baseline, lower the baseline.

        Idempotent so the daily cron can run unguarded.
        """
        ast_count, _ = self._detector.ast_dup_count()
        baseline = self._detector.read_baseline()
        if baseline < 0:
            print(
                f"No baseline found at {self._detector.baseline_file()}; "
                f"seeding to {ast_count}."
            )
            self._detector.write_baseline(ast_count)
            return 0
        if ast_count >= baseline:
            print(
                f"No tightening needed — current ({ast_count}) >= "
                f"baseline ({baseline}).",
            )
            return 0
        print(
            f"Tightening baseline {baseline} → {ast_count} "
            f"({baseline - ast_count} cluster(s) eliminated).",
        )
        self._detector.write_baseline(ast_count)
        return 0

    def cmd_check(self, _args: argparse.Namespace) -> int:
        """CI-gate complement: exit 1 if duplication regressed, 0 otherwise."""
        ast_count, _ = self._detector.ast_dup_count()
        baseline = self._detector.read_baseline()
        if baseline < 0:
            print(
                f"No baseline found at {self._detector.baseline_file()}; "
                f"treating as pass (seed will run on first 'tighten').",
            )
            return 0
        if ast_count > baseline:
            print(
                f"REGRESSION: duplicate-code count grew from {baseline} to "
                f"{ast_count}.",
                file=sys.stderr,
            )
            return 1
        print(f"OK — {ast_count} clusters (baseline {baseline}).")
        return 0


__all__ = ["DupBurndownRunner"]
