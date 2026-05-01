"""Tests for ``BazarrLifecycle`` — ADR-0003 Phase 3.

Bazarr is structurally Sab/*arr-shaped but with YAML config
(``bazarr/config/config.yaml`` ``apikey: <value>`` line). Tests focus
on the YAML-format specifics + the "wait for the file" mint.
"""

from __future__ import annotations

import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from media_stack.adapters.bazarr.lifecycle import BazarrLifecycle
from media_stack.domain.services import (
    OrchestrationContext,
    ServiceLifecycle,
)


def _ctx(**overrides) -> OrchestrationContext:
    cfg = {
        "host": "bazarr",
        "port": 6767,
        "scheme": "http",
        "health_path": "/api/system/status",
        "api_key_env": "BAZARR_API_KEY",
        "api_key_config": "bazarr/config/config.yaml",
        "api_key_format": "yaml",
        "config_root": "/srv-config",
    }
    cfg.update(overrides.pop("config", {}))
    return OrchestrationContext(
        service_id="bazarr",
        config=cfg,
        secrets=overrides.pop("secrets", {}),
        now=overrides.pop("now", lambda: 1700000000.0),
        **overrides,
    )


def test_isinstance() -> None:
    assert isinstance(BazarrLifecycle(), ServiceLifecycle)


@patch("urllib.request.urlopen")
def test_probe_running_ok(mock_open: MagicMock) -> None:
    resp = MagicMock()
    resp.status = 200
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda *_: None
    mock_open.return_value = resp
    r = BazarrLifecycle().probe_running(_ctx())
    assert r.is_ok


@patch("urllib.request.urlopen")
def test_probe_running_unknown_on_dns(mock_open: MagicMock) -> None:
    mock_open.side_effect = urllib.error.URLError("Name resolution failed")
    r = BazarrLifecycle().probe_running(_ctx())
    assert r.status == "unknown"


def test_discover_env_short_circuit(monkeypatch) -> None:
    monkeypatch.setenv("BAZARR_API_KEY", "from-env")
    assert BazarrLifecycle().discover_api_key(_ctx()) == "from-env"


def test_discover_reads_yaml_apikey_line(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.delenv("BAZARR_API_KEY", raising=False)
    cfg = tmp_path / "bazarr" / "config" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "general:\n"
        "  ip: 0.0.0.0\n"
        "auth:\n"
        "  type: form\n"
        "apikey: real-bazarr-key-from-yaml\n",
        encoding="utf-8",
    )
    result = BazarrLifecycle().discover_api_key(
        _ctx(config={"config_root": str(tmp_path)}),
    )
    assert result == "real-bazarr-key-from-yaml"


def test_mint_idempotent(monkeypatch) -> None:
    monkeypatch.setenv("BAZARR_API_KEY", "exists")
    outcome = BazarrLifecycle().mint_api_key(_ctx())
    assert outcome.ok
    assert outcome.attempts == 0


def test_mint_transient_when_yaml_not_yet_generated(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.delenv("BAZARR_API_KEY", raising=False)
    outcome = BazarrLifecycle().mint_api_key(
        _ctx(config={"config_root": str(tmp_path)}),
    )
    assert not outcome.ok
    assert outcome.transient is True


def test_mint_non_transient_when_yaml_present_no_apikey(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.delenv("BAZARR_API_KEY", raising=False)
    cfg = tmp_path / "bazarr" / "config" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("general:\n  ip: 0.0.0.0\n", encoding="utf-8")
    outcome = BazarrLifecycle().mint_api_key(
        _ctx(config={"config_root": str(tmp_path)}),
    )
    assert not outcome.ok
    assert outcome.transient is False


def test_persist_writes_env(monkeypatch) -> None:
    monkeypatch.delenv("BAZARR_API_KEY", raising=False)
    with patch(
        "media_stack.services.apps.core.job_adapters._persist_preflight_keys_to_secret_safe",
        return_value={"status": "ok"},
    ), patch(
        "media_stack.services.apps.core.job_adapters._stub_state",
        return_value=object(),
    ):
        outcome = BazarrLifecycle().persist_api_key("k", _ctx())
    import os
    assert outcome.ok
    assert os.environ["BAZARR_API_KEY"] == "k"
