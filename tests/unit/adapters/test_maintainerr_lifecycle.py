"""Tests for ``MaintainerrLifecycle`` — ADR-0003 Phase 3.

Maintainerr has no API key of its own — it's a consumer of other
services' keys. The lifecycle implements the "no api key concept"
shape per the ADR design: ``probe_has_api_key`` returns ok with an
explanatory detail; mint/discover/persist are inert.

These tests pin that uniform shape so the orchestrator can call
every lifecycle method on every service without per-service
if-statements.
"""

from __future__ import annotations

import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from media_stack.adapters.maintainerr.lifecycle import MaintainerrLifecycle
from media_stack.domain.services import (
    OrchestrationContext,
    ServiceLifecycle,
)


def _ctx() -> OrchestrationContext:
    return OrchestrationContext(
        service_id="maintainerr",
        config={
            "host": "maintainerr",
            "port": 6246,
            "scheme": "http",
            "health_path": "/app/maintainerr/api/settings",
        },
        now=lambda: 1700000000.0,
    )


def test_isinstance() -> None:
    assert isinstance(MaintainerrLifecycle(), ServiceLifecycle)


@patch("urllib.request.urlopen")
def test_probe_running_ok_real(mock_open: MagicMock) -> None:
    # Probe is real — the HTTP API is the operator's signal.
    resp = MagicMock()
    resp.status = 200
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda *_: None
    mock_open.return_value = resp
    r = MaintainerrLifecycle().probe_running(_ctx())
    assert r.is_ok


@patch("urllib.request.urlopen")
def test_probe_running_unknown_on_network(mock_open: MagicMock) -> None:
    mock_open.side_effect = urllib.error.URLError("dns")
    r = MaintainerrLifecycle().probe_running(_ctx())
    assert r.status == "unknown"


def test_probe_has_api_key_ok_with_explanatory_detail() -> None:
    # The orchestrator MUST be able to call probe_has_api_key on every
    # service without special-casing. For services that have no key
    # concept, the contract is "ok + a detail string explaining why".
    r = MaintainerrLifecycle().probe_has_api_key(_ctx())
    assert r.is_ok
    assert "no api key concept" in r.detail.lower()


def test_discover_returns_none() -> None:
    assert MaintainerrLifecycle().discover_api_key(_ctx()) is None


def test_mint_returns_success_none() -> None:
    # No key to mint — return success(None) so the orchestrator's
    # "did mint succeed?" check still passes.
    outcome = MaintainerrLifecycle().mint_api_key(_ctx())
    assert outcome.ok
    assert outcome.value is None
    assert outcome.evidence["reason"] == "no_api_key_concept"


def test_persist_returns_success_ignoring_input() -> None:
    # Persisting a non-empty key on a no-key-concept service is a
    # no-op success. The evidence reports the input was ignored so
    # operator logs are honest.
    outcome = MaintainerrLifecycle().persist_api_key("ignored-value", _ctx())
    assert outcome.ok
    assert outcome.evidence["reason"] == "no_api_key_concept"
    assert outcome.evidence["ignored_input"] is True
