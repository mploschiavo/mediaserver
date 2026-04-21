"""Ratchet: every release artifact must pin the same controller
image tag as ``VERSION``.

Why: kustomize's ``images: newTag:`` silently overrides the tag
written into the per-resource ``image:`` lines when ``kubectl
kustomize`` is run.  That made it possible for the source yaml
to read ``v1.0.97`` while ``dist/k8s-deploy.yaml`` (the file
end-users actually apply) regenerated to ``v1.0.96`` because a
stale ``newTag`` was sitting in ``k8s/kustomization.yaml``.  The
2026-04-21 incident: shipped v1.0.97 in compose but k8s deploys
silently stayed on v1.0.96 for the same release.

Coverage:

* All ``image: harbor.iomio.io/library/media-stack-controller:vX``
  references across the repo (compose source + dist + k8s
  manifests + dist k8s bundle) must read the current ``VERSION``.
* Every kustomize ``newTag:`` for that image must read the
  current ``VERSION`` — the regenerator obeys this, so a stale
  value here is the silent-override case described above.
* Compose's ``BOOTSTRAP_RUNNER_IMAGE`` default must read the
  current ``VERSION`` — it's the fallback when the env var
  isn't set, so a stale default rolls back the runner image
  on a clean deploy.

If you intentionally pin an older tag for a specific manifest
(e.g. compatibility test fixture), add an exclusion to
``_EXCLUDED_PATHS`` with a comment explaining why.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

_VERSION_FILE = ROOT / "VERSION"
_IMAGE_NAME = "harbor.iomio.io/library/media-stack-controller"

# File patterns to scan. Anything outside these globs is ignored.
_SCAN_GLOBS = (
    "docker/docker-compose.yml",
    "dist/docker-compose.yml",
    "dist/k8s-deploy.yaml",
    "k8s/**/*.yaml",
    "k8s/**/*.yml",
    "k8s/*.yaml",
    "k8s/*.yml",
)

# Files that intentionally pin a non-current tag. Empty for now —
# add entries here only with a comment explaining why.
_EXCLUDED_PATHS: set[str] = set()

# Matches ``image: ...media-stack-controller:vX.Y.Z`` (with optional
# leading whitespace and shell-style ``${VAR:-default}`` wrapping).
_IMAGE_LINE_RE = re.compile(
    rf"image:\s*[^#\n]*?{re.escape(_IMAGE_NAME)}:v(\d+\.\d+\.\d+)"
)
# Matches kustomize ``newTag: vX.Y.Z`` (we only check it when the
# preceding ``name:`` block is for the controller image — handled
# in the test by parsing the YAML).
_NEWTAG_LINE_RE = re.compile(r"newTag:\s*v(\d+\.\d+\.\d+)")
_NAME_LINE_RE = re.compile(r"-\s*name:\s*([^\s#]+)")
# Matches the compose ``BOOTSTRAP_RUNNER_IMAGE`` default.
_BOOTSTRAP_DEFAULT_RE = re.compile(
    rf"BOOTSTRAP_RUNNER_IMAGE:-{re.escape(_IMAGE_NAME)}:v(\d+\.\d+\.\d+)"
)


def _gather_files() -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for pat in _SCAN_GLOBS:
        for p in ROOT.glob(pat):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT).as_posix()
            if rel in _EXCLUDED_PATHS:
                continue
            if p in seen:
                continue
            seen.add(p)
            out.append(p)
    return out


def _scan_image_pins(text: str) -> list[str]:
    return _IMAGE_LINE_RE.findall(text)


def _scan_bootstrap_defaults(text: str) -> list[str]:
    return _BOOTSTRAP_DEFAULT_RE.findall(text)


def _scan_kustomize_newtags(text: str) -> list[str]:
    """Return ``newTag`` values that follow a ``name:`` block whose
    ``name`` is the controller image. Walks line-by-line so
    unrelated images in the same file don't trip the check."""
    out: list[str] = []
    current_name: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        nm = _NAME_LINE_RE.match(line)
        if nm:
            current_name = nm.group(1).strip()
            continue
        # Reset name when we hit a new top-level list item that
        # isn't a name/newName/newTag continuation.
        if line.startswith("- ") and not nm:
            current_name = None
        if current_name == _IMAGE_NAME:
            tm = _NEWTAG_LINE_RE.search(line)
            if tm:
                out.append(tm.group(1))
    return out


class VersionPinConsistencyRatchet(unittest.TestCase):

    def setUp(self) -> None:
        self.version = _VERSION_FILE.read_text(encoding="utf-8").strip()
        self.assertRegex(
            self.version, r"^\d+\.\d+\.\d+$",
            f"VERSION file content {self.version!r} isn't semver",
        )

    def test_all_release_artifacts_pin_current_version(self) -> None:
        files = _gather_files()
        self.assertGreater(
            len(files), 3,
            "Scanner found fewer files than expected — globs likely "
            "broken after a layout change.",
        )
        bad: list[str] = []
        for path in files:
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(ROOT).as_posix()

            for tag in _scan_image_pins(text):
                if tag != self.version:
                    bad.append(
                        f"{rel}: image pin v{tag} != VERSION v{self.version}"
                    )

            for tag in _scan_kustomize_newtags(text):
                if tag != self.version:
                    bad.append(
                        f"{rel}: kustomize newTag v{tag} != VERSION "
                        f"v{self.version} (this silently overrides "
                        f"image: lines on `kubectl kustomize`)"
                    )

            for tag in _scan_bootstrap_defaults(text):
                if tag != self.version:
                    bad.append(
                        f"{rel}: BOOTSTRAP_RUNNER_IMAGE default v{tag} "
                        f"!= VERSION v{self.version}"
                    )

        self.assertFalse(
            bad,
            "Controller image pins drifted from VERSION:\n  - "
            + "\n  - ".join(bad)
            + "\n\nFix: bump every stale tag to match VERSION, then "
              "re-run `bin/regen-dist.sh` so the dist/ bundles "
              "regenerate with the right tag.",
        )


if __name__ == "__main__":
    unittest.main()
