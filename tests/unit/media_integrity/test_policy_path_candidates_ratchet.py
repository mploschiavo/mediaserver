"""Ratchet: media-integrity policy loader must check every install root.

The factory bails with ``FileNotFoundError`` when ``servarr-policy.yaml``
isn't reachable, leaving the whole subsystem disabled at boot. The
operator sees "media-integrity service not configured" + "No adapters
configured" in the dashboard with no obvious cause. This actually
happened on v1.0.229 — the wheel-based image installs contracts under
``/opt/media-stack/contracts/`` but the loader only checked
``/contracts/`` (the legacy bind-mount path used by the source-tree
image), so the file was present in the image and the path resolver
missed it.

This ratchet locks the candidate list so a future restructure can't
silently drop a path again.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from media_stack.services.media_integrity import policy as _policy


class PolicyPathCandidatesRatchet(unittest.TestCase):
    EXPECTED_CANDIDATES = (
        # Source-tree (dev / repo-mounted controller).
        Path(__file__).resolve().parents[3]
        / "src"
        / "media_stack"
        / "services"
        / "media_integrity",
        # Install-root used by the wheel-based image
        # (deploy/compose/controller.Dockerfile, v1.0.211+).
        Path("/opt/media-stack/contracts/servarr-policy.yaml"),
        # Legacy bind-mount path used by the source-tree image.
        Path("/contracts/servarr-policy.yaml"),
    )

    def test_candidate_paths_include_install_root(self) -> None:
        candidates = list(_policy._CONTRACT_PATH_CANDIDATES)
        self.assertIn(
            Path("/opt/media-stack/contracts/servarr-policy.yaml"),
            candidates,
            "Wheel-based image install root must be a candidate; "
            "without it the controller silently disables media-integrity.",
        )
        self.assertIn(
            Path("/contracts/servarr-policy.yaml"),
            candidates,
            "Legacy bind-mount path must remain a candidate so older "
            "compose deployments keep working.",
        )

    def test_resolver_picks_first_existing_candidate(self) -> None:
        # If the first candidate exists, it wins — even if later
        # candidates would also resolve.
        with mock.patch.object(Path, "exists", autospec=True) as exists:
            exists.return_value = True
            chosen = _policy._default_contract_path()
        self.assertEqual(chosen, _policy._CONTRACT_PATH_CANDIDATES[0])

    def test_resolver_falls_back_to_legacy_path_when_nothing_exists(self) -> None:
        with mock.patch.object(Path, "exists", autospec=True) as exists:
            exists.return_value = False
            chosen = _policy._default_contract_path()
        # Legacy path wins as the final fallback so the
        # FileNotFoundError surfaces with a familiar path string.
        self.assertEqual(chosen, _policy._CONTRACT_PATH_CONTAINER)


if __name__ == "__main__":
    unittest.main()
