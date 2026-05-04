"""Tests for ``BazarrLifecycle.probe_*`` + ``ensure_config_wiring`` —
the lifecycle-method port of the legacy ``ensure_bazarr_language_profile``
job handler (ADR-0005 Phase 3 cutover).

Five families of behavior:

  * Probes (one per promise) map a slice of Bazarr's REST surface to
    the tri-state ProbeResult. Missing api key / unreachable host →
    unknown; assertion miss → failed; assertion hit → ok.
  * The Jellyfin-plugin probe inspects an XML file under the config
    root rather than hitting an HTTP endpoint.
  * The single shared ensurer:
      - returns transient failure when api key is missing (orchestrator
        retries after probe_has_api_key reaches ok)
      - returns permanent failure when host/port aren't in config
      - skips profile creation when an operator-customised profile
        already exists, but always re-asserts defaults + providers +
        arr-integration (so drift is corrected on every run)
      - writes the Jellyfin Bazarr-plugin XML at the assembly-named
        path (Jellyfin.Plugin.Bazarr.xml — NOT Bazarr.xml)
      - cleans up any stray ``Bazarr.xml`` from before the v1.0.146 fix
      - HTTP 4xx → permanent failure; URLError → transient failure

No real HTTP / no real disk for HTTP — urllib is mocked. Plugin XML
write IS exercised against a tmp_path so the filename + payload shape
land on disk where the assertions can verify them.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from media_stack.adapters.bazarr.lifecycle import BazarrLifecycle
from media_stack.domain.services import OrchestrationContext


_BAZARR_KEY = "bz-test-key-1234567890"
_SONARR_KEY = "sn-test-key-abcdef"
_RADARR_KEY = "rd-test-key-abcdef"


@pytest.fixture(autouse=True)
def _clear_envs():
    import os as _os
    yield
    for var in (
        "BAZARR_API_KEY", "SONARR_API_KEY", "RADARR_API_KEY",
        "CONFIG_ROOT",
    ):
        _os.environ.pop(var, None)


def _ctx(
    *,
    bz_key: str = _BAZARR_KEY,
    sn_key: str = _SONARR_KEY,
    rd_key: str = _RADARR_KEY,
    config_root: str | Path | None = None,
) -> OrchestrationContext:
    secrets: dict[str, str] = {}
    if bz_key:
        secrets["BAZARR_API_KEY"] = bz_key
    if sn_key:
        secrets["SONARR_API_KEY"] = sn_key
    if rd_key:
        secrets["RADARR_API_KEY"] = rd_key
    cfg: dict[str, object] = {
        "host": "bazarr",
        "port": 6767,
        "scheme": "http",
        "api_key_env": "BAZARR_API_KEY",
    }
    if config_root is not None:
        cfg["config_root"] = str(config_root)
    return OrchestrationContext(
        service_id="bazarr",
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


# --- Probe: language profile -----------------------------------------


class TestProbeLanguageProfile:

    @patch("urllib.request.urlopen")
    def test_ok_when_profile_exists(self, mock_open: MagicMock) -> None:
        mock_open.return_value = _http_response(
            json.dumps([{"profileId": 1, "name": "English"}]).encode(),
        )
        bl = BazarrLifecycle()
        result = bl.probe_language_profile(_ctx())
        assert result.is_ok
        assert result.evidence.get("profile_count") == 1

    @patch("urllib.request.urlopen")
    def test_failed_when_profile_list_empty(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.return_value = _http_response(b"[]")
        bl = BazarrLifecycle()
        result = bl.probe_language_profile(_ctx())
        assert result.status == "failed"
        assert result.evidence.get("profile_count") == 0

    @patch("urllib.request.urlopen")
    def test_unknown_when_bazarr_unreachable(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.side_effect = urllib.error.URLError("dns")
        bl = BazarrLifecycle()
        result = bl.probe_language_profile(_ctx())
        assert result.status == "unknown"

    def test_unknown_when_no_api_key(self) -> None:
        bl = BazarrLifecycle()
        result = bl.probe_language_profile(_ctx(bz_key=""))
        assert result.status == "unknown"
        assert "api key" in result.detail.lower()

    def test_unknown_when_no_host_in_config(self) -> None:
        ctx = OrchestrationContext(
            service_id="bazarr",
            config={"port": 6767},
            secrets={"BAZARR_API_KEY": "k"},
            now=lambda: 0.0,
        )
        bl = BazarrLifecycle()
        result = bl.probe_language_profile(ctx)
        assert result.status == "unknown"


# --- Probe: default profile toggles ---------------------------------


class TestProbeDefaultProfileToggles:

    @patch("urllib.request.urlopen")
    def test_ok_when_both_toggles_on(self, mock_open: MagicMock) -> None:
        mock_open.return_value = _http_response(
            json.dumps({
                "general": {
                    "serie_default_enabled": True,
                    "movie_default_enabled": True,
                },
            }).encode(),
        )
        bl = BazarrLifecycle()
        result = bl.probe_default_profile_toggles(_ctx())
        assert result.is_ok

    @patch("urllib.request.urlopen")
    def test_failed_when_serie_off(self, mock_open: MagicMock) -> None:
        mock_open.return_value = _http_response(
            json.dumps({
                "general": {
                    "serie_default_enabled": False,
                    "movie_default_enabled": True,
                },
            }).encode(),
        )
        bl = BazarrLifecycle()
        result = bl.probe_default_profile_toggles(_ctx())
        assert result.status == "failed"
        assert result.evidence.get("serie_default_enabled") is False

    @patch("urllib.request.urlopen")
    def test_unknown_when_settings_unreachable(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.side_effect = urllib.error.URLError("dns")
        bl = BazarrLifecycle()
        result = bl.probe_default_profile_toggles(_ctx())
        assert result.status == "unknown"


# --- Probe: providers -----------------------------------------------


class TestProbeProviders:

    @patch("urllib.request.urlopen")
    def test_ok_when_curated_set_present(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.return_value = _http_response(
            json.dumps({
                "general": {
                    "enabled_providers": [
                        "opensubtitlescom", "podnapisi", "gestdown",
                        "yifysubtitles", "embeddedsubtitles",
                        # extra ones are fine — assertion is "subset of"
                        "extra-provider",
                    ],
                },
            }).encode(),
        )
        bl = BazarrLifecycle()
        result = bl.probe_providers(_ctx())
        assert result.is_ok

    @patch("urllib.request.urlopen")
    def test_failed_when_a_provider_is_missing(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.return_value = _http_response(
            json.dumps({
                "general": {
                    "enabled_providers": [
                        "opensubtitlescom", "podnapisi", "gestdown",
                        "yifysubtitles",
                        # missing: embeddedsubtitles
                    ],
                },
            }).encode(),
        )
        bl = BazarrLifecycle()
        result = bl.probe_providers(_ctx())
        assert result.status == "failed"
        assert "embeddedsubtitles" in result.evidence.get("missing", [])


# --- Probe: arr integration -----------------------------------------


class TestProbeArrIntegration:

    @patch("urllib.request.urlopen")
    def test_ok_when_both_arrs_wired(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.return_value = _http_response(
            json.dumps({
                "general": {"use_sonarr": True, "use_radarr": True},
                "sonarr": {"ip": "sonarr", "apikey": "sk"},
                "radarr": {"ip": "radarr", "apikey": "rk"},
            }).encode(),
        )
        bl = BazarrLifecycle()
        result = bl.probe_arr_integration(_ctx())
        assert result.is_ok

    @patch("urllib.request.urlopen")
    def test_failed_when_sonarr_apikey_missing(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.return_value = _http_response(
            json.dumps({
                "general": {"use_sonarr": True, "use_radarr": True},
                "sonarr": {"ip": "sonarr", "apikey": ""},
                "radarr": {"ip": "radarr", "apikey": "rk"},
            }).encode(),
        )
        bl = BazarrLifecycle()
        result = bl.probe_arr_integration(_ctx())
        assert result.status == "failed"
        assert (
            result.evidence.get("sonarr", {}).get("apikey_present") is False
        )


# --- Probe: jellyfin plugin config -----------------------------------


class TestProbeJellyfinPluginConfig:

    def test_failed_when_xml_absent(self, tmp_path: Path) -> None:
        bl = BazarrLifecycle()
        result = bl.probe_jellyfin_plugin_config(_ctx(config_root=tmp_path))
        assert result.status == "failed"

    def test_ok_when_xml_has_url_and_apikey(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "jellyfin" / "plugins" / "configurations"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "Jellyfin.Plugin.Bazarr.xml").write_text(
            "<root>\n"
            "  <BazarrUrl>http://bazarr:6767</BazarrUrl>\n"
            "  <BazarrApiKey>somekey</BazarrApiKey>\n"
            "</root>\n",
            encoding="utf-8",
        )
        bl = BazarrLifecycle()
        result = bl.probe_jellyfin_plugin_config(_ctx(config_root=tmp_path))
        assert result.is_ok

    def test_failed_when_apikey_blank(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "jellyfin" / "plugins" / "configurations"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "Jellyfin.Plugin.Bazarr.xml").write_text(
            "<root>\n"
            "  <BazarrUrl>http://bazarr:6767</BazarrUrl>\n"
            "  <BazarrApiKey></BazarrApiKey>\n"
            "</root>\n",
            encoding="utf-8",
        )
        bl = BazarrLifecycle()
        result = bl.probe_jellyfin_plugin_config(_ctx(config_root=tmp_path))
        assert result.status == "failed"


# --- Ensurer: prereq guards ------------------------------------------


class TestEnsureConfigWiringPrereqs:

    def test_permanent_failure_when_no_host(self) -> None:
        ctx = OrchestrationContext(
            service_id="bazarr",
            config={"port": 6767},
            secrets={"BAZARR_API_KEY": "k"},
            now=lambda: 0.0,
        )
        bl = BazarrLifecycle()
        outcome = bl.ensure_config_wiring(ctx)
        assert not outcome.ok
        assert outcome.transient is False

    def test_transient_failure_when_no_api_key(self) -> None:
        bl = BazarrLifecycle()
        outcome = bl.ensure_config_wiring(_ctx(bz_key=""))
        assert not outcome.ok
        assert outcome.transient is True
        assert "api key" in outcome.error.lower()


# --- Ensurer: happy path ---------------------------------------------


class TestEnsureConfigWiringHappyPath:

    @patch("urllib.request.urlopen")
    def test_creates_profile_and_posts_settings_and_writes_xml(
        self, mock_open: MagicMock, tmp_path: Path,
    ) -> None:
        # Sequence: GET profiles (empty) → POST settings (200).
        responses = [_http_response(b"[]"), _http_response(b"", status=200)]
        mock_open.side_effect = responses

        bl = BazarrLifecycle()
        outcome = bl.ensure_config_wiring(_ctx(config_root=tmp_path))
        assert outcome.ok, outcome.error
        # Two HTTP calls: GET profiles + POST settings.
        assert mock_open.call_count == 2

        # POST URL and form payload structure.
        post_call = mock_open.call_args_list[1]
        req = post_call.args[0]
        assert req.get_method() == "POST"
        assert req.full_url == "http://bazarr:6767/api/system/settings"
        # Form-encoded body — parse with parse_qs (multi-value).
        from urllib.parse import parse_qs
        body = parse_qs(req.data.decode())
        assert body["languages-enabled"] == ["en"]
        # Curated provider set (each appears at least once via repeated key).
        assert set(body["settings-general-enabled_providers"]) >= {
            "opensubtitlescom", "podnapisi", "gestdown",
            "yifysubtitles", "embeddedsubtitles",
        }
        # Default-profile toggle pair.
        assert body["settings-general-serie_default_enabled"] == ["true"]
        assert body["settings-general-movie_default_enabled"] == ["true"]
        # *arr integration: both wired (we provided both keys).
        assert body["settings-general-use_sonarr"] == ["true"]
        assert body["settings-general-use_radarr"] == ["true"]
        assert body["settings-sonarr-ip"] == ["sonarr"]
        assert body["settings-radarr-ip"] == ["radarr"]
        assert body["settings-sonarr-apikey"] == [_SONARR_KEY]
        assert body["settings-radarr-apikey"] == [_RADARR_KEY]
        # Profile JSON blob carries name + language code.
        profiles_json = json.loads(body["languages-profiles"][0])
        assert profiles_json[0]["name"] == "English"
        assert profiles_json[0]["items"][0]["language"] == "en"

        # Plugin XML — assembly-named filename and the right contents.
        xml_path = (
            tmp_path
            / "jellyfin" / "plugins" / "configurations"
            / "Jellyfin.Plugin.Bazarr.xml"
        )
        assert xml_path.is_file()
        xml = xml_path.read_text(encoding="utf-8")
        assert "<BazarrUrl>http://bazarr:6767</BazarrUrl>" in xml
        assert f"<BazarrApiKey>{_BAZARR_KEY}</BazarrApiKey>" in xml
        # Outcome evidence reports.
        ev = outcome.evidence
        assert ev.get("jellyfin_plugin_config") == "written"
        assert ev.get("arr_integrations") == ["sonarr", "radarr"]
        assert "created" in ev.get("profile", "")

    @patch("urllib.request.urlopen")
    def test_skips_profile_creation_when_one_exists(
        self, mock_open: MagicMock, tmp_path: Path,
    ) -> None:
        # Existing profile preserves operator's customised id (42).
        responses = [
            _http_response(
                json.dumps([{"profileId": 42, "name": "Custom"}]).encode(),
            ),
            _http_response(b"", status=200),
        ]
        mock_open.side_effect = responses
        bl = BazarrLifecycle()
        outcome = bl.ensure_config_wiring(_ctx(config_root=tmp_path))
        assert outcome.ok
        ev = outcome.evidence
        assert "skipped" in ev.get("profile", "")
        assert "id=42" in ev.get("profile", "")

    @patch("urllib.request.urlopen")
    def test_cleans_up_stray_bazarr_xml_filename(
        self, mock_open: MagicMock, tmp_path: Path,
    ) -> None:
        # Stray pre-v1.0.146 ``Bazarr.xml`` should be removed during
        # the assembly-named write.
        plugin_dir = tmp_path / "jellyfin" / "plugins" / "configurations"
        plugin_dir.mkdir(parents=True)
        stray = plugin_dir / "Bazarr.xml"
        stray.write_text("<old/>\n", encoding="utf-8")

        responses = [_http_response(b"[]"), _http_response(b"", status=200)]
        mock_open.side_effect = responses
        bl = BazarrLifecycle()
        outcome = bl.ensure_config_wiring(_ctx(config_root=tmp_path))
        assert outcome.ok
        assert not stray.exists()
        assert (plugin_dir / "Jellyfin.Plugin.Bazarr.xml").is_file()


# --- Ensurer: arr-integration cross-service handling ----------------


class TestEnsureConfigWiringArrIntegration:

    @patch("urllib.request.urlopen")
    def test_skips_arr_block_when_arr_key_missing(
        self, mock_open: MagicMock, tmp_path: Path,
    ) -> None:
        # No SONARR_API_KEY in secrets — the sonarr block must NOT be
        # in the form body, and the evidence list reflects that.
        responses = [_http_response(b"[]"), _http_response(b"", status=200)]
        mock_open.side_effect = responses
        bl = BazarrLifecycle()
        outcome = bl.ensure_config_wiring(
            _ctx(sn_key="", config_root=tmp_path),
        )
        assert outcome.ok
        post_req = mock_open.call_args_list[1].args[0]
        from urllib.parse import parse_qs
        body = parse_qs(post_req.data.decode())
        assert "settings-sonarr-apikey" not in body
        assert "settings-general-use_sonarr" not in body
        # Radarr is still wired.
        assert body["settings-radarr-ip"] == ["radarr"]
        assert outcome.evidence.get("arr_integrations") == ["radarr"]


# --- Ensurer: HTTP failure semantics ---------------------------------


class TestEnsureConfigWiringHttpFailureSemantics:

    @patch("urllib.request.urlopen")
    def test_transient_failure_when_profile_list_unreachable(
        self, mock_open: MagicMock, tmp_path: Path,
    ) -> None:
        mock_open.side_effect = urllib.error.URLError("dns")
        bl = BazarrLifecycle()
        outcome = bl.ensure_config_wiring(_ctx(config_root=tmp_path))
        assert not outcome.ok
        assert outcome.transient is True

    @patch("urllib.request.urlopen")
    def test_permanent_failure_on_post_4xx(
        self, mock_open: MagicMock, tmp_path: Path,
    ) -> None:
        responses = [
            _http_response(b"[]"),
            urllib.error.HTTPError(
                "http://bazarr:6767/api/system/settings",
                400, "Bad", {}, None,
            ),
        ]
        mock_open.side_effect = responses
        bl = BazarrLifecycle()
        outcome = bl.ensure_config_wiring(_ctx(config_root=tmp_path))
        assert not outcome.ok
        assert outcome.transient is False
        assert outcome.evidence.get("http_status") == 400

    @patch("urllib.request.urlopen")
    def test_transient_failure_on_post_urlerror(
        self, mock_open: MagicMock, tmp_path: Path,
    ) -> None:
        responses = [
            _http_response(b"[]"),
            urllib.error.URLError("connection refused"),
        ]
        mock_open.side_effect = responses
        bl = BazarrLifecycle()
        outcome = bl.ensure_config_wiring(_ctx(config_root=tmp_path))
        assert not outcome.ok
        assert outcome.transient is True
