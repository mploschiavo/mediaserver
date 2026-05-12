"""Ratchet: ui/package.json version must match VERSION-UI.

These two strings serve different purposes but must agree:

* ``VERSION-UI`` is what ``bin/build/build-ui-image.sh`` reads to tag
  the container image (``harbor.iomio.io/public/media-stack-ui:vX.Y.Z``).
* ``ui/package.json``'s ``version`` field is read by Vite at build time
  and baked into the JS bundle — it's what ``UpdateAvailableBanner``
  and the operator-visible "version" pill display.

When they drift, deploys ship images tagged vX.Y.Z whose bundled JS
still claims to be vX.Y.W. That's exactly the bug that lured us when
v1.3.41 was tagged but the bundle still said 1.3.20: the operator
saw an "old" UI, doubted whether `:latest` had actually shipped, and
hit a stale ExposureCard that crashed the page.

This ratchet enforces parity. Bumping one without the other is a
release-tooling regression — fix release.sh / build_ui_image_main.py
to bump both, don't suppress this test.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_ui_package_json_version_matches_version_ui_file() -> None:
    version_file = REPO_ROOT / "VERSION-UI"
    package_json = REPO_ROOT / "ui" / "package.json"

    assert version_file.is_file(), (
        f"VERSION-UI is missing at {version_file} — the build script "
        f"reads this to tag the image. Restore it from git history."
    )
    assert package_json.is_file(), (
        f"ui/package.json is missing at {package_json}."
    )

    file_version = version_file.read_text(encoding="utf-8").strip()
    pkg_version = json.loads(
        package_json.read_text(encoding="utf-8"),
    ).get("version", "").strip()

    assert file_version == pkg_version, (
        "VERSION-UI and ui/package.json drifted apart:\n"
        f"  VERSION-UI       : {file_version!r}\n"
        f"  ui/package.json  : {pkg_version!r}\n\n"
        "Bump both atomically. The image tag (VERSION-UI) and the "
        "JS bundle's baked-in version (package.json) must agree, "
        "or operators see a UI claiming to be a different version "
        "than the image they pulled."
    )
