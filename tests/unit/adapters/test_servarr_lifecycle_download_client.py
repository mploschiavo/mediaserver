"""Tests for ``ServarrLifecycle.probe_download_client`` and
``ensure_download_client`` — the lifecycle-method port of the legacy
``ensure_arr_download_client`` job handler (ADR-0005 Phase 5b — the
deferred 9th wirer).

Three families of behavior:

  * Unsupported services (prowlarr) short-circuit to ok / success —
    Prowlarr doesn't have a download client. The lifecycle methods
    exist on every ServarrLifecycle (since one class covers all five
    *arrs) but become no-ops for that service id.
  * Probe maps the *arr's download-client list to the tri-state
    ProbeResult (ok=qBit present + enabled + correct category,
    failed=qBit missing or category drifted, unknown=can't reach /
    no key).
  * Ensurer is idempotent (skip POST/PUT when probe says ok),
    transient-failure when prerequisites aren't met (no arr key),
    permanent-failure on 4xx from the *arr's API, transient-failure
    on URLError / OSError (network).

No real HTTP — urllib is mocked. The probe + ensurer parse JSON
shapes that match what real *arrs return, so the assertion logic
is exercised against representative payloads.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from media_stack.adapters.servarr.download_client_wiring import (
    DownloadClientWirer,
)
from media_stack.adapters.servarr.lifecycle import ServarrLifecycle
from media_stack.domain.services import OrchestrationContext


_ARR_KEY = "arr-test-key-abcdef"


@pytest.fixture(autouse=True)
def _clear_envs():
    import os as _os
    yield
    for var in (
        "SONARR_API_KEY", "RADARR_API_KEY", "LIDARR_API_KEY",
        "READARR_API_KEY",
    ):
        _os.environ.pop(var, None)


def _ctx(
    service_id: str = "sonarr",
    *, arr_key: str = _ARR_KEY,
) -> OrchestrationContext:
    """Build an ``OrchestrationContext`` pre-populated with the arr
    api key in ``secrets``."""
    secrets: dict[str, str] = {}
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


def _qbit_entry(
    *,
    cat_field: str,
    cat_value: str,
    enabled: bool = True,
    entry_id: int = 7,
    extra_fields: list[dict] | None = None,
) -> dict:
    """Build a representative *arr download-client list entry for
    qBittorrent. Only the fields the probe asserts against need to
    be realistic."""
    fields = list(extra_fields or [])
    fields.append({"name": cat_field, "value": cat_value})
    return {
        "id": entry_id,
        "name": "qBittorrent",
        "implementation": "QBittorrent",
        "enable": enabled,
        "fields": fields,
    }


# --- Unsupported-service short-circuit --------------------------------


class TestUnsupportedServicesShortCircuit:
    """``prowlarr`` doesn't have a download-client concept. The
    methods exist on every ServarrLifecycle (since one class covers
    all five *arrs), but for that id both methods short-circuit
    without any HTTP call."""

    def test_probe_returns_ok_for_prowlarr(self) -> None:
        sl = ServarrLifecycle("prowlarr")
        result = sl.probe_download_client(_ctx("prowlarr"))
        assert result.is_ok
        assert result.evidence.get("reason") == "unsupported_service"

    def test_ensure_returns_success_for_prowlarr(self) -> None:
        sl = ServarrLifecycle("prowlarr")
        outcome = sl.ensure_download_client(_ctx("prowlarr"))
        assert outcome.ok
        assert outcome.evidence.get("reason") == "unsupported_service"


# --- Probe behavior ---------------------------------------------------


class TestProbeDownloadClient:

    @patch("urllib.request.urlopen")
    def test_ok_when_qbit_configured_with_correct_category(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps([
            _qbit_entry(cat_field="tvCategory", cat_value="tv"),
        ]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("sonarr")
        result = sl.probe_download_client(_ctx("sonarr"))
        assert result.is_ok
        assert result.evidence.get("client_count") == 1

    @patch("urllib.request.urlopen")
    def test_ok_for_radarr_with_movies_category(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps([
            _qbit_entry(
                cat_field="movieCategory", cat_value="movies",
            ),
        ]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("radarr")
        result = sl.probe_download_client(_ctx("radarr"))
        assert result.is_ok

    @patch("urllib.request.urlopen")
    def test_failed_when_qbit_missing(
        self, mock_open: MagicMock,
    ) -> None:
        # *arr has download clients but no QBittorrent entry — the
        # promise asserts qBit specifically.
        body = json.dumps([
            {
                "id": 1, "name": "Other",
                "implementation": "Transmission",
                "enable": True, "fields": [],
            },
        ]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("sonarr")
        result = sl.probe_download_client(_ctx("sonarr"))
        assert result.status == "failed"
        assert "no qBittorrent" in result.detail

    @patch("urllib.request.urlopen")
    def test_failed_when_category_wrong(
        self, mock_open: MagicMock,
    ) -> None:
        # qBit present + enabled, but category drifted to ``shows``.
        # The promise's per-arr category map is "tv" for Sonarr, so
        # this is a drift detection — surface as failed (NOT
        # unknown) so auto-heal dispatches the ensurer.
        body = json.dumps([
            _qbit_entry(cat_field="tvCategory", cat_value="shows"),
        ]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("sonarr")
        result = sl.probe_download_client(_ctx("sonarr"))
        assert result.status == "failed"
        assert "drifted" in result.detail
        assert result.evidence.get("qbit_id") == 7

    @patch("urllib.request.urlopen")
    def test_failed_when_qbit_disabled(
        self, mock_open: MagicMock,
    ) -> None:
        # qBit present + correct category but ``enable: False``.
        # The legacy handler's match logic also requires
        # ``enable=True``.
        body = json.dumps([
            _qbit_entry(
                cat_field="tvCategory", cat_value="tv", enabled=False,
            ),
        ]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("sonarr")
        result = sl.probe_download_client(_ctx("sonarr"))
        assert result.status == "failed"
        assert result.evidence.get("qbit_enabled") is False

    @patch("urllib.request.urlopen")
    def test_unknown_when_arr_unreachable(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.side_effect = urllib.error.URLError("dns")

        sl = ServarrLifecycle("sonarr")
        result = sl.probe_download_client(_ctx("sonarr"))
        assert result.status == "unknown"

    def test_unknown_when_no_arr_api_key(self) -> None:
        sl = ServarrLifecycle("sonarr")
        result = sl.probe_download_client(_ctx("sonarr", arr_key=""))
        assert result.status == "unknown"
        assert "api key" in result.detail.lower()

    def test_unknown_when_no_host_in_config(self) -> None:
        ctx = OrchestrationContext(
            service_id="sonarr",
            config={"port": 8989},
            secrets={"SONARR_API_KEY": "k"},
            now=lambda: 0.0,
        )
        sl = ServarrLifecycle("sonarr")
        result = sl.probe_download_client(ctx)
        assert result.status == "unknown"


# --- Ensurer behavior -------------------------------------------------


class TestEnsureDownloadClient:

    @patch("urllib.request.urlopen")
    def test_idempotent_when_qbit_already_configured(
        self, mock_open: MagicMock,
    ) -> None:
        # GET returns a populated list with a healthy qBit entry —
        # ensurer short-circuits, no POST/PUT fires.
        body = json.dumps([
            _qbit_entry(cat_field="tvCategory", cat_value="tv"),
        ]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_download_client(_ctx("sonarr"))
        assert outcome.ok
        assert outcome.evidence.get("reason") == "already_configured"
        # Exactly one urlopen call (the GET); no POST.
        assert mock_open.call_count == 1

    @patch("urllib.request.urlopen")
    def test_posts_when_qbit_missing(
        self, mock_open: MagicMock,
    ) -> None:
        # GET returns empty list. Ensurer POSTs the canonical qBit
        # payload.
        responses = [_http_response(b"[]"), _http_response(b"", status=201)]
        mock_open.side_effect = responses

        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_download_client(_ctx("sonarr"))
        assert outcome.ok
        assert mock_open.call_count == 2

        post_call = mock_open.call_args_list[1]
        req = post_call.args[0]
        assert req.get_method() == "POST"
        assert "/app/sonarr/api/v3/downloadclient" in req.full_url
        body = json.loads(req.data.decode())
        assert body["implementation"] == "QBittorrent"
        assert body["configContract"] == "QBittorrentSettings"
        assert body["enable"] is True
        # The category field MUST be ``tvCategory: tv`` for Sonarr —
        # legacy handler's table.
        cat_fields = [
            f for f in body["fields"] if f["name"] == "tvCategory"
        ]
        assert len(cat_fields) == 1
        assert cat_fields[0]["value"] == "tv"
        assert outcome.evidence.get("operation") == "created"

    @patch("urllib.request.urlopen")
    def test_puts_when_qbit_present_but_drifted(
        self, mock_open: MagicMock,
    ) -> None:
        # GET returns a qBit entry with the wrong category — ensurer
        # PUTs to /<id> with the desired payload.
        existing_body = json.dumps([
            _qbit_entry(
                cat_field="tvCategory", cat_value="shows",
                entry_id=42,
            ),
        ]).encode()
        responses = [
            _http_response(existing_body),
            _http_response(b"", status=200),
        ]
        mock_open.side_effect = responses

        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_download_client(_ctx("sonarr"))
        assert outcome.ok
        put_call = mock_open.call_args_list[1]
        req = put_call.args[0]
        assert req.get_method() == "PUT"
        assert "/app/sonarr/api/v3/downloadclient/42" in req.full_url
        body = json.loads(req.data.decode())
        assert body.get("id") == 42
        assert outcome.evidence.get("operation") == "updated"

    @patch("urllib.request.urlopen")
    def test_radarr_uses_movie_category(
        self, mock_open: MagicMock,
    ) -> None:
        # Radarr's category field is ``movieCategory: movies`` — the
        # wirer's per-arr table covers it.
        responses = [_http_response(b"[]"), _http_response(b"", status=201)]
        mock_open.side_effect = responses
        sl = ServarrLifecycle("radarr")
        outcome = sl.ensure_download_client(_ctx("radarr"))
        assert outcome.ok
        post_call = mock_open.call_args_list[1]
        body = json.loads(post_call.args[0].data.decode())
        assert "/app/radarr/api/v3/downloadclient" in (
            post_call.args[0].full_url
        )
        cat_fields = [
            f for f in body["fields"] if f["name"] == "movieCategory"
        ]
        assert len(cat_fields) == 1
        assert cat_fields[0]["value"] == "movies"
        assert outcome.evidence.get("service_id") == "radarr"

    def test_transient_failure_when_no_arr_key(self) -> None:
        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_download_client(_ctx("sonarr", arr_key=""))
        assert not outcome.ok
        assert outcome.transient is True
        assert "api key" in outcome.error.lower()

    @patch("urllib.request.urlopen")
    def test_permanent_failure_on_arr_4xx(
        self, mock_open: MagicMock,
    ) -> None:
        # GET ok-but-empty, POST returns 400 from the *arr — payload-
        # level problem with the request, not transient.
        responses = [
            _http_response(b"[]"),
            urllib.error.HTTPError(
                "http://sonarr/cmd", 400, "Bad", {}, None,
            ),
        ]
        mock_open.side_effect = responses

        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_download_client(_ctx("sonarr"))
        assert not outcome.ok
        assert outcome.transient is False
        assert outcome.evidence.get("http_status") == 400

    @patch("urllib.request.urlopen")
    def test_transient_failure_when_arr_unreachable(
        self, mock_open: MagicMock,
    ) -> None:
        # GET on *arr download-client list fails — the ensurer can't
        # make a "skip if already configured" decision. Surface as
        # transient (orchestrator retries).
        mock_open.side_effect = urllib.error.URLError("dns")

        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_download_client(_ctx("sonarr"))
        assert not outcome.ok
        assert outcome.transient is True

    @patch("urllib.request.urlopen")
    def test_transient_failure_on_post_url_error(
        self, mock_open: MagicMock,
    ) -> None:
        # GET ok-but-empty, POST URLError — *arr is warming up.
        # Surface as transient.
        responses = [
            _http_response(b"[]"),
            urllib.error.URLError("dns"),
        ]
        mock_open.side_effect = responses

        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_download_client(_ctx("sonarr"))
        assert not outcome.ok
        assert outcome.transient is True


# --- Constructor-injection coverage -----------------------------------


class TestQbitCredentialInjection:
    """The wirer's qBit username / password resolve through the
    ``LifecycleWirerBase._discover_secret(ctx, env_var)`` path:
    ctx.secrets first, then ``os.environ``, then upstream factory
    defaults. Tests inject via ctx.secrets so the discovery path is
    identical to every other wirer in the family."""

    @patch("urllib.request.urlopen")
    def test_ctx_secrets_creds_appear_in_payload(
        self, mock_open: MagicMock,
    ) -> None:
        responses = [_http_response(b"[]"), _http_response(b"", status=201)]
        mock_open.side_effect = responses

        # Inject via the ctx.secrets discovery channel — same shape
        # ``ServarrLifecycle`` uses for arr api keys.
        ctx_base = _ctx("sonarr")
        merged_secrets = dict(ctx_base.secrets)
        merged_secrets["QBIT_USERNAME"] = "injected-user"
        merged_secrets["QBIT_PASSWORD"] = "injected-pass"
        ctx = OrchestrationContext(
            service_id=ctx_base.service_id,
            config=ctx_base.config,
            secrets=merged_secrets,
            now=ctx_base.now,
        )

        wirer = DownloadClientWirer()
        outcome = wirer.ensure("sonarr", _ARR_KEY, ctx)
        assert outcome.ok

        post_call = mock_open.call_args_list[1]
        body = json.loads(post_call.args[0].data.decode())
        username_field = next(
            f for f in body["fields"] if f["name"] == "username"
        )
        password_field = next(
            f for f in body["fields"] if f["name"] == "password"
        )
        assert username_field["value"] == "injected-user"
        assert password_field["value"] == "injected-pass"
