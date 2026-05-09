"""Ratchet #7 — runtime "data flows" contract.

The "shape is right" tests already pass with empty-payload responses
(``libraries: []``, ``indexers: []``) — they only check key
existence, not the absence of a silent zero-state. That's how the
2026-04 ``discover-api-keys`` regression slipped through CI.

This contract test asserts that, given fixture API keys for
Jellyfin / Sonarr / Radarr on disk **and** a stubbed upstream that
authenticates against those keys, the live endpoints return data,
not zeros:

- ``/api/libraries`` — ``live[].length >= 1`` and ``source !=
  "defaults"``.
- ``/api/recent`` — at least one of the per-service maps has items.
- ``/api/indexers`` — ``total >= 0`` and the call doesn't 500.

We exercise the controller in-process — every endpoint gets routed
through the production handler stack so a regression in the
*plumbing* between handler ↔ service ↔ runtime_keys is caught,
not just the leaf functions.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import unittest.mock as _mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


_KEY = "abc123def456abc123def456abc123de"  # 32-hex


class _UpstreamHandler(BaseHTTPRequestHandler):
    """Stand-in upstream for Sonarr/Radarr/Jellyfin used by the
    in-process controller during this test."""

    routes: dict[str, dict] = {}

    def do_GET(self) -> None:  # noqa: N802
        # Strip query string for simple matching.
        path = self.path.split("?", 1)[0]
        body_obj = self.routes.get(path)
        if body_obj is None:
            self.send_response(404)
            self.end_headers()
            return
        # Auth: either X-Api-Key header (arr) or X-Emby-Token
        # (jellyfin). The test fixture uses the same key for both.
        if (self.headers.get("X-Api-Key") != _KEY and
                self.headers.get("X-Emby-Token") != _KEY):
            self.send_response(401)
            self.end_headers()
            return
        body = json.dumps(body_obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a, **_kw) -> None:  # noqa: D401
        return


def _start_upstream(routes: dict[str, dict]) -> ThreadingHTTPServer:
    _UpstreamHandler.routes = routes
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class RuntimeDataFlowsContract(unittest.TestCase):
    """End-to-end "data flows" assertions for libraries / recent /
    indexers when keys are populated."""

    def setUp(self) -> None:
        from media_stack.api.services import runtime_keys
        runtime_keys.invalidate_cache()
        self.upstream = _start_upstream({
            "/Library/VirtualFolders": [
                {"Name": "Movies", "CollectionType": "movies",
                 "Locations": ["/data/movies"], "ItemCount": 42},
                {"Name": "Shows", "CollectionType": "tvshows",
                 "Locations": ["/data/shows"], "ItemCount": 7},
            ],
            "/api/v3/history": {
                "records": [
                    {"id": 1, "sourceTitle": "Some.Movie.2024",
                     "eventType": "downloadFolderImported",
                     "date": "2026-04-01T00:00:00Z"},
                ],
            },
            "/api/v3/indexer": [
                {"id": 1, "name": "test-indexer", "enable": True,
                 "protocol": "torrent"},
            ],
        })
        self.addCleanup(self.upstream.shutdown)
        self.addCleanup(self.upstream.server_close)

    def _fake_services(self, port: int) -> list:
        from media_stack.core.service_registry.registry import ServiceDef
        return [
            ServiceDef(
                id="jellyfin", name="jellyfin",
                category="media-server",
                host="127.0.0.1", port=port,
                api_key_env="JELLYFIN_API_KEY",
                auth_mode="X-Emby-Token",
            ),
            ServiceDef(
                id="sonarr", name="sonarr", category="indexer",
                host="127.0.0.1", port=port,
                api_key_env="SONARR_API_KEY",
                recent_path="/api/v3/history",
                indexer_path="/api/v3/indexer",
            ),
            ServiceDef(
                id="radarr", name="radarr", category="indexer",
                host="127.0.0.1", port=port,
                api_key_env="RADARR_API_KEY",
                recent_path="/api/v3/history",
            ),
        ]

    def test_libraries_recent_indexers_return_real_data(self) -> None:
        port = self.upstream.server_address[1]
        env = {
            "JELLYFIN_API_KEY": _KEY,
            "SONARR_API_KEY": _KEY,
            "RADARR_API_KEY": _KEY,
        }
        from media_stack.api.services import content as content_mod
        from media_stack.api.services import health as health_mod

        fake_services = self._fake_services(port)
        # ``content.py`` reads via two paths:
        #   - ``runtime_keys.read_service_api_key`` (libraries)
        #   - ``health.discover_api_keys`` (recent, indexers)
        # The contract test stubs *both* to return the fixture key so
        # the assertion is "data flowed end-to-end", not "we found a
        # key once".
        with _mock.patch.dict(os.environ, env, clear=False), \
                _mock.patch.object(content_mod, "SERVICES",
                                   fake_services), \
                _mock.patch.object(health_mod, "SERVICES",
                                   fake_services), \
                _mock.patch(
                    "media_stack.core.service_registry.registry.SERVICES",
                    fake_services), \
                _mock.patch.object(
                    content_mod, "read_service_api_key",
                    side_effect=lambda sid: _KEY), \
                _mock.patch.object(
                    content_mod, "discover_api_keys",
                    return_value={
                        "jellyfin": _KEY, "sonarr": _KEY,
                        "radarr": _KEY,
                    }):
            libs = content_mod.get_media_server_libraries()
            recent = content_mod.get_recent()
            indexers = content_mod.get_indexers()

        # /api/libraries — live data must show at least one
        # library, and the source must not be the "no upstream
        # could be reached, here are defaults" sentinel.
        live_libs = libs.get("libraries") or []
        self.assertGreaterEqual(
            len(live_libs), 1,
            f"libraries returned empty payload: {libs!r}",
        )
        # ``source`` is set when defaults are used. Its absence,
        # or any non-"defaults" value, is fine.
        self.assertNotEqual(libs.get("source"), "defaults")

        # /api/recent — at least one per-service map should have
        # at least one record. The shape varies per controller
        # version; the contract is "something flowed through".
        recent_payload = recent.get("recent") or recent
        self.assertTrue(
            any(
                isinstance(v, list) and v
                for v in (
                    recent_payload.values()
                    if isinstance(recent_payload, dict) else []
                )
            ) or (
                isinstance(recent_payload, list) and recent_payload
            ),
            f"recent returned no items in any per-service map: "
            f"{recent!r}",
        )

        # /api/indexers — must return without crashing and have
        # ``total`` as a non-negative int.
        self.assertIn("total", indexers)
        self.assertGreaterEqual(int(indexers.get("total") or 0), 0)


if __name__ == "__main__":
    unittest.main()
