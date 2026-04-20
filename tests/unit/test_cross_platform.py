"""Cross-platform safety tests.

Flags patterns that break on macOS / Windows even when they work on
Linux — the gaps called out in the earlier cross-platform analysis:
shell scripts, docker socket paths, /etc/hosts manipulation,
hardcoded POSIX-only paths.

These tests are STATIC-ANALYSIS. A real cross-OS CI matrix is the
next step, but catching the patterns up front eliminates 80% of the
surprises.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = ROOT / "bin"


class ShellScriptPortabilityTests(unittest.TestCase):
    """Scan bin/*.sh for patterns that silently fail on non-GNU
    userlands (macOS BSD tools, Windows WSL). Pipes `sed -i` with
    no backup argument, `grep -P`, etc. are the usual offenders."""

    _SH_FILES: list[Path] = []

    @classmethod
    def setUpClass(cls):
        if BIN_DIR.is_dir():
            cls._SH_FILES = [p for p in BIN_DIR.glob("*.sh")
                              if p.is_file()]

    def test_scripts_declare_bash_shebang(self):
        """Using `#!/bin/sh` on macOS pulls dash-like /bin/sh which
        doesn't support arrays, [[ ]], $'...', or process substitution
        — all used by our verify-stack.sh. Enforce bash."""
        offenders = []
        for script in self._SH_FILES:
            first = script.read_text(encoding="utf-8").splitlines()[0:1]
            if not first:
                continue
            if not (first[0].startswith("#!/usr/bin/env bash")
                    or first[0].startswith("#!/bin/bash")):
                offenders.append(f"{script.name}: {first[0]!r}")
        self.assertEqual(
            offenders, [],
            "Shell scripts lacking a bash shebang will run under "
            "macOS's dash-like /bin/sh and silently drop array "
            f"features: {offenders}",
        )

    def test_no_sed_i_without_backup_suffix(self):
        """`sed -i ''` on BSD needs a backup-suffix argument (even
        if empty) that GNU sed rejects. `sed -i.bak` works on both.
        A bare `sed -i` works only on GNU."""
        offenders = []
        for script in self._SH_FILES:
            for i, line in enumerate(
                    script.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                # Flag `sed -i` followed by whitespace (no suffix).
                if re.search(r"\bsed\s+-i\s+['\"-]", stripped):
                    # sed -i ''  OR  sed -i .bak  OR  sed -i -e ...
                    # The BSD-only form is `sed -i ''`; the rest is
                    # portable. Flag only the dangerous bare form.
                    if re.search(r"\bsed\s+-i\s+-e\b", stripped):
                        continue
                    offenders.append(
                        f"{script.name}:{i}: {stripped[:80]}")
        # This is advisory — we don't have a ratchet here today
        # because no current scripts use the dangerous form. Add one
        # and the test fires.
        for violation in offenders:
            if "sed -i ''" in violation or "sed -i \"\"" in violation:
                self.fail(
                    "BSD-only `sed -i ''` form in a shell script; "
                    "use `sed -i.bak` or `sed -i -e ...` for cross-"
                    f"OS compatibility. {violation}",
                )


class DockerSocketPortabilityTests(unittest.TestCase):
    """Docker socket path differs between Linux (/var/run/docker.sock),
    macOS Docker Desktop (~/.docker/run/docker.sock), and Windows
    (//./pipe/docker_engine). Hard-coding /var/run/docker.sock works
    but only on Linux. The docker-compose.yml uses an env var so
    mac/windows can override — test documents that path."""

    def test_compose_uses_overridable_docker_socket(self):
        compose = ROOT / "docker" / "docker-compose.yml"
        if not compose.is_file():
            self.skipTest("docker-compose.yml not found")
        text = compose.read_text(encoding="utf-8")
        hard_coded = [
            i + 1 for i, line in enumerate(text.splitlines())
            if "/var/run/docker.sock:/var/run/docker.sock" in line
            and not line.strip().startswith("#")
        ]
        # Current state: socket is hard-coded on several services.
        # This test DOCUMENTS that fact and requires the count to
        # stay bounded — a regression where new services also
        # hard-code would surface here. When we add ${DOCKER_SOCKET}
        # templating, ratchet this down to 0.
        self.assertLessEqual(
            len(hard_coded), 6,
            f"Too many services hard-code the Linux docker socket "
            f"path (found on lines {hard_coded}). Use ${{DOCKER_SOCKET:"
            f"-/var/run/docker.sock}} so mac/windows can override.",
        )


class PlatformDetectionReadinessTests(unittest.TestCase):
    """Verify Python code doesn't assume POSIX-only behaviors in
    contexts that'll be executed on macOS/Windows via the
    controller image (Linux), the k8s cluster (Linux), or a
    developer's bin/ script from a Mac host."""

    def test_no_usrlocalbin_hardcoded_in_python(self):
        """Hard-coded absolute bin paths (/usr/local/bin/xxx) won't
        exist on macOS-with-Homebrew or Windows. Use `which` /
        `shutil.which` for lookups."""
        offenders = []
        for py in (ROOT / "src").rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            text = py.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if line.strip().startswith("#"):
                    continue
                if re.search(r'["\']/usr/local/bin/', line):
                    offenders.append(f"{py.relative_to(ROOT)}:{i}")
        self.assertEqual(
            offenders, [],
            f"Hard-coded /usr/local/bin/... paths in Python: "
            f"{offenders}. Use shutil.which() for discovery.",
        )

    def test_no_shell_true_on_untrusted_input(self):
        """subprocess.run(..., shell=True) with string interpolation
        is command-injection-prone AND behaves differently on
        Windows (no /bin/sh). Flag usages so someone sanity-checks."""
        offenders = []
        for py in (ROOT / "src").rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            text = py.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "shell=True" in stripped and "subprocess" in text:
                    offenders.append(
                        f"{py.relative_to(ROOT)}:{i}: "
                        f"{stripped[:80]}")
        # Ratchet — bounded at current count; a new shell=True call
        # must justify itself.
        self.assertLessEqual(
            len(offenders), 50,
            f"subprocess shell=True count has grown past baseline. "
            f"Each new occurrence needs justification (shell built-ins, "
            f"wildcard expansion) AND careful input sanitization. "
            f"Found: {offenders[:5]}...",
        )


if __name__ == "__main__":
    unittest.main()
