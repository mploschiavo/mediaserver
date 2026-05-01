"""Tests for ``JellyseerrLifecycle`` — ADR-0003 Phase 3.

Jellyseerr stores its API key at ``main.apiKey`` in
``settings.json``. Tests pin the JSON-format read path + the
"wait for the file" mint.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from media_stack.adapters.jellyseerr.lifecycle import JellyseerrLifecycle
from media_stack.domain.services import (
    OrchestrationContext,
    ServiceLifecycle,
)


def _ctx(**overrides) -> OrchestrationContext:
    cfg = {
        "host": "jellyseerr",
        "port": 5055,
        "scheme": "http",
        "health_path": "/api/v1/status",
        "api_key_env": "JELLYSEERR_API_KEY",
        "api_key_config": "jellyseerr/settings.json",
        "api_key_format": "json",
        "config_root": "/srv-config",
    }
    cfg.update(overrides.pop("config", {}))
    return OrchestrationContext(
        service_id="jellyseerr",
        config=cfg,
        secrets=overrides.pop("secrets", {}),
        now=overrides.pop("now", lambda: 1700000000.0),
        **overrides,
    )


def test_isinstance() -> None:
    assert isinstance(JellyseerrLifecycle(), ServiceLifecycle)


@patch("urllib.request.urlopen")
def test_probe_running_ok(mock_open: MagicMock) -> None:
    resp = MagicMock()
    resp.status = 200
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda *_: None
    mock_open.return_value = resp
    r = JellyseerrLifecycle().probe_running(_ctx())
    assert r.is_ok


def test_discover_reads_main_apikey_from_settings_json(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.delenv("JELLYSEERR_API_KEY", raising=False)
    settings = tmp_path / "jellyseerr" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps({"main": {"apiKey": "real-jellyseerr-key", "port": 5055}}),
        encoding="utf-8",
    )
    result = JellyseerrLifecycle().discover_api_key(
        _ctx(config={"config_root": str(tmp_path)}),
    )
    assert result == "real-jellyseerr-key"


def test_mint_idempotent(monkeypatch) -> None:
    monkeypatch.setenv("JELLYSEERR_API_KEY", "exists")
    outcome = JellyseerrLifecycle().mint_api_key(_ctx())
    assert outcome.ok
    assert outcome.attempts == 0


def test_mint_transient_when_settings_not_yet_generated(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.delenv("JELLYSEERR_API_KEY", raising=False)
    outcome = JellyseerrLifecycle().mint_api_key(
        _ctx(config={"config_root": str(tmp_path)}),
    )
    assert not outcome.ok
    assert outcome.transient is True


def test_mint_non_transient_when_settings_present_no_apikey(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.delenv("JELLYSEERR_API_KEY", raising=False)
    settings = tmp_path / "jellyseerr" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps({"main": {"port": 5055}}),  # no apiKey
        encoding="utf-8",
    )
    outcome = JellyseerrLifecycle().mint_api_key(
        _ctx(config={"config_root": str(tmp_path)}),
    )
    assert not outcome.ok
    assert outcome.transient is False
