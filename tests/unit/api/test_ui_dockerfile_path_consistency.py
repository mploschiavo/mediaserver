"""Ratchet: paths referenced by ``docker/ui.Dockerfile`` must exist.

The UI image's multi-stage build COPY-s several paths from the build
context (the repo root). A typo in any of those paths only surfaces at
``docker build`` time — slow feedback. This test scans the Dockerfile,
extracts each ``COPY <src> <dst>`` (excluding ``COPY --from=...``,
which references a previous build stage's filesystem rather than the
build context), and asserts the source path resolves to a real file or
directory under the repo root.

It also asserts the UI source layout the build stage assumes is
present (``ui/package.json``, ``ui/vite.config.ts``). The lockfile
``ui/pnpm-lock.yaml`` is treated as a soft requirement: the test warns
via the captured output but does not fail if absent — first-time
installs need a window in which the lockfile hasn't been generated yet.
"""

from __future__ import annotations

import re
import shlex
import unittest
import warnings
from pathlib import Path

import pytest

ROOT: Path = Path(__file__).resolve().parents[2]
DOCKERFILE_PATH: Path = ROOT / "docker" / "ui.Dockerfile"


def _read_dockerfile() -> str:
    if not DOCKERFILE_PATH.is_file():
        pytest.skip(f"file {DOCKERFILE_PATH} not present")
    return DOCKERFILE_PATH.read_text(encoding="utf-8")


def _logical_lines(text: str) -> list[str]:
    """Join Dockerfile continuation lines (``\\`` at end of line) into one."""

    out: list[str] = []
    buf = ""
    for raw in text.splitlines():
        # Strip comments-only lines and blank lines once joined.
        stripped = raw.rstrip()
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        buf += stripped
        line = buf.strip()
        buf = ""
        if not line or line.startswith("#"):
            continue
        out.append(line)
    if buf.strip():
        out.append(buf.strip())
    return out


# Regex to peel off the leading 'COPY' instruction and any flags
# (e.g. '--from=<stage>', '--chown=<user>:<group>', '--chmod=...').
_COPY_HEAD = re.compile(r"^COPY\s+((?:--\S+\s+)*)(.*)$", re.IGNORECASE)


def _copy_sources_from_build_context(text: str) -> list[tuple[str, str]]:
    """Return ``[(source, full_line)]`` for every COPY whose source is
    the build context (i.e. ``--from=`` is absent)."""

    out: list[tuple[str, str]] = []
    for line in _logical_lines(text):
        if not line.upper().startswith("COPY "):
            continue
        match = _COPY_HEAD.match(line)
        if not match:
            continue
        flags = match.group(1) or ""
        rest = match.group(2).strip()
        if "--from=" in flags.lower():
            # Stage-to-stage copy; not validated against the repo tree.
            continue
        # Tokenize the remaining args (sources... destination). We use
        # shlex so quoted paths with spaces survive intact.
        try:
            tokens = shlex.split(rest, posix=True)
        except ValueError:
            continue
        if len(tokens) < 2:
            continue
        for src in tokens[:-1]:
            out.append((src, line))
    return out


class UiDockerfilePathConsistencyTests(unittest.TestCase):
    """Each failure names the offending COPY line + missing path."""

    def test_ui_package_json_exists(self) -> None:
        path = ROOT / "ui" / "package.json"
        self.assertTrue(
            path.is_file(),
            f"{path} is required: docker/ui.Dockerfile's build stage "
            "copies it to seed `pnpm install`.",
        )

    def test_ui_vite_config_exists(self) -> None:
        path = ROOT / "ui" / "vite.config.ts"
        self.assertTrue(
            path.is_file(),
            f"{path} is required: the build stage runs `pnpm build` "
            "(tsc -b && vite build) which needs vite.config.ts.",
        )

    def test_ui_pnpm_lockfile_recommended(self) -> None:
        """Soft check: warn if ``ui/pnpm-lock.yaml`` is missing.

        --frozen-lockfile in the Dockerfile will fail without it, but
        first-time builds (or fresh checkouts that haven't run
        ``pnpm install`` yet) shouldn't fail this contract test.
        """

        path = ROOT / "ui" / "pnpm-lock.yaml"
        if not path.is_file():
            warnings.warn(
                f"{path} is missing; run `pnpm install` in ui/ before "
                "building the UI image (the Dockerfile's "
                "`pnpm install --frozen-lockfile` will otherwise fail).",
                UserWarning,
                stacklevel=2,
            )

    def test_dockerfile_copy_sources_resolve(self) -> None:
        text = _read_dockerfile()
        copies = _copy_sources_from_build_context(text)
        self.assertTrue(
            copies,
            f"{DOCKERFILE_PATH}: no build-context COPY directives found "
            "— Dockerfile cannot ship anything.",
        )
        # ui/pnpm-lock.yaml is allowed to be missing on a fresh checkout
        # (handled by ``test_ui_pnpm_lockfile_recommended``). Don't
        # double-fail here.
        soft_missing_allowed = {"ui/pnpm-lock.yaml"}
        missing: list[str] = []
        for src, full_line in copies:
            # Build-context paths are repo-relative. Strip leading './'
            # if present. Skip absolute paths that originate from a
            # previous WORKDIR-controlled stage (handled by --from=).
            rel = src.lstrip("./")
            if rel in soft_missing_allowed:
                continue
            candidate = ROOT / rel
            # Allow trailing-slash directory references; Path handles
            # both. A file glob like 'ui/*.json' would not resolve
            # directly — none currently exist, so fail loudly if added.
            if any(c in src for c in ("*", "?")):
                missing.append(
                    f"{src!r} (glob in COPY is not validated; line: {full_line!r})"
                )
                continue
            if not (candidate.is_file() or candidate.is_dir()):
                missing.append(f"{src!r} -> {candidate} (line: {full_line!r})")
        self.assertFalse(
            missing,
            f"{DOCKERFILE_PATH}: COPY sources do not exist in the build "
            f"context (repo root {ROOT}):\n  - "
            + "\n  - ".join(missing),
        )


if __name__ == "__main__":
    unittest.main()
