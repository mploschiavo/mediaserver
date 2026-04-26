"""Library-name → Jellyfin-ID resolution on policy apply.

Role catalog lets admins declare ``EnabledFolderNames`` for
restricted-access roles (e.g. kid). The Jellyfin provider resolves
names to IDs at apply time via ``/Library/MediaFolders`` so the catalog
can stay portable across installs.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.jellyfin.user_provider import (  # noqa: E402
    JellyfinApiProvider,
)


def _http(responses: dict) -> MagicMock:
    # Strip ?api_key=... before lookup — the provider now authenticates
    # via query param (Jellyfin 10.11 rejects the X-Api-Key header).
    def _req(base, path, api_key=None, method="GET", payload=None, **_kw):
        bare = str(path).split("?", 1)[0]
        return responses.get((method, bare), (404, None, ""))
    c = MagicMock()
    c.request.side_effect = _req
    return c


class LibraryNameResolutionTests(unittest.TestCase):
    def _provider(self, client):
        return JellyfinApiProvider(base_url="http://jf:8096", api_key="k",
                                    http_client=client)

    def test_update_user_resolves_library_names_to_ids(self):
        client = _http({
            ("GET", "/Library/MediaFolders"): (200, {
                "Items": [
                    {"Name": "Movies", "Id": "movies-id"},
                    {"Name": "Kids",   "Id": "kids-id"},
                    {"Name": "Family", "Id": "family-id"},
                ],
            }, ""),
            ("POST", "/Users/u1/Policy"): (204, None, ""),
        })
        p = self._provider(client)
        p.update_user("u1", policy={
            "EnableAllFolders": False,
            "EnabledFolderNames": ["Kids", "Family"],
            "MaxParentalRating": 7,
        })
        # Path now includes ?api_key=... — strip before matching
        policy_call = [c for c in client.request.call_args_list
                        if c.kwargs.get("method") == "POST"
                        and str(c.args[1]).split("?", 1)[0]
                                == "/Users/u1/Policy"][0]
        applied = policy_call.kwargs["payload"]
        self.assertNotIn("EnabledFolderNames", applied)
        self.assertEqual(set(applied["EnabledFolders"]),
                         {"kids-id", "family-id"})
        self.assertFalse(applied["EnableAllFolders"])
        self.assertEqual(applied["MaxParentalRating"], 7)

    def test_unknown_library_names_are_dropped_not_raised(self):
        client = _http({
            ("GET", "/Library/MediaFolders"): (200, {"Items": [
                {"Name": "Movies", "Id": "movies-id"},
            ]}, ""),
            ("POST", "/Users/u1/Policy"): (204, None, ""),
        })
        p = self._provider(client)
        p.update_user("u1", policy={
            "EnableAllFolders": False,
            "EnabledFolderNames": ["Kids", "DoesNotExist"],
        })
        policy_call = [c for c in client.request.call_args_list
                        if c.kwargs.get("method") == "POST"][0]
        applied = policy_call.kwargs["payload"]
        self.assertEqual(applied["EnabledFolders"], [])

    def test_case_insensitive_matching(self):
        client = _http({
            ("GET", "/Library/MediaFolders"): (200, {"Items": [
                {"Name": "kids", "Id": "kids-id"},
            ]}, ""),
            ("POST", "/Users/u1/Policy"): (204, None, ""),
        })
        p = self._provider(client)
        p.update_user("u1", policy={"EnabledFolderNames": ["KIDS"]})
        policy_call = [c for c in client.request.call_args_list
                        if c.kwargs.get("method") == "POST"][0]
        self.assertEqual(policy_call.kwargs["payload"]["EnabledFolders"],
                         ["kids-id"])

    def test_library_fetch_failure_yields_empty_ids(self):
        client = _http({
            ("GET", "/Library/MediaFolders"): (500, None, "server err"),
            ("POST", "/Users/u1/Policy"): (204, None, ""),
        })
        p = self._provider(client)
        p.update_user("u1", policy={"EnabledFolderNames": ["Kids"]})
        policy_call = [c for c in client.request.call_args_list
                        if c.kwargs.get("method") == "POST"][0]
        self.assertEqual(policy_call.kwargs["payload"]["EnabledFolders"], [])

    def test_policy_without_names_passes_through(self):
        """Roles that don't use EnabledFolderNames are unaffected."""
        client = _http({("POST", "/Users/u1/Policy"): (204, None, "")})
        p = self._provider(client)
        p.update_user("u1", policy={
            "IsAdministrator": True, "EnableAllFolders": True,
        })
        # Library fetch should not even be called
        paths_called = [c.args[1] for c in client.request.call_args_list]
        self.assertNotIn("/Library/MediaFolders", paths_called)


if __name__ == "__main__":
    unittest.main()
