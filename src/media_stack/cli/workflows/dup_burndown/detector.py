"""DupBurndownDetector — Repository for duplicate-code detection + baseline.

ADR-0015 Phase 7g. Pre-Phase-7g this class lived in
``cli/commands/dup_burndown_main.py`` alongside the CLI command
class. It's pure workflow logic — AST function-fingerprint
detection + PMD CPD invocation + baseline file rw — so it
belongs in workflows/.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path


_PMD_CLUSTER_HEADER_RE = re.compile(r"Found a \d+ line")


class DupBurndownDetector:
    """Repository: AST + PMD detection + baseline file rw."""

    def load_ratchet_module(self):
        spec = importlib.util.spec_from_file_location(
            "_dup_ratchet",
            self.repo_root()
            / "tests" / "unit" / "ratchets"
            / "test_no_duplicate_code_ratchet.py",
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Cannot locate dup-code ratchet for shared scanner.")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def repo_root(self) -> Path:
        # parents[5] = repo root (this file at
        # src/media_stack/cli/workflows/dup_burndown/...).
        return Path(__file__).resolve().parents[5]

    def baseline_file(self) -> Path:
        return self.repo_root() / ".ratchets" / "duplicate-code-baseline.txt"

    def ast_dup_count(self) -> tuple[int, dict[str, list[str]]]:
        ratchet = self.load_ratchet_module()
        groups = ratchet._all_duplicate_groups()
        return len(groups), groups

    def find_pmd(self) -> str | None:
        """Locate a usable ``pmd`` binary, in order of preference:

        1. ``$PMD_HOME/bin/pmd`` if exported,
        2. ``pmd`` on PATH,
        3. the canonical ``~/Downloads/pmd/pmd-bin-*`` install.

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

        Returns ``(-1, '')`` when PMD is not installed.
        """
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
        return len(_PMD_CLUSTER_HEADER_RE.findall(output)), output

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


__all__ = ["DupBurndownDetector"]
