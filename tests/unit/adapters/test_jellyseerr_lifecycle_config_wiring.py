"""Tests for ``JellyseerrLifecycle.{probe|ensure}_*`` config-wiring
methods — the lifecycle-method port of the legacy
``ensure_jellyseerr_oidc`` and ``configure_jellyseerr`` job handlers
(ADR-0005 Phase 3 cutover).

Three families of behavior:

  * OIDC: probe hits the LIVE ``/api/v1/settings/public`` endpoint
    and looks for an ``authelia`` provider; ensure idempotently
    mutates settings.json (oidcLogin + providers + applicationUrl
    + trustProxy) and best-effort restarts.
  * applicationUrl: probe inspects the on-disk settings.json for
    ``main.applicationUrl`` https + ``network.trustProxy`` true;
    ensure shares the same settings.json mutation (so the second
    of OIDC/applicationUrl ensurers is a no-op).
  * arr-servers: probe inspects settings.json for radarr/sonarr/
    jellyfin entries with apiKeys; ensure delegates to the
    existing ``configure_jellyseerr`` handler (idempotent skip
    when the probe already ok'd).

No real HTTP and no real Docker/k8s — urllib + the configure handler
+ JobContext factory are all mocked / injected.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from media_stack.adapters.jellyseerr.config_wiring import (
    JellyseerrConfigWirer,
)
from media_stack.adapters.jellyseerr.lifecycle import JellyseerrLifecycle
from media_stack.domain.services import OrchestrationContext


_HOST = "jellyseerr"
_PORT = 5055
_SETTINGS_REL = "jellyseerr/settings.json"


def _ctx(
    config_root: Path | None = None,
    *,
    extra: dict | None = None,
) -> OrchestrationContext:
    cfg = {
        "host": _HOST,
        "port": _PORT,
        "scheme": "http",
    }
    if config_root is not None:
        cfg["config_root"] = str(config_root)
    return OrchestrationContext(
        service_id="jellyseerr",
        config=cfg,
        secrets={},
        now=lambda: 1700000000.0,
        extra=extra or {},
    )


def _http_response(body: bytes, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda *_: None
    return resp


def _seed_settings(root: Path, payload: dict) -> Path:
    p = root / _SETTINGS_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# --- OIDC probe -------------------------------------------------------


class TestProbeOidc:

    @patch("urllib.request.urlopen")
    def test_ok_when_authelia_provider_present(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps({
            "openIdProviders": [
                {"slug": "authelia", "name": "Authelia"},
            ],
        }).encode()
        mock_open.return_value = _http_response(body)

        sl = JellyseerrLifecycle()
        result = sl.probe_oidc(_ctx())
        assert result.is_ok
        assert result.evidence.get("providers_count") == 1

    @patch("urllib.request.urlopen")
    def test_failed_when_providers_empty(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.return_value = _http_response(
            json.dumps({"openIdProviders": []}).encode(),
        )
        sl = JellyseerrLifecycle()
        result = sl.probe_oidc(_ctx())
        assert result.status == "failed"

    @patch("urllib.request.urlopen")
    def test_failed_when_other_provider_present(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps({
            "openIdProviders": [
                {"slug": "google", "name": "Google"},
            ],
        }).encode()
        mock_open.return_value = _http_response(body)
        sl = JellyseerrLifecycle()
        result = sl.probe_oidc(_ctx())
        assert result.status == "failed"
        assert "authelia" in result.detail

    @patch("urllib.request.urlopen")
    def test_unknown_when_endpoint_unreachable(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.side_effect = urllib.error.URLError("dns")
        sl = JellyseerrLifecycle()
        result = sl.probe_oidc(_ctx())
        assert result.status == "unknown"

    def test_unknown_when_no_host(self) -> None:
        ctx = OrchestrationContext(
            service_id="jellyseerr",
            config={"port": _PORT},
            secrets={},
            now=lambda: 0.0,
        )
        sl = JellyseerrLifecycle()
        assert sl.probe_oidc(ctx).status == "unknown"


# --- applicationUrl probe ---------------------------------------------


class TestProbeApplicationUrl:

    def test_ok_when_https_and_trust_proxy(self, tmp_path: Path) -> None:
        _seed_settings(tmp_path, {
            "main": {"applicationUrl": "https://apps.media-stack.local/app/jellyseerr"},
            "network": {"trustProxy": True},
        })
        sl = JellyseerrLifecycle()
        result = sl.probe_application_url(_ctx(tmp_path))
        assert result.is_ok

    def test_failed_when_application_url_http(self, tmp_path: Path) -> None:
        _seed_settings(tmp_path, {
            "main": {"applicationUrl": "http://apps.media-stack.local/app/jellyseerr"},
            "network": {"trustProxy": True},
        })
        sl = JellyseerrLifecycle()
        result = sl.probe_application_url(_ctx(tmp_path))
        assert result.status == "failed"
        assert "https" in result.detail.lower() or "applicationUrl" in result.detail

    def test_failed_when_trust_proxy_missing(self, tmp_path: Path) -> None:
        _seed_settings(tmp_path, {
            "main": {"applicationUrl": "https://apps.media-stack.local/app/jellyseerr"},
            "network": {},
        })
        sl = JellyseerrLifecycle()
        result = sl.probe_application_url(_ctx(tmp_path))
        assert result.status == "failed"

    def test_failed_when_settings_missing(self, tmp_path: Path) -> None:
        sl = JellyseerrLifecycle()
        result = sl.probe_application_url(_ctx(tmp_path))
        assert result.status == "failed"
        assert "not yet generated" in result.detail

    def test_failed_when_no_config_root(self, monkeypatch) -> None:
        # Strip CONFIG_ROOT from env so the wirer can't fall back to it.
        # When CONFIG_ROOT is genuinely unresolvable, the probe must
        # return ``failed`` — NOT ``unknown`` — so the ensurer can
        # escalate to permanent and stop the orchestrator's transient-
        # retry loop. (Pre-fix: ``unknown`` made the orchestrator log
        # WARN every tick at "failed_transient attempt N".)
        monkeypatch.delenv("CONFIG_ROOT", raising=False)
        ctx = OrchestrationContext(
            service_id="jellyseerr",
            config={"host": _HOST, "port": _PORT},
            secrets={},
            now=lambda: 0.0,
        )
        sl = JellyseerrLifecycle()
        assert sl.probe_application_url(ctx).status == "failed"
        assert "no CONFIG_ROOT" in sl.probe_application_url(ctx).detail


# --- arr-servers probe ------------------------------------------------


class TestProbeArrServers:

    def test_ok_when_all_present(self, tmp_path: Path) -> None:
        _seed_settings(tmp_path, {
            "radarr": [{"apiKey": "rk", "url": "http://radarr:7878"}],
            "sonarr": [{"apiKey": "sk", "url": "http://sonarr:8989"}],
            "jellyfin": {"apiKey": "jk"},
        })
        sl = JellyseerrLifecycle()
        result = sl.probe_arr_servers(_ctx(tmp_path))
        assert result.is_ok

    def test_failed_when_radarr_missing(self, tmp_path: Path) -> None:
        _seed_settings(tmp_path, {
            "sonarr": [{"apiKey": "sk"}],
            "jellyfin": {"apiKey": "jk"},
        })
        sl = JellyseerrLifecycle()
        result = sl.probe_arr_servers(_ctx(tmp_path))
        assert result.status == "failed"
        assert "radarr" in result.detail

    def test_failed_when_arr_lacks_api_key(self, tmp_path: Path) -> None:
        _seed_settings(tmp_path, {
            "radarr": [{"url": "http://radarr:7878"}],
            "sonarr": [{"apiKey": "sk"}],
            "jellyfin": {"apiKey": "jk"},
        })
        sl = JellyseerrLifecycle()
        result = sl.probe_arr_servers(_ctx(tmp_path))
        assert result.status == "failed"
        assert "apiKey" in result.detail

    def test_failed_when_jellyfin_key_missing(self, tmp_path: Path) -> None:
        _seed_settings(tmp_path, {
            "radarr": [{"apiKey": "rk"}],
            "sonarr": [{"apiKey": "sk"}],
            "jellyfin": {},
        })
        sl = JellyseerrLifecycle()
        result = sl.probe_arr_servers(_ctx(tmp_path))
        assert result.status == "failed"
        assert "jellyfin" in result.detail.lower()


# --- ensure-oidc / ensure-application-url -----------------------------


class TestEnsureSettingsJson:
    """Both ``ensure_oidc`` and ``ensure_application_url`` route through
    the same settings.json mutation: oidcLogin + providers +
    applicationUrl + trustProxy in one pass. The second ensurer
    sees ``changed=False`` and no-ops (idempotent)."""

    def _seed_minimal(self, tmp_path: Path) -> Path:
        return _seed_settings(tmp_path, {})

    @patch.object(JellyseerrConfigWirer, "_restart_jellyseerr", return_value=True)
    @patch.object(JellyseerrConfigWirer, "_resolve_routing", return_value={})
    def test_ensure_oidc_writes_full_block(
        self, _routing: MagicMock, _restart: MagicMock,
        tmp_path: Path,
    ) -> None:
        path = self._seed_minimal(tmp_path)
        sl = JellyseerrLifecycle()
        outcome = sl.ensure_oidc(_ctx(tmp_path))
        assert outcome.ok
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["main"]["oidcLogin"] is True
        assert data["main"]["applicationUrl"].startswith("https://")
        assert data["network"]["trustProxy"] is True
        providers = data["oidc"]["providers"]
        assert any(p.get("slug") == "authelia" for p in providers)

    @patch.object(JellyseerrConfigWirer, "_restart_jellyseerr", return_value=True)
    @patch.object(JellyseerrConfigWirer, "_resolve_routing", return_value={})
    def test_ensure_application_url_idempotent_on_repeat(
        self, _routing: MagicMock, restart: MagicMock,
        tmp_path: Path,
    ) -> None:
        self._seed_minimal(tmp_path)
        sl = JellyseerrLifecycle()
        # First ensurer mutates + restarts.
        first = sl.ensure_application_url(_ctx(tmp_path))
        assert first.ok
        assert first.evidence.get("settings_written") is True
        # Second ensurer (or repeat call) sees changed=False.
        second = sl.ensure_application_url(_ctx(tmp_path))
        assert second.ok
        assert second.evidence.get("reason") == "already_in_sync"
        # _restart_jellyseerr called exactly once across both.
        assert restart.call_count == 1

    def test_ensure_oidc_transient_when_settings_missing(
        self, tmp_path: Path,
    ) -> None:
        sl = JellyseerrLifecycle()
        outcome = sl.ensure_oidc(_ctx(tmp_path))
        assert not outcome.ok
        assert outcome.transient is True
        assert "not yet generated" in outcome.error

    def test_ensure_application_url_permanent_when_no_config_root(
        self, monkeypatch,
    ) -> None:
        monkeypatch.delenv("CONFIG_ROOT", raising=False)
        ctx = OrchestrationContext(
            service_id="jellyseerr",
            config={"host": _HOST, "port": _PORT},
            secrets={},
            now=lambda: 0.0,
        )
        sl = JellyseerrLifecycle()
        outcome = sl.ensure_application_url(ctx)
        assert not outcome.ok
        assert outcome.transient is False

    @patch.object(JellyseerrConfigWirer, "_restart_jellyseerr", return_value=True)
    @patch.object(JellyseerrConfigWirer, "_resolve_routing",
                   return_value={
                       "base_domain": "example.com",
                       "stack_subdomain": "stack",
                       "gateway_host": "apps.example.com",
                   })
    def test_ensure_oidc_uses_resolved_routing(
        self, _routing: MagicMock, _restart: MagicMock,
        tmp_path: Path,
    ) -> None:
        path = self._seed_minimal(tmp_path)
        sl = JellyseerrLifecycle()
        outcome = sl.ensure_oidc(_ctx(tmp_path))
        assert outcome.ok
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["main"]["applicationUrl"] == "https://apps.example.com/app/jellyseerr"
        provider = data["oidc"]["providers"][0]
        assert provider["issuerUrl"] == "https://authelia.stack.example.com"


# --- ensure-arr-servers -----------------------------------------------


class TestEnsureArrServers:
    """``ensure_arr_servers`` delegates to the existing
    ``configure_jellyseerr`` handler when the probe is not yet ok."""

    def test_idempotent_skip_when_already_configured(
        self, tmp_path: Path,
    ) -> None:
        # Seed a fully-configured settings.json — probe returns ok,
        # ensurer must NOT call the configure handler.
        _seed_settings(tmp_path, {
            "radarr": [{"apiKey": "rk"}],
            "sonarr": [{"apiKey": "sk"}],
            "jellyfin": {"apiKey": "jk"},
        })
        wirer = JellyseerrConfigWirer()
        configure = MagicMock(return_value=None)
        factory = MagicMock()
        outcome = wirer.ensure_arr_servers(
            _ctx(tmp_path),
            configure_handler=configure,
            job_context_factory=factory,
        )
        assert outcome.ok
        assert outcome.evidence.get("reason") == "already_configured"
        configure.assert_not_called()
        factory.assert_not_called()

    def test_invokes_configure_handler_when_probe_failed(
        self, tmp_path: Path,
    ) -> None:
        # Empty settings.json — probe is failed → ensurer calls the
        # injected handler with the JobContext from the factory.
        _seed_settings(tmp_path, {})
        wirer = JellyseerrConfigWirer()
        sentinel_ctx = object()
        factory = MagicMock(return_value=sentinel_ctx)
        configure = MagicMock(return_value={"ok": True})
        outcome = wirer.ensure_arr_servers(
            _ctx(tmp_path),
            configure_handler=configure,
            job_context_factory=factory,
        )
        assert outcome.ok
        configure.assert_called_once_with(sentinel_ctx)
        assert outcome.evidence.get("result") == {"ok": True}

    def test_transient_when_factory_raises(
        self, tmp_path: Path,
    ) -> None:
        _seed_settings(tmp_path, {})
        wirer = JellyseerrConfigWirer()

        def boom() -> None:
            raise RuntimeError("env not ready")

        outcome = wirer.ensure_arr_servers(
            _ctx(tmp_path),
            configure_handler=MagicMock(),
            job_context_factory=boom,
        )
        assert not outcome.ok
        assert outcome.transient is True
        assert "JobContext" in outcome.error

    def test_transient_when_handler_raises(
        self, tmp_path: Path,
    ) -> None:
        _seed_settings(tmp_path, {})
        wirer = JellyseerrConfigWirer()

        def configure(ctx) -> None:
            raise RuntimeError("API 503")

        outcome = wirer.ensure_arr_servers(
            _ctx(tmp_path),
            configure_handler=configure,
            job_context_factory=MagicMock(),
        )
        assert not outcome.ok
        assert outcome.transient is True
        assert "configure_jellyseerr raised" in outcome.error


# --- Lifecycle-level binding ------------------------------------------


class TestLifecycleDelegation:
    """The lifecycle methods are thin delegators — they pass ctx
    straight through to the module-level wirer singleton."""

    def test_probe_oidc_returns_probe_result(self) -> None:
        sl = JellyseerrLifecycle()
        # No mock — just verify no host/port → unknown (the actual
        # short-circuit path through the wirer).
        ctx = OrchestrationContext(
            service_id="jellyseerr",
            config={},
            secrets={},
            now=lambda: 0.0,
        )
        result = sl.probe_oidc(ctx)
        assert result.status == "unknown"

    def test_ensure_arr_servers_lifecycle_path_resolves_handler(
        self, tmp_path: Path,
    ) -> None:
        # Lifecycle path lazy-imports configure_jellyseerr +
        # JobContext. Verify the import chain doesn't blow up by
        # forcing the probe-ok path so we never actually call them.
        _seed_settings(tmp_path, {
            "radarr": [{"apiKey": "rk"}],
            "sonarr": [{"apiKey": "sk"}],
            "jellyfin": {"apiKey": "jk"},
        })
        sl = JellyseerrLifecycle()
        outcome = sl.ensure_arr_servers(_ctx(tmp_path))
        assert outcome.ok
        assert outcome.evidence.get("reason") == "already_configured"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
