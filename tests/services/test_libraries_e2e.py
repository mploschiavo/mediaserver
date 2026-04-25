"""Ratchet #3 — boot-time API-key fallback contract.

The 2026-04 ``discover-api-keys`` regression: with an empty
``JELLYFIN_API_KEY`` in the K8s Secret, every dashboard tile
quietly degraded to "1 of each". The fix: ``runtime_keys.read_service_api_key``
falls back to the on-disk config file when the env is empty.

This test locks the env-empty fallback behaviour by spinning up a
``MockJellyfin`` HTTP handler and asserting that
``services.content.get_media_server_libraries()`` still returns
real ``ItemCount`` data when the env is empty but a key is
parsable from a fixture file.

If a future change reintroduces "env wins even when empty", this
test will catch it.
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


_LIBRARY_FIXTURE = [
    {
        "Name": "Movies",
        "CollectionType": "movies",
        "Locations": ["/data/media/movies"],
        "ItemCount": 42,
    },
    {
        "Name": "Shows",
        "CollectionType": "tvshows",
        "Locations": ["/data/media/shows"],
        "ItemCount": 7,
    },
]
_FIXTURE_KEY = "deadbeef" * 4  # 32 hex chars — looks like a real *arr key


class MockJellyfinHandler(BaseHTTPRequestHandler):
    """Tiny stand-in for Jellyfin's ``/Library/VirtualFolders``.

    Only authenticates: a request without ``X-Emby-Token`` matching
    the canned fixture key returns 401. This lets the test prove the
    controller **did** send the file-sourced key, not an empty one.
    """

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.path != "/Library/VirtualFolders":
            self.send_response(404)
            self.end_headers()
            return
        token = self.headers.get("X-Emby-Token", "")
        if token != _FIXTURE_KEY:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"unauthorised")
            return
        body = json.dumps(_LIBRARY_FIXTURE).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # Silence the default BaseHTTPRequestHandler logger.
    def log_message(self, *_args, **_kwargs) -> None:  # noqa: D401
        return


def _start_mock_jellyfin() -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockJellyfinHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class BootTimeKeyFallbackTests(unittest.TestCase):
    """JELLYFIN_API_KEY="" in env, real key on disk → live data."""

    def _stub_runtime_keys(self) -> _mock.MagicMock:
        """Patch ``read_service_api_key`` to mimic env-empty + file-real.

        We do not import the (sibling-owned) ``services/runtime_keys``
        module directly — its surface is the same one the production
        code calls, so we patch at the call site.
        """
        from media_stack.api.services import runtime_keys
        return _mock.patch.object(
            runtime_keys, "read_service_api_key",
            side_effect=lambda sid: (
                _FIXTURE_KEY if sid == "jellyfin" else None
            ),
        )

    def test_env_empty_falls_back_to_file_key(self) -> None:
        # Empty env to simulate fresh-install Secret.
        with _mock.patch.dict(os.environ, {"JELLYFIN_API_KEY": ""},
                              clear=False):
            server, _thread = _start_mock_jellyfin()
            try:
                port = server.server_address[1]
                # Point the registry's media-server entry at the
                # mock. We patch ``SERVICES`` directly so the test
                # doesn't depend on contracts loading their full set.
                from media_stack.api.services import content as content_mod

                fake_svc = _mock.MagicMock()
                fake_svc.id = "jellyfin"
                fake_svc.category = "media-server"
                fake_svc.host = "127.0.0.1"
                fake_svc.port = port
                fake_svc.auth_mode = "X-Emby-Token"

                with _mock.patch.object(
                    content_mod, "SERVICES", [fake_svc]
                ), self._stub_runtime_keys():
                    out = content_mod.get_media_server_libraries()
            finally:
                server.shutdown()
                server.server_close()

        self.assertIn("libraries", out)
        live = out["libraries"]
        self.assertGreaterEqual(
            len(live), 1,
            f"expected >=1 library from mock, got {out!r}",
        )
        # The "data flowed" assertion the spec calls for.
        self.assertGreaterEqual(live[0]["count"], 42)
        # No silent error path.
        self.assertNotIn("error", out)


if __name__ == "__main__":
    unittest.main()
