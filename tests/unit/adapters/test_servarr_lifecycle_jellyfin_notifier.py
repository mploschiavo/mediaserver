"""Tests for ``ServarrLifecycle.probe_jellyfin_notifier`` and
``ensure_jellyfin_notifier`` — the lifecycle-method port of the
legacy ``ensure_arr_jellyfin_notifier`` job handler (ADR-0005
Phase 3 cutover).

Three families of behavior:

  * Unsupported services (prowlarr, readarr) short-circuit to ok /
    success — those *arrs don't expose the MediaBrowser notifier
    schema. The lifecycle methods exist on every ServarrLifecycle
    instance but become no-ops for those service ids.
  * Probe maps the *arr's notifier list to the tri-state ProbeResult
    (ok=name found, failed=name missing, unknown=can't reach).
  * Ensurer is idempotent (skip POST when probe says ok),
    transient-failure when prerequisites aren't met (no Jellyfin key,
    no arr key — orchestrator retries on next tick), permanent-
    failure on 4xx from the arr's API.

No real HTTP — urllib is mocked. The probe + ensurer parse JSON
shapes that match what real *arrs return, so the assertion logic
is exercised against representative payloads.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from media_stack.adapters.servarr.lifecycle import ServarrLifecycle
from media_stack.domain.services import OrchestrationContext


_JELLYFIN_KEY = "jf-test-key-1234567890"
_ARR_KEY = "arr-test-key-abcdef"


@pytest.fixture(autouse=True)
def _clear_envs():
    import os as _os
    yield
    for var in (
        "SONARR_API_KEY", "RADARR_API_KEY", "LIDARR_API_KEY",
        "READARR_API_KEY", "PROWLARR_API_KEY", "JELLYFIN_API_KEY",
    ):
        _os.environ.pop(var, None)


def _ctx(
    service_id: str = "sonarr",
    *, jf_key: str = _JELLYFIN_KEY, arr_key: str = _ARR_KEY,
) -> OrchestrationContext:
    """Build an ``OrchestrationContext`` pre-populated with the
    Jellyfin + arr api keys in ``secrets``."""
    secrets: dict[str, str] = {}
    if jf_key:
        secrets["JELLYFIN_API_KEY"] = jf_key
    if arr_key:
        secrets[f"{service_id.upper()}_API_KEY"] = arr_key
    cfg = {
        "host": service_id,
        "port": 8989,
        "scheme": "http",
        "api_key_env": f"{service_id.upper()}_API_KEY",
    }
    return OrchestrationContext(
        service_id=service_id,
        config=cfg,
        secrets=secrets,
        now=lambda: 1700000000.0,
    )


def _http_response(body: bytes, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda *_: None
    return resp


# --- Unsupported-service short-circuit --------------------------------


class TestUnsupportedServicesShortCircuit:
    """``prowlarr`` and ``readarr`` don't expose MediaBrowser
    notifiers. The methods exist on every ServarrLifecycle (since
    one class covers all five *arrs), but for those ids both
    methods short-circuit without making any HTTP call."""

    @pytest.mark.parametrize("sid", ["prowlarr", "readarr"])
    def test_probe_returns_ok_with_unsupported_reason(
        self, sid: str,
    ) -> None:
        sl = ServarrLifecycle(sid)
        result = sl.probe_jellyfin_notifier(_ctx(sid))
        assert result.is_ok
        assert result.evidence.get("reason") == "unsupported_service"

    @pytest.mark.parametrize("sid", ["prowlarr", "readarr"])
    def test_ensure_returns_success_with_unsupported_reason(
        self, sid: str,
    ) -> None:
        sl = ServarrLifecycle(sid)
        outcome = sl.ensure_jellyfin_notifier(_ctx(sid))
        assert outcome.ok
        assert outcome.evidence.get("reason") == "unsupported_service"


# --- Probe behavior ---------------------------------------------------


class TestProbeJellyfinNotifier:

    @patch("urllib.request.urlopen")
    def test_ok_when_notifier_present(
        self, mock_open: MagicMock,
    ) -> None:
        # Real *arrs return a list of notifier dicts, each with a
        # ``name`` and ``id``.
        body = json.dumps([
            {"id": 1, "name": "media-stack-jellyfin",
             "implementation": "MediaBrowser"},
        ]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("sonarr")
        result = sl.probe_jellyfin_notifier(_ctx("sonarr"))
        assert result.is_ok
        assert result.evidence.get("notifier_id") == 1

    @patch("urllib.request.urlopen")
    def test_failed_when_notifier_missing(
        self, mock_open: MagicMock,
    ) -> None:
        # Empty list → notifier not configured → failed (orchestrator
        # will dispatch ensure_jellyfin_notifier next).
        mock_open.return_value = _http_response(b"[]")

        sl = ServarrLifecycle("sonarr")
        result = sl.probe_jellyfin_notifier(_ctx("sonarr"))
        assert result.status == "failed"
        assert "not present" in result.detail

    @patch("urllib.request.urlopen")
    def test_failed_when_other_notifier_present(
        self, mock_open: MagicMock,
    ) -> None:
        # A different notifier exists (Discord, custom, etc.) — our
        # one is still missing → failed.
        body = json.dumps([
            {"id": 99, "name": "discord-bot",
             "implementation": "Discord"},
        ]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("radarr")
        result = sl.probe_jellyfin_notifier(_ctx("radarr"))
        assert result.status == "failed"

    @patch("urllib.request.urlopen")
    def test_unknown_when_arr_unreachable(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.side_effect = urllib.error.URLError("dns")

        sl = ServarrLifecycle("lidarr")
        result = sl.probe_jellyfin_notifier(_ctx("lidarr"))
        assert result.status == "unknown"

    def test_unknown_when_no_arr_api_key(self) -> None:
        # No api key in secrets, no env var, no config.xml — probe
        # returns unknown (orchestrator retries after probe_has_api_key
        # reaches ok).
        sl = ServarrLifecycle("sonarr")
        result = sl.probe_jellyfin_notifier(_ctx("sonarr", arr_key=""))
        assert result.status == "unknown"
        assert "api key" in result.detail.lower()

    def test_unknown_when_no_host_in_config(self) -> None:
        # Mis-configured service entry: no host. Probe returns
        # unknown rather than failed — operator likely fixed it
        # downstream.
        ctx = OrchestrationContext(
            service_id="sonarr",
            config={"port": 8989},
            secrets={"SONARR_API_KEY": "k"},
            now=lambda: 0.0,
        )
        sl = ServarrLifecycle("sonarr")
        result = sl.probe_jellyfin_notifier(ctx)
        assert result.status == "unknown"


# --- Ensurer behavior -------------------------------------------------


class TestEnsureJellyfinNotifier:

    @patch("urllib.request.urlopen")
    def test_idempotent_when_already_configured(
        self, mock_open: MagicMock,
    ) -> None:
        # GET returns the notifier already present — no POST follows.
        body = json.dumps([
            {"id": 1, "name": "media-stack-jellyfin"},
        ]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_jellyfin_notifier(_ctx("sonarr"))
        assert outcome.ok
        assert outcome.evidence.get("reason") == "already_configured"
        # Exactly one urlopen call (the GET); no POST.
        assert mock_open.call_count == 1

    @patch("urllib.request.urlopen")
    def test_posts_payload_when_missing(
        self, mock_open: MagicMock,
    ) -> None:
        # First call (GET) returns empty list. Second call (POST) is
        # the create. Verify the payload carries the per-arr event
        # flags and the Jellyfin key / host / port.
        responses = [_http_response(b"[]"), _http_response(b"", status=201)]
        mock_open.side_effect = responses

        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_jellyfin_notifier(_ctx("sonarr"))
        assert outcome.ok
        assert mock_open.call_count == 2

        post_call = mock_open.call_args_list[1]
        req = post_call.args[0]
        assert req.get_method() == "POST"
        body = json.loads(req.data.decode())
        assert body["name"] == "media-stack-jellyfin"
        assert body["implementation"] == "MediaBrowser"
        assert body["onDownload"] is True   # sonarr-specific
        assert body["onImportComplete"] is True   # sonarr-specific
        # Common-off flags
        assert body["onGrab"] is False
        # Fields list carries jellyfin host:port + key
        fields = {f["name"]: f["value"] for f in body["fields"]}
        assert fields["host"] == "jellyfin"
        assert fields["port"] == 8096
        assert fields["apiKey"] == _JELLYFIN_KEY
        assert fields["updateLibrary"] is True
        assert fields["notify"] is False

    @patch("urllib.request.urlopen")
    def test_radarr_event_flags(self, mock_open: MagicMock) -> None:
        # Radarr's flag set is movie-shaped — onDownload, onUpgrade,
        # onMovieDelete, onMovieFileDelete. ``onMovieAdded`` is False
        # by intent (the file isn't there yet on add).
        responses = [_http_response(b"[]"), _http_response(b"", status=201)]
        mock_open.side_effect = responses
        sl = ServarrLifecycle("radarr")
        outcome = sl.ensure_jellyfin_notifier(_ctx("radarr"))
        assert outcome.ok
        body = json.loads(mock_open.call_args_list[1].args[0].data.decode())
        assert body["onDownload"] is True
        assert body["onMovieDelete"] is True
        assert body["onMovieAdded"] is False

    @patch("urllib.request.urlopen")
    def test_lidarr_event_flags(self, mock_open: MagicMock) -> None:
        # Lidarr uses onReleaseImport (= onDownload for albums) and
        # onTrackRetag — different field names from sonarr/radarr.
        responses = [_http_response(b"[]"), _http_response(b"", status=201)]
        mock_open.side_effect = responses
        sl = ServarrLifecycle("lidarr")
        outcome = sl.ensure_jellyfin_notifier(_ctx("lidarr"))
        assert outcome.ok
        body = json.loads(mock_open.call_args_list[1].args[0].data.decode())
        assert body["onReleaseImport"] is True
        assert body["onTrackRetag"] is True
        assert body["onArtistAdd"] is False

    def test_transient_failure_when_no_jellyfin_key(self) -> None:
        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_jellyfin_notifier(_ctx("sonarr", jf_key=""))
        assert not outcome.ok
        assert outcome.transient is True
        assert "JELLYFIN_API_KEY" in outcome.error

    def test_transient_failure_when_no_arr_key(self) -> None:
        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_jellyfin_notifier(_ctx("sonarr", arr_key=""))
        assert not outcome.ok
        assert outcome.transient is True
        assert "api key" in outcome.error.lower()

    @patch("urllib.request.urlopen")
    def test_permanent_failure_on_4xx(
        self, mock_open: MagicMock,
    ) -> None:
        # GET ok, POST returns 400 — payload-level problem, not
        # transient.
        responses = [
            _http_response(b"[]"),
            urllib.error.HTTPError(
                "http://sonarr/api", 400, "Bad", {}, None,
            ),
        ]
        mock_open.side_effect = responses

        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_jellyfin_notifier(_ctx("sonarr"))
        assert not outcome.ok
        assert outcome.transient is False
        assert outcome.evidence.get("http_status") == 400

    @patch("urllib.request.urlopen")
    def test_transient_failure_when_arr_unreachable(
        self, mock_open: MagicMock,
    ) -> None:
        # GET fails on URLError — _get_notifier_list returns None, so
        # the ensurer can't make a "skip if already configured"
        # decision. Surface as transient (orchestrator retries).
        mock_open.side_effect = urllib.error.URLError("dns")

        sl = ServarrLifecycle("lidarr")
        outcome = sl.ensure_jellyfin_notifier(_ctx("lidarr"))
        assert not outcome.ok
        assert outcome.transient is True
