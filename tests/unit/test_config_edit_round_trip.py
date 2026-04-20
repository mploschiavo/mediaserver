"""Round-trip tests for library + download-category editors.

These are the two most-edited config panels in the UI after user
management. Both persist through the app-config YAML layer. The
failure mode they guard against: UI sends update, handler returns
``{"status": "updated"}``, but get_libraries() / get_download_categories()
still show the old values because the write silently went to the
wrong file (or a cache never invalidated).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.config._downloads import (  # noqa: E402
    DownloadConfigService,
)
from media_stack.api.services.config._media_server import (  # noqa: E402
    LibraryConfigService,
)


class _FakeProfile:
    """Profile stand-in whose load() returns (data, path) and whose
    update_section() writes back into the in-memory dict."""

    def __init__(self, path: Path, data: dict):
        self._path = path
        self._data = data
        self._path.write_text(yaml.safe_dump(data), encoding="utf-8")

    def load(self):
        return (self._data, self._path)

    def media_server_id(self) -> str:
        return "jellyfin"

    def update_section(self, key: str, value) -> dict:
        self._data[key] = value
        self._path.write_text(yaml.safe_dump(self._data), encoding="utf-8")
        return {"status": "updated"}


class DownloadCategoriesRoundTripTests(unittest.TestCase):
    def _svc(self, profile):
        return DownloadConfigService(profile)

    def test_empty_payload_rejected(self):
        """Zero categories would silently erase the existing list if
        not rejected up front. Reject with a clear error."""
        with tempfile.TemporaryDirectory() as d:
            profile = _FakeProfile(Path(d) / "profile.yaml", {
                "technology_bindings": {},
            })
            svc = self._svc(profile)
            result = svc.update_download_categories({})
            self.assertIn("error", result)

    def test_write_persists_to_profile_when_no_torrent_client(self):
        """Fallback path: no torrent_client binding → write to
        profile.download_categories. A round-trip through get_ must
        return what was written."""
        with tempfile.TemporaryDirectory() as d:
            profile = _FakeProfile(Path(d) / "profile.yaml", {
                "technology_bindings": {"torrent_client": None},
            })
            svc = self._svc(profile)
            cats = {"movies": "Movies", "tv": "TV", "music": "Music"}
            svc.update_download_categories(cats)
            got = svc.get_download_categories()
            self.assertEqual(got["categories"], cats)


class LibraryRoundTripTests(unittest.TestCase):
    def _svc(self, profile):
        return LibraryConfigService(profile)

    def test_update_rejects_library_with_missing_fields(self):
        """A library missing name/collection_type/paths must fail
        validation — would otherwise produce a half-written config
        that the media server can't consume."""
        with tempfile.TemporaryDirectory() as d:
            profile = _FakeProfile(Path(d) / "profile.yaml", {})
            svc = self._svc(profile)
            result = svc.update_libraries([
                {"name": "Movies"},  # missing collection_type + paths
            ])
            self.assertIn("error", result)

    def test_update_rejects_when_no_media_server_configured(self):
        """When no media server exists (fresh install), the edit
        must fail loudly — not silently write to the void."""
        profile = MagicMock()
        profile.media_server_id.return_value = ""
        profile.load.return_value = ({}, None)
        svc = self._svc(profile)
        result = svc.update_libraries([{
            "name": "Movies",
            "collection_type": "movies",
            "paths": ["/media/movies"],
        }])
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
