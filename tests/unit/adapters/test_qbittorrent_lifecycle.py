"""Tests for ``QbittorrentLifecycle`` — ADR-0003 Phase 3.

qBittorrent doesn't issue a static API key — the WebUI auth is
session-cookie via username/password. So in this adapter the "API
key" is the admin password. The test set pins:

  * Probe distinguishes 200 / 403 / non-2xx / network errors. Both
    200 and 403 mean "running" (403 = auth gate active = service is
    up). Other HTTP errors → failed; network → unknown.
  * ``probe_has_api_key`` is pure inspection — no login attempt
    (would risk rate-limiting and conflate two questions).
  * ``mint_api_key`` is HONEST about the operator-config gap. If the
    password env is missing, return ``Outcome.failure(transient=False,
    ...)``. Do NOT silently succeed — that's the
    ``ensure-qbittorrent-categories`` bug class we explicitly
    avoid here.
  * ``persist_api_key`` writes env + best-effort secret patch.
"""

from __future__ import annotations

import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from media_stack.adapters.qbittorrent.lifecycle import QbittorrentLifecycle
from media_stack.domain.services import (
    OrchestrationContext,
    ServiceLifecycle,
)


def _ctx(**overrides) -> OrchestrationContext:
    cfg = {
        "host": "qbittorrent",
        "port": 8080,
        "scheme": "http",
        "health_path": "/api/v2/app/version",
        "api_key_env": "QBITTORRENT_PASSWORD",
    }
    cfg.update(overrides.pop("config", {}))
    return OrchestrationContext(
        service_id="qbittorrent",
        config=cfg,
        secrets=overrides.pop("secrets", {}),
        now=overrides.pop("now", lambda: 1700000000.0),
        **overrides,
    )


class TestProtocolConformance:
    def test_isinstance(self) -> None:
        assert isinstance(QbittorrentLifecycle(), ServiceLifecycle)


class TestProbeRunning:
    @patch("urllib.request.urlopen")
    def test_ok_on_200(self, mock_open: MagicMock) -> None:
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *_: None
        mock_open.return_value = resp
        r = QbittorrentLifecycle().probe_running(_ctx())
        assert r.is_ok
        assert r.evidence["http_status"] == 200

    @patch("urllib.request.urlopen")
    def test_ok_on_403_via_response(self, mock_open: MagicMock) -> None:
        # Some qBit versions return 403 on /app/version without auth
        # but with a 200 response object (proxy/edge layer). Probe
        # MUST treat that as running too.
        resp = MagicMock()
        resp.status = 403
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *_: None
        mock_open.return_value = resp
        r = QbittorrentLifecycle().probe_running(_ctx())
        assert r.is_ok

    @patch("urllib.request.urlopen")
    def test_ok_on_403_via_httperror(self, mock_open: MagicMock) -> None:
        # urllib raises HTTPError for 4xx by default. 403 specifically
        # MUST be treated as "running, just unauthorized" — qBit is up
        # and gating us, that's success for "is the service running?".
        mock_open.side_effect = urllib.error.HTTPError(
            url="x", code=403, msg="Forbidden", hdrs=None, fp=None,
        )
        r = QbittorrentLifecycle().probe_running(_ctx())
        assert r.is_ok
        assert r.evidence["http_status"] == 403

    @patch("urllib.request.urlopen")
    def test_failed_on_other_4xx(self, mock_open: MagicMock) -> None:
        mock_open.side_effect = urllib.error.HTTPError(
            url="x", code=404, msg="Not Found", hdrs=None, fp=None,
        )
        r = QbittorrentLifecycle().probe_running(_ctx())
        assert r.status == "failed"

    @patch("urllib.request.urlopen")
    def test_unknown_on_network_error(self, mock_open: MagicMock) -> None:
        mock_open.side_effect = urllib.error.URLError("Name resolution failed")
        r = QbittorrentLifecycle().probe_running(_ctx())
        assert r.status == "unknown"


class TestDiscoverAndProbeKey:
    def test_discover_reads_env(self, monkeypatch) -> None:
        monkeypatch.setenv("QBITTORRENT_PASSWORD", "p4ssw0rd")
        assert QbittorrentLifecycle().discover_api_key(_ctx()) == "p4ssw0rd"

    def test_discover_reads_secrets_first(self, monkeypatch) -> None:
        monkeypatch.setenv("QBITTORRENT_PASSWORD", "from-env")
        result = QbittorrentLifecycle().discover_api_key(
            _ctx(secrets={"QBITTORRENT_PASSWORD": "from-secrets"}),
        )
        assert result == "from-secrets"

    def test_discover_returns_none_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("QBITTORRENT_PASSWORD", raising=False)
        assert QbittorrentLifecycle().discover_api_key(_ctx()) is None

    def test_probe_has_api_key_ok_when_present(self, monkeypatch) -> None:
        monkeypatch.setenv("QBITTORRENT_PASSWORD", "p")
        r = QbittorrentLifecycle().probe_has_api_key(_ctx())
        assert r.is_ok
        assert r.evidence["source"] == "env"

    def test_probe_has_api_key_failed_with_actionable_detail(
        self, monkeypatch,
    ) -> None:
        monkeypatch.delenv("QBITTORRENT_PASSWORD", raising=False)
        r = QbittorrentLifecycle().probe_has_api_key(_ctx())
        assert r.status == "failed"
        # Operator-actionable — names the env var to set
        assert "QBITTORRENT_PASSWORD" in r.detail


class TestMintApiKey:
    def test_idempotent_when_already_discoverable(self, monkeypatch) -> None:
        monkeypatch.setenv("QBITTORRENT_PASSWORD", "existing")
        outcome = QbittorrentLifecycle().mint_api_key(_ctx())
        assert outcome.ok
        assert outcome.value == "existing"
        assert outcome.attempts == 0

    def test_non_transient_failure_when_unset(self, monkeypatch) -> None:
        # The whole point: do NOT silently succeed when the operator
        # forgot to set the env. Surface the gap with transient=False
        # so the auto-heal cycle doesn't burn cycles retrying — the
        # ensure-qbittorrent-categories bug class lived precisely
        # here.
        monkeypatch.delenv("QBITTORRENT_PASSWORD", raising=False)
        outcome = QbittorrentLifecycle().mint_api_key(_ctx())
        assert not outcome.ok
        assert outcome.transient is False
        assert "operator must provide" in outcome.error.lower()


class TestPersistApiKey:
    def test_refuses_empty(self) -> None:
        outcome = QbittorrentLifecycle().persist_api_key("", _ctx())
        assert not outcome.ok
        assert outcome.transient is False

    def test_writes_env(self, monkeypatch) -> None:
        monkeypatch.delenv("QBITTORRENT_PASSWORD", raising=False)
        with patch(
            "media_stack.services.apps.core.job_adapters._persist_preflight_keys_to_secret_safe",
            return_value={"status": "ok"},
        ), patch(
            "media_stack.services.apps.core.job_adapters._stub_state",
            return_value=object(),
        ):
            outcome = QbittorrentLifecycle().persist_api_key("k", _ctx())
        import os
        assert outcome.ok
        assert os.environ["QBITTORRENT_PASSWORD"] == "k"

    def test_transient_failure_when_secret_patch_fails(
        self, monkeypatch,
    ) -> None:
        monkeypatch.delenv("QBITTORRENT_PASSWORD", raising=False)
        with patch(
            "media_stack.services.apps.core.job_adapters._persist_preflight_keys_to_secret_safe",
            side_effect=RuntimeError("unauthorized"),
        ), patch(
            "media_stack.services.apps.core.job_adapters._stub_state",
            return_value=object(),
        ):
            outcome = QbittorrentLifecycle().persist_api_key("k", _ctx())
        import os
        assert os.environ["QBITTORRENT_PASSWORD"] == "k"
        assert not outcome.ok
        assert outcome.transient is True
