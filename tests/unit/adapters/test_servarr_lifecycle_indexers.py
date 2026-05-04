"""Tests for ``ServarrLifecycle.probe_has_indexers`` and
``ensure_indexers`` — the lifecycle-method port of the legacy
``push_indexers`` job handler (ADR-0005 Phase 3 follow-on to the
*-jellyfin-notifier proof-of-pattern).

Three families of behavior:

  * Unsupported services (prowlarr, readarr) short-circuit to ok /
    success — those *arrs don't have a ``has-indexers`` promise.
    The lifecycle methods exist on every ServarrLifecycle (since
    one class covers all five *arrs) but become no-ops for those
    service ids.
  * Probe maps the *arr's indexer list to the tri-state ProbeResult
    (ok=non-empty, failed=empty, unknown=can't reach / no key).
  * Ensurer is idempotent (skip Prowlarr trigger when probe says ok),
    transient-failure when prerequisites aren't met (no Prowlarr
    key, no arr key — orchestrator retries on next tick),
    permanent-failure on 4xx from Prowlarr's command endpoint.

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


_PROWLARR_KEY = "prowlarr-test-key-1234567890"
_ARR_KEY = "arr-test-key-abcdef"


@pytest.fixture(autouse=True)
def _clear_envs():
    import os as _os
    yield
    for var in (
        "SONARR_API_KEY", "RADARR_API_KEY", "LIDARR_API_KEY",
        "READARR_API_KEY", "PROWLARR_API_KEY",
    ):
        _os.environ.pop(var, None)


def _ctx(
    service_id: str = "sonarr",
    *, prowlarr_key: str = _PROWLARR_KEY, arr_key: str = _ARR_KEY,
) -> OrchestrationContext:
    """Build an ``OrchestrationContext`` pre-populated with the
    Prowlarr + arr api keys in ``secrets``."""
    secrets: dict[str, str] = {}
    if prowlarr_key:
        secrets["PROWLARR_API_KEY"] = prowlarr_key
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
    """``prowlarr`` and ``readarr`` don't carry a ``has-indexers``
    promise (prowlarr IS the indexer source; readarr's schema
    differs and no promise references it). The methods exist on
    every ServarrLifecycle (since one class covers all five
    *arrs), but for those ids both methods short-circuit without
    any HTTP call."""

    @pytest.mark.parametrize("sid", ["prowlarr", "readarr"])
    def test_probe_returns_ok_with_unsupported_reason(
        self, sid: str,
    ) -> None:
        sl = ServarrLifecycle(sid)
        result = sl.probe_has_indexers(_ctx(sid))
        assert result.is_ok
        assert result.evidence.get("reason") == "unsupported_service"

    @pytest.mark.parametrize("sid", ["prowlarr", "readarr"])
    def test_ensure_returns_success_with_unsupported_reason(
        self, sid: str,
    ) -> None:
        sl = ServarrLifecycle(sid)
        outcome = sl.ensure_indexers(_ctx(sid))
        assert outcome.ok
        assert outcome.evidence.get("reason") == "unsupported_service"


# --- Probe behavior ---------------------------------------------------


class TestProbeHasIndexers:

    @patch("urllib.request.urlopen")
    def test_ok_when_indexers_present(
        self, mock_open: MagicMock,
    ) -> None:
        # Real *arrs return a list of indexer dicts. A non-empty list
        # is the only thing the promise asserts.
        body = json.dumps([
            {"id": 1, "name": "1337x", "implementation": "Cardigann"},
            {"id": 2, "name": "RARBG", "implementation": "Cardigann"},
        ]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("sonarr")
        result = sl.probe_has_indexers(_ctx("sonarr"))
        assert result.is_ok
        assert result.evidence.get("indexer_count") == 2

    @patch("urllib.request.urlopen")
    def test_failed_when_indexer_list_empty(
        self, mock_open: MagicMock,
    ) -> None:
        # Empty list → no indexers → failed (orchestrator will
        # dispatch ensure_indexers next).
        mock_open.return_value = _http_response(b"[]")

        sl = ServarrLifecycle("sonarr")
        result = sl.probe_has_indexers(_ctx("sonarr"))
        assert result.status == "failed"
        assert result.evidence.get("indexer_count") == 0

    @patch("urllib.request.urlopen")
    def test_ok_for_radarr_when_indexers_present(
        self, mock_open: MagicMock,
    ) -> None:
        # Radarr's indexer shape is identical to sonarr's — both serve
        # ``/api/v3/indexer``. Verify the lifecycle method works
        # equally for both.
        body = json.dumps([
            {"id": 99, "name": "Some Indexer"},
        ]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("radarr")
        result = sl.probe_has_indexers(_ctx("radarr"))
        assert result.is_ok
        assert result.evidence.get("indexer_count") == 1

    @patch("urllib.request.urlopen")
    def test_unknown_when_arr_unreachable(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.side_effect = urllib.error.URLError("dns")

        sl = ServarrLifecycle("sonarr")
        result = sl.probe_has_indexers(_ctx("sonarr"))
        assert result.status == "unknown"

    def test_unknown_when_no_arr_api_key(self) -> None:
        # No api key in secrets, no env var, no config.xml — probe
        # returns unknown (orchestrator retries after probe_has_api_key
        # reaches ok).
        sl = ServarrLifecycle("sonarr")
        result = sl.probe_has_indexers(_ctx("sonarr", arr_key=""))
        assert result.status == "unknown"
        assert "api key" in result.detail.lower()

    def test_unknown_when_no_host_in_config(self) -> None:
        # Mis-configured service entry: no host. Probe returns
        # unknown rather than failed — the contract YAML likely
        # had a typo or hadn't loaded yet.
        ctx = OrchestrationContext(
            service_id="sonarr",
            config={"port": 8989},
            secrets={"SONARR_API_KEY": "k"},
            now=lambda: 0.0,
        )
        sl = ServarrLifecycle("sonarr")
        result = sl.probe_has_indexers(ctx)
        assert result.status == "unknown"


# --- Ensurer behavior -------------------------------------------------


class TestEnsureIndexers:

    @patch("urllib.request.urlopen")
    def test_idempotent_when_indexers_already_present(
        self, mock_open: MagicMock,
    ) -> None:
        # GET returns a populated list — short-circuit, no Prowlarr
        # trigger fires.
        body = json.dumps([
            {"id": 1, "name": "1337x"},
        ]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_indexers(_ctx("sonarr"))
        assert outcome.ok
        assert outcome.evidence.get("reason") == "already_configured"
        assert outcome.evidence.get("indexer_count") == 1
        # Exactly one urlopen call (the GET); no POST.
        assert mock_open.call_count == 1

    @patch("urllib.request.urlopen")
    def test_triggers_prowlarr_sync_when_indexers_missing(
        self, mock_open: MagicMock,
    ) -> None:
        # First call (GET indexers) returns empty list. Second call
        # (POST to Prowlarr) is the ApplicationIndexerSync trigger.
        responses = [_http_response(b"[]"), _http_response(b"", status=201)]
        mock_open.side_effect = responses

        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_indexers(_ctx("sonarr"))
        assert outcome.ok
        assert mock_open.call_count == 2

        post_call = mock_open.call_args_list[1]
        req = post_call.args[0]
        assert req.get_method() == "POST"
        # Verify the POST hits Prowlarr's command endpoint, not the
        # *arr's endpoint.
        assert "/app/prowlarr/api/v1/command" in req.full_url
        body = json.loads(req.data.decode())
        # ``forceSync=true`` is required to push to apps Prowlarr's
        # sync state thinks are already in sync — same flag the
        # legacy handler uses.
        assert body["name"] == "ApplicationIndexerSync"
        assert body["forceSync"] is True
        # Outcome carries enough evidence to identify the per-*arr
        # invocation in run-history.
        assert outcome.evidence.get("service_id") == "sonarr"
        assert outcome.evidence.get("command") == "ApplicationIndexerSync"

    @patch("urllib.request.urlopen")
    def test_triggers_prowlarr_sync_for_radarr(
        self, mock_open: MagicMock,
    ) -> None:
        # Radarr-side ensurer also reaches Prowlarr's command
        # endpoint — Prowlarr's ApplicationIndexerSync fans out to
        # every registered app. ``service_id`` in the evidence
        # makes the per-*arr call traceable.
        responses = [_http_response(b"[]"), _http_response(b"", status=201)]
        mock_open.side_effect = responses
        sl = ServarrLifecycle("radarr")
        outcome = sl.ensure_indexers(_ctx("radarr"))
        assert outcome.ok
        post_call = mock_open.call_args_list[1]
        req = post_call.args[0]
        assert "/app/prowlarr/api/v1/command" in req.full_url
        assert outcome.evidence.get("service_id") == "radarr"

    def test_transient_failure_when_no_prowlarr_key(self) -> None:
        # No Prowlarr key + arr indexer list is empty would normally
        # try to trigger sync. Without the key, the ensurer returns
        # transient failure — orchestrator retries after Prowlarr's
        # own probe_has_api_key reaches ok.
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = _http_response(b"[]")
            sl = ServarrLifecycle("sonarr")
            outcome = sl.ensure_indexers(_ctx("sonarr", prowlarr_key=""))
        assert not outcome.ok
        assert outcome.transient is True
        assert "PROWLARR_API_KEY" in outcome.error

    def test_transient_failure_when_no_arr_key(self) -> None:
        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_indexers(_ctx("sonarr", arr_key=""))
        assert not outcome.ok
        assert outcome.transient is True
        assert "api key" in outcome.error.lower()

    @patch("urllib.request.urlopen")
    def test_permanent_failure_on_prowlarr_4xx(
        self, mock_open: MagicMock,
    ) -> None:
        # GET ok-but-empty, POST returns 400 from Prowlarr — payload-
        # level problem with the command, not transient.
        responses = [
            _http_response(b"[]"),
            urllib.error.HTTPError(
                "http://prowlarr/cmd", 400, "Bad", {}, None,
            ),
        ]
        mock_open.side_effect = responses

        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_indexers(_ctx("sonarr"))
        assert not outcome.ok
        assert outcome.transient is False
        assert outcome.evidence.get("http_status") == 400

    @patch("urllib.request.urlopen")
    def test_transient_failure_when_arr_unreachable(
        self, mock_open: MagicMock,
    ) -> None:
        # GET on *arr indexer list fails — the ensurer can't make a
        # "skip if already configured" decision. Surface as
        # transient (orchestrator retries).
        mock_open.side_effect = urllib.error.URLError("dns")

        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_indexers(_ctx("sonarr"))
        assert not outcome.ok
        assert outcome.transient is True

    @patch("urllib.request.urlopen")
    def test_transient_failure_when_prowlarr_unreachable(
        self, mock_open: MagicMock,
    ) -> None:
        # GET on *arr list ok-but-empty, POST to Prowlarr times out —
        # Prowlarr is warming up. Surface as transient.
        responses = [
            _http_response(b"[]"),
            urllib.error.URLError("dns"),
        ]
        mock_open.side_effect = responses

        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_indexers(_ctx("sonarr"))
        assert not outcome.ok
        assert outcome.transient is True
