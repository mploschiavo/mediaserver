"""Extended unit tests for Jellyfin service modules with zero or low coverage.

Covers: livetv_service, playback_service, livetv_state_service,
livetv_source_service, home_rails_service, livetv_source_ops.
40 test methods total.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.jellyfin.livetv_service import (  # noqa: E402
    JellyfinLiveTvDependencies,
    JellyfinService,
)
from media_stack.services.apps.jellyfin.playback_service import (  # noqa: E402
    JellyfinPlaybackDependencies,
    JellyfinPlaybackService,
)
from media_stack.services.apps.jellyfin.livetv_state_service import (  # noqa: E402
    JellyfinLiveTvStateService,
)
from media_stack.services.apps.jellyfin.livetv_source_service import (  # noqa: E402
    JellyfinLiveTvSourceService,
)
from media_stack.services.apps.jellyfin.home_rails_service import (  # noqa: E402
    JellyfinHomeRailsDependencies,
    JellyfinHomeRailsService,
)
from media_stack.services.apps.jellyfin.livetv_source_ops import (  # noqa: E402
    transform_m3u_for_guide,
    enrich_xmltv_programmes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_list(v):
    if isinstance(v, (list, tuple)):
        return list(v)
    if isinstance(v, set):
        return list(v)
    if v is None:
        return []
    return [v]


def _bool_cfg(cfg, key, default):
    return bool(cfg.get(key, default))


def _build_query_path(path, params):
    if not params:
        return path
    pairs = [f"{k}={v}" for k, v in params.items()]
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}{'&'.join(pairs)}"


# ---------------------------------------------------------------------------
# LiveTV Service Tests (livetv_service.py) -- 8 tests
# ---------------------------------------------------------------------------

def _make_livetv_deps(**overrides):
    defaults = dict(
        log=MagicMock(),
        bool_cfg=_bool_cfg,
        coerce_list=_coerce_list,
        to_int=lambda v, default=None: int(v) if v is not None else default,
        normalize_url=lambda u: str(u).rstrip("/"),
        wait_for_service=MagicMock(),
        resolve_api_key=MagicMock(return_value="test-key"),
        jellyfin_request=MagicMock(return_value=(200, {}, "")),
        prepare_tuner_url=MagicMock(side_effect=lambda t, g, c, **kw: t.get("url", "")),
        prepare_guide_path=MagicMock(side_effect=lambda g, t, c: g.get("path", "")),
        load_state=MagicMock(return_value={
            "tuner_keys": set(),
            "guide_keys": set(),
            "tuner_ids_by_key": {},
            "tuners_by_key": {},
            "guides_by_key": {},
            "source_path": "/tmp/test",
        }),
        resolve_tuner_type_id=MagicMock(return_value="m3u"),
        normalize_enabled_tuner_ids=MagicMock(return_value=[]),
        delete_entity=MagicMock(),
        trigger_refresh=MagicMock(return_value=(True, "refresh ok")),
    )
    defaults.update(overrides)
    return JellyfinLiveTvDependencies(**defaults)


class TestLiveTvService(unittest.TestCase):
    """8 tests covering livetv_service.py ensure_livetv orchestration."""

    def test_returns_early_when_disabled(self):
        deps = _make_livetv_deps()
        svc = JellyfinService(deps=deps)
        svc.ensure_livetv({"jellyfin_livetv": {"enabled": False}}, "/config", 60)
        deps.wait_for_service.assert_not_called()

    def test_returns_early_when_livetv_section_missing(self):
        deps = _make_livetv_deps()
        svc = JellyfinService(deps=deps)
        svc.ensure_livetv({}, "/config", 60)
        deps.wait_for_service.assert_not_called()

    def test_warns_when_no_tuners_or_guides(self):
        deps = _make_livetv_deps()
        svc = JellyfinService(deps=deps)
        cfg = {"jellyfin_livetv": {"enabled": True, "refresh_on_bootstrap": False}}
        svc.ensure_livetv(cfg, "/config", 60)
        deps.log.assert_any_call(
            "[WARN] Jellyfin Live TV: enabled but no tuners/guides configured."
        )

    def test_raises_when_api_key_unavailable(self):
        deps = _make_livetv_deps(resolve_api_key=MagicMock(return_value=""))
        svc = JellyfinService(deps=deps)
        cfg = {"jellyfin_livetv": {"enabled": True, "tuners": [{"url": "http://t/s.m3u", "type": "m3u"}]}}
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure_livetv(cfg, "/config", 60)
        self.assertIn("API key unavailable", str(ctx.exception))

    def test_raises_when_health_check_fails(self):
        deps = _make_livetv_deps(
            jellyfin_request=MagicMock(return_value=(500, None, "server error")),
        )
        svc = JellyfinService(deps=deps)
        cfg = {"jellyfin_livetv": {"enabled": True, "tuners": [{"url": "http://t/s.m3u", "type": "m3u"}]}}
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure_livetv(cfg, "/config", 60)
        self.assertIn("failed auth/health check", str(ctx.exception))

    def test_raises_on_invalid_tuner_entry(self):
        deps = _make_livetv_deps()
        svc = JellyfinService(deps=deps)
        cfg = {"jellyfin_livetv": {"enabled": True, "tuners": ["not-a-dict"]}}
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure_livetv(cfg, "/config", 60)
        self.assertIn("each tuner entry must be an object", str(ctx.exception))

    def test_adds_tuner_successfully(self):
        responses = [(200, {}, ""), (201, {"Id": "tuner-abc"}, "")]
        idx = {"n": 0}

        def fake_request(*args, **kwargs):
            i = idx["n"]; idx["n"] += 1
            return responses[i] if i < len(responses) else (200, {}, "")

        deps = _make_livetv_deps(jellyfin_request=MagicMock(side_effect=fake_request))
        svc = JellyfinService(deps=deps)
        cfg = {"jellyfin_livetv": {"enabled": True, "tuners": [{"url": "http://t/s.m3u", "type": "m3u"}]}}
        svc.ensure_livetv(cfg, "/config", 60)
        log_strs = [str(c) for c in deps.log.call_args_list]
        self.assertTrue(any("added tuner" in s for s in log_strs))

    def test_refresh_triggered_when_flag_set(self):
        deps = _make_livetv_deps()
        svc = JellyfinService(deps=deps)
        cfg = {"jellyfin_livetv": {"enabled": True, "refresh_on_bootstrap": True}}
        svc.ensure_livetv(cfg, "/config", 60)
        self.assertTrue(deps.trigger_refresh.called)


# ---------------------------------------------------------------------------
# Playback Service Tests (playback_service.py) -- 7 tests
# ---------------------------------------------------------------------------

def _make_playback_deps(**overrides):
    defaults = dict(
        log=MagicMock(),
        bool_cfg=_bool_cfg,
        coerce_list=_coerce_list,
        normalize_url=lambda u: str(u).rstrip("/"),
        wait_for_service=MagicMock(),
        resolve_api_key=MagicMock(return_value="test-key"),
        jellyfin_request=MagicMock(return_value=(200, {}, "")),
        build_query_path=_build_query_path,
        resolve_user_id=MagicMock(return_value="user-123"),
        normalize_plugin_name=lambda v: str(v or "").strip().lower().replace(" ", ""),
    )
    defaults.update(overrides)
    return JellyfinPlaybackDependencies(**defaults)


class TestPlaybackService(unittest.TestCase):
    """7 tests covering playback_service.py."""

    def test_returns_early_when_disabled(self):
        deps = _make_playback_deps()
        svc = JellyfinPlaybackService(deps=deps)
        svc.ensure({"jellyfin_playback": {"enabled": False}}, "/config", 60)
        deps.wait_for_service.assert_not_called()

    def test_raises_when_api_key_unavailable(self):
        deps = _make_playback_deps(resolve_api_key=MagicMock(return_value=""))
        svc = JellyfinPlaybackService(deps=deps)
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure({"jellyfin_playback": {"enabled": True}}, "/config", 60)
        self.assertIn("API key unavailable", str(ctx.exception))

    def test_raises_when_user_id_unavailable(self):
        deps = _make_playback_deps(resolve_user_id=MagicMock(return_value=""))
        svc = JellyfinPlaybackService(deps=deps)
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure({"jellyfin_playback": {"enabled": True}}, "/config", 60)
        self.assertIn("no Jellyfin user id", str(ctx.exception))

    def test_raises_when_user_read_fails(self):
        deps = _make_playback_deps(
            jellyfin_request=MagicMock(return_value=(500, None, "fail")),
        )
        svc = JellyfinPlaybackService(deps=deps)
        with self.assertRaises(RuntimeError) as ctx:
            svc.ensure({"jellyfin_playback": {"enabled": True}}, "/config", 60)
        self.assertIn("failed reading user", str(ctx.exception))

    def test_no_change_when_defaults_match(self):
        user_cfg = {
            "AudioLanguagePreference": "eng", "PlayDefaultAudioTrack": True,
            "SubtitleLanguagePreference": "eng", "SubtitleMode": "Smart",
            "RememberAudioSelections": True, "RememberSubtitleSelections": True,
            "EnableNextEpisodeAutoPlay": True, "DisplayCollectionsView": False,
            "HidePlayedInLatest": False,
        }
        server_cfg = {
            "PreferredMetadataLanguage": "en", "MetadataCountryCode": "US",
            "UICulture": "en-US", "ImageSavingConvention": "Compatible",
            "ChapterImageResolution": "P720",
            "EnableGroupingMoviesIntoCollections": True,
            "EnableGroupingShowsIntoCollections": True,
            "EnableExternalContentInSuggestions": True,
        }
        responses = [
            (200, {"Configuration": dict(user_cfg)}, ""),
            (200, dict(server_cfg), ""),
            (200, [], ""),
        ]
        idx = {"n": 0}

        def fake_req(*a, **kw):
            i = idx["n"]; idx["n"] += 1
            return responses[i] if i < len(responses) else (200, {}, "")

        deps = _make_playback_deps(jellyfin_request=MagicMock(side_effect=fake_req))
        svc = JellyfinPlaybackService(deps=deps)
        cfg = {"jellyfin_playback": {"enabled": True, "display_preferences": {"enabled": False}, "home_media": {"enabled": False}}}
        svc.ensure(cfg, "/config", 60)
        log_strs = [str(c) for c in deps.log.call_args_list]
        self.assertTrue(any("user defaults already match" in s for s in log_strs))

    def test_updates_user_defaults_when_different(self):
        responses = [
            (200, {"Configuration": {"AudioLanguagePreference": "jpn"}}, ""),
            (204, None, ""),
            (200, {"PreferredMetadataLanguage": "en", "MetadataCountryCode": "US",
                    "UICulture": "en-US", "ImageSavingConvention": "Compatible",
                    "ChapterImageResolution": "P720",
                    "EnableGroupingMoviesIntoCollections": True,
                    "EnableGroupingShowsIntoCollections": True,
                    "EnableExternalContentInSuggestions": True}, ""),
            (200, [], ""),
        ]
        idx = {"n": 0}

        def fake_req(*a, **kw):
            i = idx["n"]; idx["n"] += 1
            return responses[i] if i < len(responses) else (200, {}, "")

        deps = _make_playback_deps(jellyfin_request=MagicMock(side_effect=fake_req))
        svc = JellyfinPlaybackService(deps=deps)
        cfg = {"jellyfin_playback": {"enabled": True, "display_preferences": {"enabled": False}, "home_media": {"enabled": False}}}
        svc.ensure(cfg, "/config", 60)
        log_strs = [str(c) for c in deps.log.call_args_list]
        self.assertTrue(any("updated user defaults" in s for s in log_strs))

    def test_intro_skip_plugin_detected(self):
        responses = [
            (200, {"Configuration": {}}, ""),       # GET user
            (204, {}, ""),                           # POST user config (defaults differ)
            (200, {}, ""),                           # GET server config
            (200, [{"Name": "Intro Skipper"}], ""),  # GET plugins
        ]
        idx = {"n": 0}

        def fake_req(*a, **kw):
            i = idx["n"]; idx["n"] += 1
            return responses[i] if i < len(responses) else (200, {}, "")

        deps = _make_playback_deps(jellyfin_request=MagicMock(side_effect=fake_req))
        svc = JellyfinPlaybackService(deps=deps)
        cfg = {"jellyfin_playback": {"enabled": True, "display_preferences": {"enabled": False}, "home_media": {"enabled": False}}}
        svc.ensure(cfg, "/config", 60)
        log_strs = [str(c) for c in deps.log.call_args_list]
        self.assertTrue(any("Intro Skipper plugin is installed" in s for s in log_strs))


# ---------------------------------------------------------------------------
# LiveTV State Service Tests (livetv_state_service.py) -- 10 tests
# ---------------------------------------------------------------------------

def _make_state_service(**overrides):
    defaults = dict(
        coerce_list=_coerce_list,
        resolve_path=lambda base, rel: Path(base) / Path(str(rel)),
        candidate_config_roots=lambda root: [Path(root)],
        jellyfin_request=MagicMock(),
        log=MagicMock(),
    )
    defaults.update(overrides)
    return JellyfinLiveTvStateService(**defaults)


class TestLiveTvStateService(unittest.TestCase):
    """10 tests covering livetv_state_service.py."""

    def test_load_state_returns_empty_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_state_service()
            state = svc.load_state(tmp, {})
            self.assertEqual(state["tuner_keys"], set())
            self.assertEqual(state["guide_keys"], set())

    def test_load_state_parses_xml(self):
        xml = """<LiveTv>
  <TunerHosts><TunerHostInfo>
    <Id>t1</Id><Type>m3u</Type><Url>http://t/1.m3u</Url>
  </TunerHostInfo></TunerHosts>
  <ListingProviders><ListingsProviderInfo>
    <Id>g1</Id><Type>xmltv</Type><Path>/g/guide.xml</Path>
    <EnableAllTuners>true</EnableAllTuners>
  </ListingsProviderInfo></ListingProviders>
</LiveTv>"""
        with tempfile.TemporaryDirectory() as tmp:
            xml_path = Path(tmp) / "jellyfin" / "config" / "livetv.xml"
            xml_path.parent.mkdir(parents=True, exist_ok=True)
            xml_path.write_text(xml)
            svc = _make_state_service()
            state = svc.load_state(tmp, {})
            self.assertIn(("m3u", "http://t/1.m3u"), state["tuner_keys"])
            self.assertIn(("xmltv", "/g/guide.xml"), state["guide_keys"])
            self.assertEqual(state["tuner_ids_by_key"][("m3u", "http://t/1.m3u")], "t1")

    def test_resolve_tuner_type_by_id(self):
        svc = _make_state_service(
            jellyfin_request=MagicMock(return_value=(200, [{"Id": "m3u", "Name": "M3U"}], "")),
        )
        self.assertEqual(svc.resolve_tuner_type_id("http://jf", "key", "m3u"), "m3u")

    def test_resolve_tuner_type_by_name(self):
        svc = _make_state_service(
            jellyfin_request=MagicMock(return_value=(200, [{"Id": "m3u", "Name": "M3U Tuner"}], "")),
        )
        self.assertEqual(svc.resolve_tuner_type_id("http://jf", "key", "m3u tuner"), "m3u")

    def test_resolve_tuner_type_raises_when_not_found(self):
        svc = _make_state_service(
            jellyfin_request=MagicMock(return_value=(200, [{"Id": "m3u", "Name": "M3U"}], "")),
        )
        with self.assertRaises(RuntimeError):
            svc.resolve_tuner_type_id("http://jf", "key", "nonexistent")

    def test_normalize_enabled_tuner_ids_passthrough(self):
        svc = _make_state_service()
        result = svc.normalize_enabled_tuner_ids(["id-1", "id-2"], {"tuner_ids_by_key": {}})
        self.assertEqual(result, ["id-1", "id-2"])

    def test_normalize_enabled_tuner_ids_url_lookup(self):
        svc = _make_state_service()
        state = {"tuner_ids_by_key": {("m3u", "http://t/1.m3u"): "resolved-1"}}
        result = svc.normalize_enabled_tuner_ids(["tuner-url:http://t/1.m3u"], state)
        self.assertEqual(result, ["resolved-1"])

    def test_delete_entity_tuner(self):
        req = MagicMock(return_value=(200, None, ""))
        svc = _make_state_service(jellyfin_request=req)
        svc.delete_entity("http://jf", "key", "tuner", "t-1")
        self.assertIn("/LiveTv/TunerHosts", req.call_args[0][1])

    def test_delete_entity_raises_for_unknown(self):
        svc = _make_state_service()
        with self.assertRaises(RuntimeError):
            svc.delete_entity("http://jf", "key", "unknown", "id-1")

    def test_trigger_refresh_success(self):
        svc = _make_state_service(
            jellyfin_request=MagicMock(return_value=(204, None, "")),
        )
        ok, msg = svc.trigger_refresh("http://jf", "key", "/LiveTv/RefreshChannels", "channel refresh")
        self.assertTrue(ok)
        self.assertIn("requested channel refresh", msg)


# ---------------------------------------------------------------------------
# LiveTV Source Service Tests (livetv_source_service.py) -- 7 tests
# ---------------------------------------------------------------------------

class TestLiveTvSourceService(unittest.TestCase):
    """7 tests covering livetv_source_service.py static helpers."""

    def test_extract_xmltv_channel_ids(self):
        xml = '<tv><channel id="ch1"/><channel id="ch2"/></tv>'
        result = JellyfinLiveTvSourceService._extract_xmltv_channel_ids(xml)
        self.assertEqual(result, {"ch1", "ch2"})

    def test_extract_xmltv_channel_ids_empty(self):
        self.assertEqual(JellyfinLiveTvSourceService._extract_xmltv_channel_ids(""), set())

    def test_rewrite_extinf_tvg_id(self):
        line = '#EXTINF:-1 tvg-id="old@sfx",Test'
        result = JellyfinLiveTvSourceService._rewrite_extinf_tvg_id(line, "new")
        self.assertIn('tvg-id="new"', result)

    def test_container_path_jellyfin_prefix(self):
        result = JellyfinLiveTvSourceService._container_path_for_materialized_playlist(
            "jellyfin/livetv-tuners/abc.m3u"
        )
        self.assertEqual(result, "/config/livetv-tuners/abc.m3u")

    def test_container_path_empty(self):
        self.assertEqual(
            JellyfinLiveTvSourceService._container_path_for_materialized_playlist(""), ""
        )

    def test_category_from_group_title_sports(self):
        self.assertEqual(JellyfinLiveTvSourceService._category_from_group_title("NFL Games"), "Sports")

    def test_normalize_tvg_id_strips_suffix(self):
        self.assertEqual(JellyfinLiveTvSourceService._normalize_tvg_id("ch1@iptv"), "ch1")


# ---------------------------------------------------------------------------
# Home Rails Service Tests (home_rails_service.py) -- 5 tests
# ---------------------------------------------------------------------------

def _make_rails_deps(**overrides):
    defaults = dict(
        log=MagicMock(),
        bool_cfg=_bool_cfg,
        coerce_list=_coerce_list,
        to_int=lambda v, default=None: int(v) if v is not None else default,
        jellyfin_request=MagicMock(return_value=(200, {"Items": []}, "")),
        jellyfin_build_query_path=_build_query_path,
        jellyfin_items_from_payload=lambda p: p.get("Items", []) if isinstance(p, dict) else [],
        normalize_item_ids=lambda items: [
            str(i.get("Id")) for i in items if isinstance(i, dict) and i.get("Id")
        ],
        chunked=lambda vals, size: [vals[i:i + size] for i in range(0, len(vals), size)],
        resolve_jellyfin_user_id_value=MagicMock(return_value="user-1"),
    )
    defaults.update(overrides)
    return JellyfinHomeRailsDependencies(**defaults)


class TestHomeRailsService(unittest.TestCase):
    """5 tests covering home_rails_service.py."""

    def test_find_collection_returns_id_when_found(self):
        deps = _make_rails_deps(
            jellyfin_request=MagicMock(return_value=(
                200, {"Items": [{"Name": "My Collection", "Id": "col-123"}]}, "",
            )),
        )
        svc = JellyfinHomeRailsService(deps=deps)
        self.assertEqual(
            svc.find_collection_by_name("http://jf", "key", "u1", "My Collection"),
            "col-123",
        )

    def test_find_collection_returns_empty_when_missing(self):
        deps = _make_rails_deps(
            jellyfin_request=MagicMock(return_value=(200, {"Items": []}, "")),
        )
        svc = JellyfinHomeRailsService(deps=deps)
        self.assertEqual(svc.find_collection_by_name("http://jf", "key", "u1", "X"), "")

    def test_collection_item_ids_empty_for_blank_id(self):
        deps = _make_rails_deps()
        svc = JellyfinHomeRailsService(deps=deps)
        self.assertEqual(svc.collection_item_ids("http://jf", "key", "u1", ""), [])

    def test_delete_collection_returns_false_when_missing(self):
        deps = _make_rails_deps(
            jellyfin_request=MagicMock(return_value=(200, {"Items": []}, "")),
        )
        svc = JellyfinHomeRailsService(deps=deps)
        self.assertFalse(svc.delete_collection_by_name("http://jf", "key", "u1", "X"))

    def test_update_collection_items_adds_and_removes(self):
        req = MagicMock(return_value=(200, None, ""))
        deps = _make_rails_deps(jellyfin_request=req)
        svc = JellyfinHomeRailsService(deps=deps)
        added, removed = svc.update_collection_items(
            "http://jf", "key", "col-1", ["add-1"], ["rm-1"],
        )
        self.assertEqual(added, 1)
        self.assertEqual(removed, 1)


# ---------------------------------------------------------------------------
# LiveTV Source Ops Tests (livetv_source_ops.py) -- 3 tests
# ---------------------------------------------------------------------------

class TestLiveTvSourceOps(unittest.TestCase):
    """3 tests covering livetv_source_ops.py transform and enrich functions."""

    def test_transform_m3u_normalizes_tvg_id(self):
        m3u = "#EXTM3U\n#EXTINF:-1 tvg-id=\"ch1@iptv\",Ch1\nhttp://s/1\n"
        rendered, summary = transform_m3u_for_guide(
            m3u,
            normalize_tvg_id_suffix=True,
            guide_channel_ids=None,
            rewrite_extinf_tvg_id=JellyfinLiveTvSourceService._rewrite_extinf_tvg_id,
        )
        self.assertIn('tvg-id="ch1"', rendered)
        self.assertEqual(summary["normalized_ids"], 1)

    def test_transform_m3u_filters_by_guide(self):
        m3u = "#EXTM3U\n#EXTINF:-1 tvg-id=\"keep\",Keep\nhttp://s/k\n#EXTINF:-1 tvg-id=\"drop\",Drop\nhttp://s/d\n"
        _, summary = transform_m3u_for_guide(
            m3u,
            normalize_tvg_id_suffix=False,
            guide_channel_ids={"keep"},
            rewrite_extinf_tvg_id=JellyfinLiveTvSourceService._rewrite_extinf_tvg_id,
        )
        self.assertEqual(summary["kept_entries"], 1)
        self.assertEqual(summary["dropped_entries"], 1)

    def test_enrich_xmltv_adds_icon(self):
        xml = '<tv>\n<programme channel="ch1" start="20250101">\n  <title>S</title>\n</programme>\n</tv>\n'
        _, summary = enrich_xmltv_programmes(
            xml,
            logo_by_channel={"ch1": "http://logo/ch1.png"},
            groups_by_channel={},
            channel_display_names={},
            logo_by_name={},
            add_icons=True,
            replace_existing_icons=False,
            add_categories=False,
            default_category="",
            default_icon_url="",
            normalize_tvg_id=JellyfinLiveTvSourceService._normalize_tvg_id,
            category_from_group_title=JellyfinLiveTvSourceService._category_from_group_title,
            normalize_name=JellyfinLiveTvSourceService._normalize_name,
        )
        self.assertEqual(summary["icons_added"], 1)


if __name__ == "__main__":
    unittest.main()
