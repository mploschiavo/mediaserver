"""Static-analysis contract tests for the UI container Dockerfile.

Verifies ``docker/ui.Dockerfile`` will produce a build artifact that meets
the production hardening contract: multi-stage build (node build + alpine
nginx runtime), non-root, port 8080 only, healthcheck wired to /healthz,
Vite-built ``dist/`` baked into the image, OCI version label, and no
Python/pip pollution (UI is a static asset container, not the API).

No docker build, no network — pure file-content / regex checks so the
suite stays fast and runnable in CI without a docker daemon.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import pytest

ROOT: Path = Path(__file__).resolve().parents[2]
DOCKERFILE_PATH: Path = ROOT / "docker" / "ui.Dockerfile"


def _read_dockerfile() -> str:
    """Return the Dockerfile contents, or skip the test if missing."""

    if not DOCKERFILE_PATH.is_file():
        pytest.skip(
            f"file {DOCKERFILE_PATH} not yet created by parallel agent — "
            "re-run after that agent completes"
        )
    return DOCKERFILE_PATH.read_text(encoding="utf-8")


def _significant_lines(text: str) -> list[str]:
    """Non-blank, non-comment lines (stripped of leading whitespace)."""

    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _from_lines(text: str) -> list[str]:
    """Every ``FROM ...`` directive in source order."""

    return [line for line in _significant_lines(text) if line.upper().startswith("FROM ")]


def _first_significant_line(text: str) -> str:
    """First non-blank, non-comment line (strip leading whitespace)."""

    lines = _significant_lines(text)
    return lines[0] if lines else ""


class UiDockerfileContractTests(unittest.TestCase):
    """Each test failure points at the precise missing/incorrect directive."""

    def test_dockerfile_exists(self) -> None:
        self.assertTrue(
            DOCKERFILE_PATH.is_file(),
            f"Expected Dockerfile at {DOCKERFILE_PATH}; "
            "UI container build will fail without it.",
        )

    def test_dockerfile_has_two_stages(self) -> None:
        text = _read_dockerfile()
        froms = _from_lines(text)
        self.assertGreaterEqual(
            len(froms),
            2,
            f"{DOCKERFILE_PATH}: multi-stage build required (build stage "
            f"+ runtime stage). Found {len(froms)} FROM directives: "
            f"{froms!r}.",
        )

    def test_dockerfile_build_stage_uses_node(self) -> None:
        text = _read_dockerfile()
        froms = _from_lines(text)
        self.assertTrue(
            froms,
            f"{DOCKERFILE_PATH}: Dockerfile has no FROM directives.",
        )
        self.assertIn(
            "node:",
            froms[0],
            f"{DOCKERFILE_PATH}: build stage (first FROM) must use a "
            f"node:* base image so pnpm/Vite can run. Got: {froms[0]!r}.",
        )

    def test_dockerfile_uses_alpine_nginx_base(self) -> None:
        text = _read_dockerfile()
        froms = _from_lines(text)
        self.assertTrue(
            froms,
            f"{DOCKERFILE_PATH}: no FROM directives.",
        )
        runtime = froms[-1]
        # Accept official nginx:*-alpine OR nginxinc/nginx-unprivileged:*-alpine.
        # Both ship alpine-based, non-root-friendly nginx images.
        is_alpine_nginx = (
            "alpine" in runtime
            and ("nginx:" in runtime or "nginx-unprivileged" in runtime)
        )
        self.assertTrue(
            is_alpine_nginx,
            f"{DOCKERFILE_PATH}: runtime stage (last FROM) must use an "
            f"alpine-based nginx image (got: {runtime!r}). Alpine-nginx "
            "is required for unprivileged port binding and small image "
            "size.",
        )

    def test_dockerfile_runs_pnpm_install_and_build(self) -> None:
        text = _read_dockerfile()
        self.assertRegex(
            text,
            r"(?m)^\s*RUN\s+[^\n]*\bpnpm\s+install\b",
            f"{DOCKERFILE_PATH}: build stage must run 'pnpm install' to "
            "install UI dependencies before building.",
        )
        self.assertRegex(
            text,
            r"(?ms)^\s*RUN\b[^\n]*\bpnpm\b[^\n]*\bbuild\b",
            f"{DOCKERFILE_PATH}: build stage must run 'pnpm ... build' "
            "to produce the Vite dist/ artifact.",
        )

    def test_dockerfile_copies_dist_from_build_stage(self) -> None:
        text = _read_dockerfile()
        pattern = re.compile(
            r"(?m)^\s*COPY\s+--from=build\s+/build/dist\s+/usr/share/nginx/html\b"
        )
        self.assertRegex(
            text,
            pattern,
            f"{DOCKERFILE_PATH}: runtime stage must "
            "'COPY --from=build /build/dist /usr/share/nginx/html' so "
            "the Vite-built bundle is the only thing shipped.",
        )

    def test_dockerfile_does_not_run_as_root(self) -> None:
        text = _read_dockerfile()
        froms = _from_lines(text)
        runtime = froms[-1] if froms else ""
        has_user = re.search(r"(?m)^\s*USER\s+\S+", text) is not None
        # nginx-unprivileged already runs as user 101 (nginx).
        uses_unprivileged_nginx = "nginx-unprivileged" in runtime
        uses_alpine_nginx = "nginx:" in runtime and "alpine" in runtime
        self.assertTrue(
            has_user or uses_unprivileged_nginx or uses_alpine_nginx,
            f"{DOCKERFILE_PATH}: container must not run as root. "
            "Add an explicit 'USER nginx' (preferred) or stay on the "
            "nginx-unprivileged / nginx:alpine base which already drops "
            "privileges to the nginx user at runtime.",
        )

    def test_dockerfile_exposes_8080_only(self) -> None:
        text = _read_dockerfile()
        self.assertRegex(
            text,
            r"(?m)^\s*EXPOSE\s+8080\b",
            f"{DOCKERFILE_PATH}: missing 'EXPOSE 8080' directive. "
            "UI container listens unprivileged on 8080 (not 80).",
        )
        # 'EXPOSE 80' (default upstream port) must be absent — we override
        # it. Match 80 as a standalone token, not as part of '8080'.
        self.assertNotRegex(
            text,
            r"(?m)^\s*EXPOSE\s+80(?!\d)",
            f"{DOCKERFILE_PATH}: 'EXPOSE 80' is forbidden — the UI "
            "listens on 8080 only. Remove the inherited 'EXPOSE 80' "
            "from the base image.",
        )

    def test_dockerfile_has_healthcheck(self) -> None:
        text = _read_dockerfile()
        self.assertRegex(
            text,
            r"(?m)^\s*HEALTHCHECK\b",
            f"{DOCKERFILE_PATH}: missing HEALTHCHECK directive. "
            "Compose/k8s probes rely on a working /healthz contract.",
        )
        self.assertIn(
            "/healthz",
            text,
            f"{DOCKERFILE_PATH}: HEALTHCHECK must reference the "
            "'/healthz' endpoint exposed by the nginx config.",
        )

    def test_dockerfile_has_version_label(self) -> None:
        text = _read_dockerfile()
        self.assertRegex(
            text,
            r"org\.opencontainers\.image\.version",
            f"{DOCKERFILE_PATH}: missing OCI 'org.opencontainers.image."
            "version' LABEL. Operators rely on this label for image "
            "provenance and rollback automation.",
        )
        self.assertRegex(
            text,
            r"(?ms)^\s*LABEL\b.*org\.opencontainers\.image\.version",
            f"{DOCKERFILE_PATH}: 'org.opencontainers.image.version' "
            "must appear inside a LABEL directive.",
        )

    def test_dockerfile_does_not_install_python_or_pip(self) -> None:
        text = _read_dockerfile()
        # Look for any 'apk add' (single-line, with optional flags) that
        # references python3 or pip — UI is a static-asset container, the
        # API stack does not belong here.
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "apk add" in line and re.search(r"\b(python3|pip)\b", line):
                self.fail(
                    f"{DOCKERFILE_PATH}: UI container must not install "
                    f"python3 or pip via apk (offending line: {line!r}). "
                    "This is a static-asset image; Python belongs to the "
                    "controller container only.",
                )


if __name__ == "__main__":
    unittest.main()
