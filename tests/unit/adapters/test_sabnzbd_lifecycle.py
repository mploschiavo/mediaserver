"""Tests for ``SabnzbdLifecycle`` — ADR-0003 Phase 3.

SABnzbd's lifecycle is structurally close to ServarrLifecycle but
with INI rather than XML config. The test set pins the same probe
tri-state, env-short-circuit discover, and "wait for the file" mint
shape that the *arr family uses.
"""

from __future__ import annotations

import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from media_stack.adapters.sabnzbd.lifecycle import SabnzbdLifecycle
from media_stack.domain.services import (
    OrchestrationContext,
    ServiceLifecycle,
)


def _ctx(**overrides) -> OrchestrationContext:
    cfg = {
        "host": "sabnzbd",
        "port": 8080,
        "scheme": "http",
        "health_path": "/sabnzbd/api?mode=version",
        "api_key_env": "SABNZBD_API_KEY",
        "api_key_config": "sabnzbd/sabnzbd.ini",
        "api_key_format": "ini",
        "config_root": "/srv-config",
    }
    cfg.update(overrides.pop("config", {}))
    return OrchestrationContext(
        service_id="sabnzbd",
        config=cfg,
        secrets=overrides.pop("secrets", {}),
        now=overrides.pop("now", lambda: 1700000000.0),
        **overrides,
    )


class TestProtocolConformance:
    def test_isinstance(self) -> None:
        assert isinstance(SabnzbdLifecycle(), ServiceLifecycle)


class TestProbeRunning:
    @patch("urllib.request.urlopen")
    def test_ok_on_200(self, mock_open: MagicMock) -> None:
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *_: None
        mock_open.return_value = resp
        r = SabnzbdLifecycle().probe_running(_ctx())
        assert r.is_ok

    @patch("urllib.request.urlopen")
    def test_unknown_on_dns(self, mock_open: MagicMock) -> None:
        mock_open.side_effect = urllib.error.URLError("Name resolution failed")
        r = SabnzbdLifecycle().probe_running(_ctx())
        assert r.status == "unknown"


class TestDiscoverApiKey:
    def test_env_short_circuits_file_read(self, monkeypatch) -> None:
        monkeypatch.setenv("SABNZBD_API_KEY", "from-env-key")
        assert SabnzbdLifecycle().discover_api_key(_ctx()) == "from-env-key"

    def test_returns_none_when_ini_missing(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("SABNZBD_API_KEY", raising=False)
        result = SabnzbdLifecycle().discover_api_key(
            _ctx(config={"config_root": str(tmp_path)}),
        )
        assert result is None

    def test_reads_ini_when_present(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("SABNZBD_API_KEY", raising=False)
        ini = tmp_path / "sabnzbd" / "sabnzbd.ini"
        ini.parent.mkdir()
        ini.write_text(
            "[misc]\n"
            "host = 0.0.0.0\n"
            "api_key = real-key-from-ini\n"
            "host_whitelist = sabnzbd\n",
            encoding="utf-8",
        )
        result = SabnzbdLifecycle().discover_api_key(
            _ctx(config={"config_root": str(tmp_path)}),
        )
        assert result == "real-key-from-ini"


class TestProbeHasApiKey:
    def test_ok_when_env(self, monkeypatch) -> None:
        monkeypatch.setenv("SABNZBD_API_KEY", "k")
        r = SabnzbdLifecycle().probe_has_api_key(_ctx())
        assert r.is_ok

    def test_failed_when_neither_env_nor_file(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("SABNZBD_API_KEY", raising=False)
        r = SabnzbdLifecycle().probe_has_api_key(
            _ctx(config={"config_root": str(tmp_path)}),
        )
        assert r.status == "failed"


class TestMintApiKey:
    def test_idempotent(self, monkeypatch) -> None:
        monkeypatch.setenv("SABNZBD_API_KEY", "already-set")
        outcome = SabnzbdLifecycle().mint_api_key(_ctx())
        assert outcome.ok
        assert outcome.value == "already-set"
        assert outcome.attempts == 0

    def test_transient_when_ini_not_yet_generated(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        # Service warming up; no sabnzbd.ini yet. Auto-heal retries.
        monkeypatch.delenv("SABNZBD_API_KEY", raising=False)
        outcome = SabnzbdLifecycle().mint_api_key(
            _ctx(config={"config_root": str(tmp_path)}),
        )
        assert not outcome.ok
        assert outcome.transient is True
        assert "not yet generated" in outcome.error

    def test_non_transient_when_ini_present_but_no_key(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        # File there, [misc] section there, but no api_key= line. SAB
        # should write it on first start; if not, operator action.
        monkeypatch.delenv("SABNZBD_API_KEY", raising=False)
        ini = tmp_path / "sabnzbd" / "sabnzbd.ini"
        ini.parent.mkdir()
        ini.write_text("[misc]\nhost = 0.0.0.0\n", encoding="utf-8")
        outcome = SabnzbdLifecycle().mint_api_key(
            _ctx(config={"config_root": str(tmp_path)}),
        )
        assert not outcome.ok
        assert outcome.transient is False
        assert "missing" in outcome.error.lower()


class TestPersistApiKey:
    def test_refuses_empty(self) -> None:
        outcome = SabnzbdLifecycle().persist_api_key("", _ctx())
        assert not outcome.ok
        assert outcome.transient is False

    def test_writes_env(self, monkeypatch) -> None:
        monkeypatch.delenv("SABNZBD_API_KEY", raising=False)
        with patch(
            "media_stack.services.apps.core.job_adapters._persist_preflight_keys_to_secret_safe",
            return_value={"status": "ok"},
        ), patch(
            "media_stack.services.apps.core.job_adapters._stub_state",
            return_value=object(),
        ):
            outcome = SabnzbdLifecycle().persist_api_key("k", _ctx())
        import os
        assert outcome.ok
        assert os.environ["SABNZBD_API_KEY"] == "k"
