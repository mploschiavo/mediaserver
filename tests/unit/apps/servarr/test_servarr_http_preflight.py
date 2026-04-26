"""Tests for ServarrHttpPreflight's UrlBase reconciliation.

2026-04-20 Prowlarr blank-page recurrence: the preflight patched
``<UrlBase>/app/prowlarr</UrlBase>`` into config.xml, the app
restarted, then Prowlarr rehydrated config.xml from its own
SQLite DB and overwrote the UrlBase back to empty. The file edit
alone isn't sufficient — we have to PUT the value through the
app's API so it lands in the DB.

These tests pin the API-based reconciliation so the bug can't
recur:

- GET hit, urlBase matches desired → no PUT (no-op).
- GET hit, urlBase drifted → PUT sent with full config + updated
  urlBase.
- Missing API key in env → skipped cleanly (doesn't blow up
  bootstrap).
- GET returns non-200 or raises → skipped cleanly.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.servarr.http_preflight import (  # noqa: E402
    ServarrHttpPreflight,
)


class UrlBaseReconcileTests(unittest.TestCase):

    def _preflight(self, **env: str) -> ServarrHttpPreflight:
        return ServarrHttpPreflight(env=env)

    def _mock_requests(self, get_status=200, get_body=None, put_status=200):
        """Install a requests mock that returns the given status on
        GET /api/*/config/host and PUT on the same URL."""
        mock = MagicMock()
        get_resp = MagicMock()
        get_resp.status_code = get_status
        get_resp.json.return_value = (
            get_body if get_body is not None
            else {"urlBase": "", "bindAddress": "*"}
        )
        mock.get.return_value = get_resp
        put_resp = MagicMock()
        put_resp.status_code = put_status
        mock.put.return_value = put_resp
        return mock

    def test_reconcile_puts_when_urlbase_drifted(self):
        """The canonical fix: GET shows urlBase='', PUT must be
        called with urlBase='/app/prowlarr'. Without this the file
        patch is silently reverted by Prowlarr's DB rewrite."""
        mock = self._mock_requests()
        with patch(
            "media_stack.services.apps.servarr.http_preflight.requests",
            mock,
        ):
            self._preflight(PROWLARR_API_KEY="test-key-1234") \
                ._reconcile_url_base("prowlarr", log=lambda m: None)
        mock.put.assert_called_once()
        put_kwargs = mock.put.call_args.kwargs
        self.assertEqual(
            put_kwargs["json"]["urlBase"], "/app/prowlarr",
            "PUT must carry urlBase=/app/prowlarr so Prowlarr's "
            "DB persists it across restarts.",
        )
        self.assertEqual(
            put_kwargs["headers"]["X-Api-Key"], "test-key-1234",
            "API key header missing — Prowlarr will 401 the PUT.",
        )

    def test_reconcile_noop_when_urlbase_matches(self):
        """Don't thrash — if the DB already has the right value,
        skip the PUT."""
        mock = self._mock_requests(
            get_body={"urlBase": "/app/prowlarr", "bindAddress": "*"},
        )
        with patch(
            "media_stack.services.apps.servarr.http_preflight.requests",
            mock,
        ):
            self._preflight(PROWLARR_API_KEY="k") \
                ._reconcile_url_base("prowlarr", log=None)
        mock.put.assert_not_called()

    def test_reconcile_skipped_when_no_api_key(self):
        """Early in bootstrap the API key may not be discovered
        yet. Skip cleanly — don't raise or spam error logs."""
        mock = self._mock_requests()
        with patch(
            "media_stack.services.apps.servarr.http_preflight.requests",
            mock,
        ):
            self._preflight()._reconcile_url_base("prowlarr", log=None)
        mock.get.assert_not_called()
        mock.put.assert_not_called()

    def test_reconcile_skipped_when_get_fails(self):
        """If the app is still starting / returns 503, we don't
        want to block bootstrap. Skip this cycle; the dashboard's
        periodic reconcile will catch it later."""
        mock = self._mock_requests(get_status=503)
        with patch(
            "media_stack.services.apps.servarr.http_preflight.requests",
            mock,
        ):
            self._preflight(PROWLARR_API_KEY="k") \
                ._reconcile_url_base("prowlarr", log=None)
        mock.put.assert_not_called()

    def test_reconcile_uses_v3_for_sonarr_and_radarr(self):
        """Sonarr and Radarr use API v3; the others use v1.
        Hitting the wrong version returns 404 and urlBase never
        gets set."""
        for app in ("sonarr", "radarr"):
            mock = self._mock_requests()
            with patch(
                "media_stack.services.apps.servarr.http_preflight.requests",
                mock,
            ):
                self._preflight(**{f"{app.upper()}_API_KEY": "k"}) \
                    ._reconcile_url_base(app, log=None)
            url = mock.get.call_args.args[0]
            self.assertIn(
                "/api/v3/config/host", url,
                f"{app} reconcile hit {url} — must use v3.",
            )

    def test_reconcile_uses_v1_for_prowlarr_lidarr_readarr(self):
        for app in ("prowlarr", "lidarr", "readarr"):
            mock = self._mock_requests()
            with patch(
                "media_stack.services.apps.servarr.http_preflight.requests",
                mock,
            ):
                self._preflight(**{f"{app.upper()}_API_KEY": "k"}) \
                    ._reconcile_url_base(app, log=None)
            url = mock.get.call_args.args[0]
            self.assertIn(
                "/api/v1/config/host", url,
                f"{app} reconcile hit {url} — must use v1.",
            )


if __name__ == "__main__":
    unittest.main()
