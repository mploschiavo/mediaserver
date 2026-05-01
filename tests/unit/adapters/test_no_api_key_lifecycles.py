"""Tests for the shared ``NoApiKeyLifecycleBase`` + the five Phase 3c
adapters (Authelia, Authentik, Homepage, FlareSolverr, Envoy) and the
refactored ``MaintainerrLifecycle``.

The base provides the entire shape; each subclass just sets
``service_id`` and ``_default_health_path``. So the per-service tests
below are deliberately thin — they pin that the right class lives at
the right module path and that `service_id` / health URL match the
contract YAML. The base's behavior is tested once.
"""

from __future__ import annotations

import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from media_stack.adapters._lifecycle_base import NoApiKeyLifecycleBase
from media_stack.adapters.authelia.lifecycle import AutheliaLifecycle
from media_stack.adapters.authentik.lifecycle import AuthentikLifecycle
from media_stack.adapters.envoy.lifecycle import EnvoyLifecycle
from media_stack.adapters.flaresolverr.lifecycle import FlaresolverrLifecycle
from media_stack.adapters.homepage.lifecycle import HomepageLifecycle
from media_stack.adapters.maintainerr.lifecycle import MaintainerrLifecycle
from media_stack.domain.services import (
    OrchestrationContext,
    ServiceLifecycle,
)


# Each tuple: (LifecycleClass, expected service_id, expected default health path)
_PHASE_3C_SERVICES = [
    (AutheliaLifecycle, "authelia", "/api/health"),
    (AuthentikLifecycle, "authentik", "/-/health/live/"),
    (HomepageLifecycle, "homepage", "/"),
    (FlaresolverrLifecycle, "flaresolverr", "/"),
    (EnvoyLifecycle, "envoy", "/ready"),
    (MaintainerrLifecycle, "maintainerr", "/app/maintainerr/api/settings"),
]


def _ctx(svc_id: str, host: str, port: int, **overrides) -> OrchestrationContext:
    cfg = {"host": host, "port": port, "scheme": "http"}
    cfg.update(overrides.pop("config", {}))
    return OrchestrationContext(
        service_id=svc_id,
        config=cfg,
        now=lambda: 1700000000.0,
        **overrides,
    )


# ---------------------------------------------------------------------------
# NoApiKeyLifecycleBase behavior — tested once
# ---------------------------------------------------------------------------

class _StubLifecycle(NoApiKeyLifecycleBase):
    service_id = "stub"
    _default_health_path = "/health"


class TestNoApiKeyLifecycleBase:
    @patch("urllib.request.urlopen")
    def test_probe_running_ok(self, mock_open: MagicMock) -> None:
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *_: None
        mock_open.return_value = resp
        r = _StubLifecycle().probe_running(_ctx("stub", "host", 1234))
        assert r.is_ok
        assert r.evidence["url"] == "http://host:1234/health"

    @patch("urllib.request.urlopen")
    def test_probe_running_failed_on_non_200(
        self, mock_open: MagicMock,
    ) -> None:
        resp = MagicMock()
        resp.status = 503
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *_: None
        mock_open.return_value = resp
        r = _StubLifecycle().probe_running(_ctx("stub", "host", 1234))
        assert r.status == "failed"

    @patch("urllib.request.urlopen")
    def test_probe_running_unknown_on_network(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.side_effect = urllib.error.URLError("dns")
        r = _StubLifecycle().probe_running(_ctx("stub", "host", 1234))
        assert r.status == "unknown"

    def test_probe_running_failed_when_host_missing(self) -> None:
        ctx = OrchestrationContext(
            service_id="stub",
            config={"host": "", "port": None},
            now=lambda: 0.0,
        )
        r = _StubLifecycle().probe_running(ctx)
        assert r.status == "failed"
        assert "no host/port" in r.detail

    def test_probe_has_api_key_returns_ok_with_explanatory_detail(self) -> None:
        # The uniform contract — orchestrator never special-cases this.
        r = _StubLifecycle().probe_has_api_key(_ctx("stub", "host", 1234))
        assert r.is_ok
        assert "no api key concept" in r.detail.lower()

    def test_discover_returns_none(self) -> None:
        assert _StubLifecycle().discover_api_key(
            _ctx("stub", "host", 1234),
        ) is None

    def test_mint_returns_success_none(self) -> None:
        outcome = _StubLifecycle().mint_api_key(_ctx("stub", "host", 1234))
        assert outcome.ok
        assert outcome.value is None
        assert outcome.evidence["reason"] == "no_api_key_concept"

    def test_persist_returns_success_ignoring_input(self) -> None:
        outcome = _StubLifecycle().persist_api_key(
            "ignored", _ctx("stub", "host", 1234),
        )
        assert outcome.ok
        assert outcome.evidence["ignored_input"] is True

    def test_health_path_overridable_via_context(self) -> None:
        # Subclass default is /health, but contract YAML's value
        # wins — that's how operators tune per-deployment without
        # touching code.
        ctx = _ctx(
            "stub", "host", 1234,
            config={"health_path": "/custom"},
        )
        with patch("urllib.request.urlopen") as mock_open:
            resp = MagicMock()
            resp.status = 200
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda *_: None
            mock_open.return_value = resp
            r = _StubLifecycle().probe_running(ctx)
        assert r.evidence["url"] == "http://host:1234/custom"


# ---------------------------------------------------------------------------
# Per-service: each class is at the right module path, satisfies the
# Protocol, and carries the expected service_id + default health path.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cls, expected_id, expected_health_path", _PHASE_3C_SERVICES,
)
def test_per_service_isinstance_and_metadata(
    cls, expected_id: str, expected_health_path: str,
) -> None:
    impl = cls()
    assert isinstance(impl, ServiceLifecycle), (
        f"{cls.__name__} must satisfy ServiceLifecycle Protocol"
    )
    assert impl.service_id == expected_id
    assert impl._default_health_path == expected_health_path


@pytest.mark.parametrize("cls, _id, _path", _PHASE_3C_SERVICES)
def test_per_service_inherits_no_api_key_shape(
    cls, _id: str, _path: str,
) -> None:
    # The uniform contract: every no-API-key lifecycle reports the
    # same answers to the key-related Protocol methods. The
    # orchestrator can iterate without per-service if-statements.
    impl = cls()
    ctx = OrchestrationContext(service_id=impl.service_id, now=lambda: 0.0)
    assert impl.discover_api_key(ctx) is None
    assert impl.probe_has_api_key(ctx).is_ok
    mint_outcome = impl.mint_api_key(ctx)
    assert mint_outcome.ok and mint_outcome.value is None
    persist_outcome = impl.persist_api_key("anything", ctx)
    assert persist_outcome.ok
