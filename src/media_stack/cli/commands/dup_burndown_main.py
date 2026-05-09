"""Daily duplicate-code burn-down — measure, report, optionally tighten.

Runs two detectors against ``src/media_stack/`` and surfaces the
delta vs the AST baseline:

* AST function-fingerprint scan (in-process, ~6s, function-bodies only).
* PMD CPD (out-of-process, ~30s, token-based blocks of any kind) when
  the binary is on PATH or at ``/home/<user>/Downloads/pmd/pmd-bin-*``.

Subcommands:

* ``report``  — print both counts + show the top N dup clusters.
                Default mode. Safe to run any time.
* ``tighten`` — if the AST count dropped below baseline since the
                last update, lower the baseline file in-place. Used
                by the daily cron to ratchet the floor down without
                a human in the loop.
* ``check``   — exit 0 if AST count == baseline, exit 1 if it
                regressed up. CI gate complement to the pytest
                ratchet.

Run via:

    python3 -m media_stack.cli.commands.dup_burndown_main report
    python3 -m media_stack.cli.commands.dup_burndown_main tighten

A daily cron is the intended invocation — the CLI is idempotent and
fast enough to run every morning. See
``contracts/services/dup_burndown.yaml`` for the schedule wiring.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


class DupBurndownDetector:
    """Detects duplicate-code clusters and tracks the baseline file."""

    def load_ratchet_module(self):
        spec = importlib.util.spec_from_file_location(
            "_dup_ratchet",
            Path(__file__).resolve().parents[4]
            / "tests" / "unit" / "ratchets"
            / "test_no_duplicate_code_ratchet.py",
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Cannot locate dup-code ratchet for shared scanner.")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def repo_root(self) -> Path:
        return Path(__file__).resolve().parents[4]

    def baseline_file(self) -> Path:
        return self.repo_root() / ".ratchets" / "duplicate-code-baseline.txt"

    def ast_dup_count(self) -> tuple[int, dict[str, list[str]]]:
        ratchet = self.load_ratchet_module()
        groups = ratchet._all_duplicate_groups()
        return len(groups), groups

    def find_pmd(self) -> str | None:
        """Locate a usable ``pmd`` binary, in order of preference:
           (1) ``$PMD_HOME/bin/pmd`` if exported,
           (2) ``pmd`` on PATH,
           (3) the canonical ``~/Downloads/pmd/pmd-bin-*`` install.
        Returns the binary path or ``None`` if no usable PMD is found.
        """
        pmd_home = os.environ.get("PMD_HOME", "").strip()
        if pmd_home:
            candidate = Path(pmd_home) / "bin" / "pmd"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        path_match = shutil.which("pmd")
        if path_match:
            return path_match
        home = Path.home() / "Downloads" / "pmd"
        if home.is_dir():
            for child in sorted(home.glob("pmd-bin-*")):
                candidate = child / "bin" / "pmd"
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
        return None

    def run_pmd_cpd(self, min_tokens: int = 100) -> tuple[int, str]:
        """Run PMD CPD on ``src/media_stack/`` and return ``(cluster_count, raw_output)``.
        Returns ``(-1, '')`` when PMD is not installed."""
        pmd = self.find_pmd()
        if not pmd:
            return -1, ""
        src = self.repo_root() / "src" / "media_stack"
        proc = subprocess.run(
            [
                pmd, "cpd",
                "--dir", str(src),
                "--language", "python",
                "--minimum-tokens", str(min_tokens),
                "--format", "text",
                "--skip-duplicate-files",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        output = proc.stdout + proc.stderr
        # Each cluster begins with "Found a NN line (NN tokens) duplication" —
        # count those headers.
        return len(re.findall(r"Found a \d+ line", output)), output

    def read_baseline(self) -> int:
        bf = self.baseline_file()
        if not bf.is_file():
            return -1
        raw = bf.read_text(encoding="utf-8").strip()
        try:
            return int(raw)
        except ValueError:
            return -1

    def write_baseline(self, value: int) -> None:
        bf = self.baseline_file()
        bf.parent.mkdir(parents=True, exist_ok=True)
        bf.write_text(f"{value}\n", encoding="utf-8")


class DupBurndownCommand:
    """CLI front-end for duplicate-code burn-down subcommands."""

    def __init__(self, detector: DupBurndownDetector | None = None) -> None:
        self.detector = detector or DupBurndownDetector()

    def print_top_clusters(self, groups: dict[str, list[str]], top: int = 10) -> None:
        sorted_groups = sorted(
            groups.values(), key=lambda g: -len(g),
        )
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

        ast_count, groups = self.detector.ast_dup_count()
        baseline = self.detector.read_baseline()
        print(f"AST function-body groups: {ast_count}")
        if baseline >= 0:
            delta = baseline - ast_count
            sign = "" if delta == 0 else ("-" if delta > 0 else "+")
            print(f"  vs. baseline ({baseline}): {sign}{abs(delta)}")

        pmd_count, _raw = self.detector.run_pmd_cpd(min_tokens=args.pmd_tokens)
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
        """If AST count is below baseline, lower the baseline. Idempotent
        so the daily cron can run unguarded."""
        ast_count, _ = self.detector.ast_dup_count()
        baseline = self.detector.read_baseline()
        if baseline < 0:
            print(
                f"No baseline found at {self.detector.baseline_file()}; "
                f"seeding to {ast_count}."
            )
            self.detector.write_baseline(ast_count)
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
        self.detector.write_baseline(ast_count)
        return 0

    def cmd_check(self, _args: argparse.Namespace) -> int:
        """CI-gate complement: exit 1 if duplication regressed, 0 otherwise."""
        ast_count, _ = self.detector.ast_dup_count()
        baseline = self.detector.read_baseline()
        if baseline < 0:
            print(
                f"No baseline found at {self.detector.baseline_file()}; "
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
        args = self.build_parser().parse_args(list(argv) if argv is not None else None)
        sub = args.subcommand or "report"
        if sub == "tighten":
            return self.cmd_tighten(args)
        if sub == "check":
            return self.cmd_check(args)
        return self.cmd_report(args)


# -----------------------------------------------------------------
# Module-level singletons + back-compat aliases
# -----------------------------------------------------------------

_DETECTOR = DupBurndownDetector()
_COMMAND = DupBurndownCommand(_DETECTOR)


def _load_ratchet_module():
    return _DETECTOR.load_ratchet_module()


def _repo_root() -> Path:
    return _DETECTOR.repo_root()


def _baseline_file() -> Path:
    return _DETECTOR.baseline_file()


def _ast_dup_count() -> tuple[int, dict[str, list[str]]]:
    return _DETECTOR.ast_dup_count()


def _find_pmd() -> str | None:
    return _DETECTOR.find_pmd()


def _run_pmd_cpd(min_tokens: int = 100) -> tuple[int, str]:
    return _DETECTOR.run_pmd_cpd(min_tokens=min_tokens)


def _read_baseline() -> int:
    return _DETECTOR.read_baseline()


def _write_baseline(value: int) -> None:
    _DETECTOR.write_baseline(value)


def _print_top_clusters(groups: dict[str, list[str]], top: int = 10) -> None:
    _COMMAND.print_top_clusters(groups, top=top)


def cmd_report(args: argparse.Namespace) -> int:
    return _COMMAND.cmd_report(args)


def cmd_tighten(args: argparse.Namespace) -> int:
    return _COMMAND.cmd_tighten(args)


def cmd_check(args: argparse.Namespace) -> int:
    return _COMMAND.cmd_check(args)


def _build_parser() -> argparse.ArgumentParser:
    return _COMMAND.build_parser()


def main(argv: Iterable[str] | None = None) -> int:
    return _COMMAND.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
