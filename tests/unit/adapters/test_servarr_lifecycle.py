"""Tests for ``ServarrLifecycle`` — ADR-0003 Phase 2.

Pin the structural contract (parameterized by ``service_id``,
Protocol-conforming) and the per-method behaviors:

  * Constructor rejects bazarr (Phase 3 territory) and unknown ids.
  * ``probe_running`` tri-state same shape as Jellyfin.
  * ``discover_api_key`` reads ``config.xml`` via the canonical
    ``key_formats`` reader, with env-var short-circuit.
  * ``mint_api_key`` is "wait for the file" — *arr auto-generates on
    first start, so we either succeed (file exists, key present),
    fail-transient (file not yet generated), or fail-permanent (file
    exists but no ``<ApiKey>``).
  * ``persist_api_key`` writes the env var + best-effort secret patch.

No file-system or HTTP I/O — readers/secret-patch are stubbed.
"""

from __future__ import annotations

import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from media_stack.adapters.servarr.lifecycle import ServarrLifecycle
from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ServiceLifecycle,
)


@pytest.fixture(autouse=True)
def _clear_servarr_api_key_envs():
    """``persist_api_key`` writes ``<SERVICE>_API_KEY`` into the live
    ``os.environ`` of the test process. Without per-test cleanup the
    value leaks into unrelated tests downstream (notably the bootstrap
    secret-priming suite that seeds its own keys)."""
    import os as _os
    yield
    for var in ("SONARR_API_KEY", "RADARR_API_KEY",
                "LIDARR_API_KEY", "READARR_API_KEY",
                "PROWLARR_API_KEY"):
        _os.environ.pop(var, None)


def _ctx(service_id: str = "sonarr", **overrides) -> OrchestrationContext:
    cfg = {
        "host": service_id,
        "port": 8989 if service_id == "sonarr" else 7878,
        "scheme": "http",
        "health_path": "/ping",
        "api_key_env": f"{service_id.upper()}_API_KEY",
        "api_key_config": f"{service_id}/config.xml",
        "api_key_format": "xml",
        "config_root": "/srv-config",
    }
    cfg.update(overrides.pop("config", {}))
    return OrchestrationContext(
        service_id=service_id,
        config=cfg,
        secrets=overrides.pop("secrets", {}),
        now=overrides.pop("now", lambda: 1700000000.0),
        **overrides,
    )


class TestConstructor:
    @pytest.mark.parametrize(
        "sid", ["sonarr", "radarr", "lidarr", "readarr", "prowlarr"],
    )
    def test_supported_service_ids_accepted(self, sid: str) -> None:
        impl = ServarrLifecycle(sid)
        assert impl.service_id == sid
        assert isinstance(impl, ServiceLifecycle)

    def test_bazarr_rejected_with_phase_3_pointer(self) -> None:
        # Bazarr genuinely doesn't fit — YAML config + REST settings
        # flow. Phase 3 will land BazarrLifecycle. The error message
        # MUST point operators at the right answer.
        with pytest.raises(ValueError) as exc_info:
            ServarrLifecycle("bazarr")
        assert "Phase 3" in str(exc_info.value) or "BazarrLifecycle" in str(
            exc_info.value,
        )

    def test_unknown_service_id_rejected(self) -> None:
        with pytest.raises(ValueError):
            ServarrLifecycle("not-a-thing")

    def test_case_insensitive(self) -> None:
        # Operators write contract YAMLs in lowercase by convention,
        # but defensive normalization avoids a class of footguns.
        assert ServarrLifecycle("SONARR").service_id == "sonarr"


class TestProbeRunning:
    @patch("urllib.request.urlopen")
    def test_ok_on_http_200(self, mock_open: MagicMock) -> None:
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *_: None
        mock_open.return_value = resp

        r = ServarrLifecycle("radarr").probe_running(_ctx("radarr"))
        assert r.is_ok
        assert r.evidence["url"] == "http://radarr:7878/ping"

    @patch("urllib.request.urlopen")
    def test_unknown_on_dns_or_timeout(self, mock_open: MagicMock) -> None:
        mock_open.side_effect = urllib.error.URLError("Name resolution failed")
        r = ServarrLifecycle("sonarr").probe_running(_ctx("sonarr"))
        assert r.status == "unknown"

    @patch("urllib.request.urlopen")
    def test_failed_on_http_500(self, mock_open: MagicMock) -> None:
        mock_open.side_effect = urllib.error.HTTPError(
            url="x", code=500, msg="boom", hdrs=None, fp=None,
        )
        r = ServarrLifecycle("sonarr").probe_running(_ctx("sonarr"))
        assert r.status == "failed"


class TestDiscoverApiKey:
    def test_env_var_short_circuits_file_read(self, monkeypatch) -> None:
        monkeypatch.setenv("SONARR_API_KEY", "from-env-9876")
        result = ServarrLifecycle("sonarr").discover_api_key(_ctx("sonarr"))
        assert result == "from-env-9876"

    def test_secrets_dict_short_circuits_file_read(self) -> None:
        # Same shape — env supplied via OrchestrationContext.secrets.
        result = ServarrLifecycle("sonarr").discover_api_key(
            _ctx("sonarr", secrets={"SONARR_API_KEY": "from-secrets"}),
        )
        assert result == "from-secrets"

    def test_returns_none_when_config_xml_missing(
        self, monkeypatch, tmp_path,
    ) -> None:
        monkeypatch.delenv("SONARR_API_KEY", raising=False)
        # config_root pointing at empty dir → file doesn't exist
        result = ServarrLifecycle("sonarr").discover_api_key(
            _ctx("sonarr", config={"config_root": str(tmp_path)}),
        )
        assert result is None

    def test_reads_xml_when_present(
        self, monkeypatch, tmp_path,
    ) -> None:
        monkeypatch.delenv("SONARR_API_KEY", raising=False)
        xml = tmp_path / "sonarr" / "config.xml"
        xml.parent.mkdir()
        xml.write_text(
            '<?xml version="1.0"?>\n'
            '<Config>\n'
            '  <ApiKey>real-key-from-xml</ApiKey>\n'
            '</Config>\n',
            encoding="utf-8",
        )
        result = ServarrLifecycle("sonarr").discover_api_key(
            _ctx("sonarr", config={"config_root": str(tmp_path)}),
        )
        assert result == "real-key-from-xml"

    def test_returns_none_for_unknown_format(
        self, monkeypatch, tmp_path,
    ) -> None:
        # api_key_format=foo → no reader registered → None, not raise.
        monkeypatch.delenv("PROWLARR_API_KEY", raising=False)
        path = tmp_path / "prowlarr" / "config.xml"
        path.parent.mkdir()
        path.write_text("doesn't matter")
        result = ServarrLifecycle("prowlarr").discover_api_key(
            _ctx(
                "prowlarr",
                config={
                    "config_root": str(tmp_path),
                    "api_key_format": "totally-fake",
                },
            ),
        )
        assert result is None


class TestProbeHasApiKey:
    def test_ok_when_key_in_env(self, monkeypatch) -> None:
        monkeypatch.setenv("LIDARR_API_KEY", "abcdef")
        r = ServarrLifecycle("lidarr").probe_has_api_key(_ctx("lidarr"))
        assert r.is_ok
        assert r.evidence["source"] == "env"

    def test_failed_when_no_key_anywhere(
        self, monkeypatch, tmp_path,
    ) -> None:
        monkeypatch.delenv("LIDARR_API_KEY", raising=False)
        r = ServarrLifecycle("lidarr").probe_has_api_key(
            _ctx("lidarr", config={"config_root": str(tmp_path)}),
        )
        assert r.status == "failed"


class TestMintApiKey:
    def test_idempotent_when_already_discoverable(
        self, monkeypatch,
    ) -> None:
        monkeypatch.setenv("READARR_API_KEY", "already-here")
        outcome = ServarrLifecycle("readarr").mint_api_key(_ctx("readarr"))
        assert outcome.ok
        assert outcome.value == "already-here"
        assert outcome.attempts == 0
        assert outcome.evidence["reason"] == "already_discoverable"

    def test_transient_failure_when_config_xml_not_yet_generated(
        self, monkeypatch, tmp_path,
    ) -> None:
        # Service is warming up; config.xml hasn't been written yet.
        # Auto-heal cycle retries on the next tick.
        monkeypatch.delenv("RADARR_API_KEY", raising=False)
        outcome = ServarrLifecycle("radarr").mint_api_key(
            _ctx("radarr", config={"config_root": str(tmp_path)}),
        )
        assert not outcome.ok
        assert outcome.transient is True
        assert "not yet generated" in outcome.error

    def test_non_transient_when_xml_present_but_no_key(
        self, monkeypatch, tmp_path,
    ) -> None:
        # File exists but <ApiKey> is missing — that's structural; the
        # *arr should write it on first start. Operator action needed.
        monkeypatch.delenv("RADARR_API_KEY", raising=False)
        xml = tmp_path / "radarr" / "config.xml"
        xml.parent.mkdir()
        xml.write_text("<Config></Config>")
        outcome = ServarrLifecycle("radarr").mint_api_key(
            _ctx("radarr", config={"config_root": str(tmp_path)}),
        )
        assert not outcome.ok
        assert outcome.transient is False
        assert "missing" in outcome.error.lower()

    def test_non_transient_when_no_config_path(self, monkeypatch) -> None:
        monkeypatch.delenv("SONARR_API_KEY", raising=False)
        outcome = ServarrLifecycle("sonarr").mint_api_key(
            _ctx("sonarr", config={"api_key_config": ""}),
        )
        assert not outcome.ok
        assert outcome.transient is False


class TestPersistApiKey:
    def test_refuses_empty_key(self) -> None:
        outcome = ServarrLifecycle("sonarr").persist_api_key(
            "", _ctx("sonarr"),
        )
        assert not outcome.ok
        assert outcome.transient is False

    def test_writes_env_var(self, monkeypatch) -> None:
        monkeypatch.delenv("SONARR_API_KEY", raising=False)
        with patch(
            "media_stack.services.apps.core.job_adapters._persist_preflight_keys_to_secret_safe",
            return_value={"status": "ok"},
        ), patch(
            "media_stack.services.apps.core.job_adapters._stub_state",
            return_value=object(),
        ):
            outcome = ServarrLifecycle("sonarr").persist_api_key(
                "new-key", _ctx("sonarr"),
            )
        import os
        assert outcome.ok
        assert os.environ.get("SONARR_API_KEY") == "new-key"

    def test_transient_failure_when_secret_patch_fails(
        self, monkeypatch,
    ) -> None:
        monkeypatch.delenv("SONARR_API_KEY", raising=False)
        with patch(
            "media_stack.services.apps.core.job_adapters._persist_preflight_keys_to_secret_safe",
            side_effect=RuntimeError("kubectl unauthorized"),
        ), patch(
            "media_stack.services.apps.core.job_adapters._stub_state",
            return_value=object(),
        ):
            outcome = ServarrLifecycle("sonarr").persist_api_key(
                "k", _ctx("sonarr"),
            )
        import os
        # env is still written even though secret failed
        assert os.environ.get("SONARR_API_KEY") == "k"
        assert not outcome.ok
        assert outcome.transient is True
