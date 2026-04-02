import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.apps.jellyfin.livetv_source_service import (  # noqa: E402
    JellyfinLiveTvSourceService,
)


class JellyfinLiveTvSourceServiceTests(unittest.TestCase):
    def _service(self, logs):
        return JellyfinLiveTvSourceService(
            coerce_list=lambda value: (
                value if isinstance(value, list) else ([] if value is None else [value])
            ),
            candidate_config_roots=lambda root: [Path(str(root))],
            resolve_path=lambda base, maybe_rel: Path(base) / Path(str(maybe_rel)),
            log=logs.append,
        )

    def test_prepare_m3u_tuner_url_filters_to_guide_channels(self):
        logs = []
        service = self._service(logs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.m3u"
            guide = root / "guide.xml"
            source.write_text(
                "\n".join(
                    [
                        "#EXTM3U",
                        '#EXTINF:-1 tvg-id="match@iptv",Match',
                        "http://stream/match",
                        '#EXTINF:-1 tvg-id="drop@iptv",Drop',
                        "http://stream/drop",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            guide.write_text(
                '<tv><channel id="match"></channel></tv>',
                encoding="utf-8",
            )

            rendered_url = service.prepare_m3u_tuner_url(
                tuner={
                    "type": "m3u",
                    "url": str(source),
                    "normalize_tvg_id_suffix": True,
                    "filter_to_guide_channels": True,
                    "materialized_output_path": "jellyfin/livetv-tuners/test.m3u",
                },
                guides=[{"path": str(guide)}],
                config_root=str(root),
                guide_channel_ids_cache={},
            )

            self.assertEqual(rendered_url, "/config/livetv-tuners/test.m3u")
            output_path = root / "jellyfin" / "livetv-tuners" / "test.m3u"
            rendered = output_path.read_text(encoding="utf-8")
            self.assertIn('tvg-id="match"', rendered)
            self.assertNotIn("drop", rendered)
            self.assertTrue(any("prepared tuner playlist" in line for line in logs))

    def test_prepare_m3u_tuner_url_falls_back_when_filtered_empty(self):
        logs = []
        service = self._service(logs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.m3u"
            guide = root / "guide.xml"
            source.write_text(
                "\n".join(
                    [
                        "#EXTM3U",
                        '#EXTINF:-1 tvg-id="a@iptv",A',
                        "http://stream/a",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            guide.write_text('<tv><channel id="missing"></channel></tv>', encoding="utf-8")

            rendered_url = service.prepare_m3u_tuner_url(
                tuner={
                    "type": "m3u",
                    "url": str(source),
                    "normalize_tvg_id_suffix": True,
                    "filter_to_guide_channels": True,
                    "materialized_output_path": "jellyfin/livetv-tuners/fallback.m3u",
                },
                guides=[{"path": str(guide)}],
                config_root=str(root),
                guide_channel_ids_cache={},
            )

            self.assertEqual(rendered_url, "/config/livetv-tuners/fallback.m3u")
            output_path = root / "jellyfin" / "livetv-tuners" / "fallback.m3u"
            rendered = output_path.read_text(encoding="utf-8")
            self.assertIn('tvg-id="a"', rendered)
            self.assertTrue(
                any("guide-filtered playlist was empty" in line for line in logs),
                msg=f"logs={logs}",
            )

    def test_prepare_xmltv_guide_path_enriches_icons_and_categories(self):
        logs = []
        service = self._service(logs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_m3u = root / "source.m3u"
            guide_xml = root / "guide.xml"
            source_m3u.write_text(
                "\n".join(
                    [
                        "#EXTM3U",
                        '#EXTINF:-1 tvg-id="sports.1@iptv" tvg-logo="https://example.test/sports.png" group-title="Sports",Sports One',
                        "http://stream/sports-one",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            guide_xml.write_text(
                "\n".join(
                    [
                        "<tv>",
                        '  <channel id="sports.1"/>',
                        '  <programme start="20260330010000 +0000" stop="20260330020000 +0000" channel="sports.1">',
                        "    <title>Sports Tonight</title>",
                        "  </programme>",
                        "</tv>",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            rendered_path = service.prepare_xmltv_guide_path(
                guide={
                    "type": "xmltv",
                    "path": str(guide_xml),
                    "materialized_output_path": "jellyfin/livetv-guides/test.enriched.xml",
                    "enrich_program_icons_from_tuner_logo": True,
                    "enrich_program_categories_from_tuner_groups": True,
                },
                tuners=[
                    {
                        "type": "m3u",
                        "url": str(source_m3u),
                    }
                ],
                config_root=str(root),
            )

            self.assertEqual(rendered_path, "/config/livetv-guides/test.enriched.xml")
            output_path = root / "jellyfin" / "livetv-guides" / "test.enriched.xml"
            enriched = output_path.read_text(encoding="utf-8")
            self.assertIn('icon src="https://example.test/sports.png"', enriched)
            self.assertIn('<category lang="en">Sports</category>', enriched)
            self.assertTrue(
                any("prepared XMLTV guide" in line for line in logs), msg=f"logs={logs}"
            )

    def test_prepare_xmltv_guide_path_uses_display_name_logo_fallback_and_default_category(self):
        logs = []
        service = self._service(logs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_m3u = root / "source.m3u"
            guide_xml = root / "guide.xml"
            source_m3u.write_text(
                "\n".join(
                    [
                        "#EXTM3U",
                        '#EXTINF:-1 tvg-id="unknown.chan" tvg-logo="https://example.test/logo.png" tvg-name="Example Network",Example Network',
                        "http://stream/example-network",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            guide_xml.write_text(
                "\n".join(
                    [
                        "<tv>",
                        '  <channel id="epg.chan.1">',
                        "    <display-name>Example Network</display-name>",
                        "  </channel>",
                        '  <programme start="20260330010000 +0000" stop="20260330020000 +0000" channel="epg.chan.1">',
                        "    <title>Late Night Show</title>",
                        "  </programme>",
                        "</tv>",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            rendered_path = service.prepare_xmltv_guide_path(
                guide={
                    "type": "xmltv",
                    "path": str(guide_xml),
                    "materialized_output_path": "jellyfin/livetv-guides/display-name-fallback.xml",
                    "enrich_program_icons_from_tuner_logo": True,
                    "enrich_program_categories_from_tuner_groups": True,
                    "default_program_category": "Shows",
                },
                tuners=[
                    {
                        "type": "m3u",
                        "url": str(source_m3u),
                    }
                ],
                config_root=str(root),
            )

            self.assertEqual(rendered_path, "/config/livetv-guides/display-name-fallback.xml")
            output_path = root / "jellyfin" / "livetv-guides" / "display-name-fallback.xml"
            enriched = output_path.read_text(encoding="utf-8")
            self.assertIn('icon src="https://example.test/logo.png"', enriched)
            self.assertIn('<category lang="en">Shows</category>', enriched)

    def test_prepare_xmltv_guide_path_replaces_existing_program_icons_when_enabled(self):
        logs = []
        service = self._service(logs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_m3u = root / "source.m3u"
            guide_xml = root / "guide.xml"
            source_m3u.write_text(
                "\n".join(
                    [
                        "#EXTM3U",
                        '#EXTINF:-1 tvg-id="news.1@iptv" tvg-logo="https://example.test/new-logo.png" group-title="News",News One',
                        "http://stream/news-one",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            guide_xml.write_text(
                "\n".join(
                    [
                        "<tv>",
                        '  <channel id="news.1"/>',
                        '  <programme start="20260330010000 +0000" stop="20260330020000 +0000" channel="news.1">',
                        "    <title>News Hour</title>",
                        '    <icon src="https://old.example/icon.png" />',
                        "  </programme>",
                        "</tv>",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            rendered_path = service.prepare_xmltv_guide_path(
                guide={
                    "type": "xmltv",
                    "path": str(guide_xml),
                    "materialized_output_path": "jellyfin/livetv-guides/replace-icons.xml",
                    "enrich_program_icons_from_tuner_logo": True,
                    "replace_existing_program_icons_with_tuner_logo": True,
                },
                tuners=[
                    {
                        "type": "m3u",
                        "url": str(source_m3u),
                    }
                ],
                config_root=str(root),
            )

            self.assertEqual(rendered_path, "/config/livetv-guides/replace-icons.xml")
            output_path = root / "jellyfin" / "livetv-guides" / "replace-icons.xml"
            enriched = output_path.read_text(encoding="utf-8")
            self.assertNotIn("https://old.example/icon.png", enriched)
            self.assertIn("https://example.test/new-logo.png", enriched)

    def test_prepare_xmltv_guide_path_uses_default_program_icon_when_no_logo_match(self):
        logs = []
        service = self._service(logs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_m3u = root / "source.m3u"
            guide_xml = root / "guide.xml"
            source_m3u.write_text(
                "\n".join(
                    [
                        "#EXTM3U",
                        '#EXTINF:-1 tvg-id="channel-no-logo@iptv" group-title="News",Channel Without Logo',
                        "http://stream/channel-no-logo",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            guide_xml.write_text(
                "\n".join(
                    [
                        "<tv>",
                        '  <channel id="channel-no-logo"/>',
                        '  <programme start="20260330010000 +0000" stop="20260330020000 +0000" channel="channel-no-logo">',
                        "    <title>Morning Bulletin</title>",
                        "  </programme>",
                        "</tv>",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            rendered_path = service.prepare_xmltv_guide_path(
                guide={
                    "type": "xmltv",
                    "path": str(guide_xml),
                    "materialized_output_path": "jellyfin/livetv-guides/default-icon.xml",
                    "enrich_program_icons_from_tuner_logo": True,
                    "default_program_icon_url": "https://example.test/default-live-tv.png",
                },
                tuners=[
                    {
                        "type": "m3u",
                        "url": str(source_m3u),
                    }
                ],
                config_root=str(root),
            )

            self.assertEqual(rendered_path, "/config/livetv-guides/default-icon.xml")
            output_path = root / "jellyfin" / "livetv-guides" / "default-icon.xml"
            enriched = output_path.read_text(encoding="utf-8")
            self.assertIn("https://example.test/default-live-tv.png", enriched)

    def test_prepare_xmltv_guide_path_appends_mapped_category_when_existing_category_present(self):
        logs = []
        service = self._service(logs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_m3u = root / "source.m3u"
            guide_xml = root / "guide.xml"
            source_m3u.write_text(
                "\n".join(
                    [
                        "#EXTM3U",
                        '#EXTINF:-1 tvg-id="sports.2@iptv" group-title="Sports",Sports Two',
                        "http://stream/sports-two",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            guide_xml.write_text(
                "\n".join(
                    [
                        "<tv>",
                        '  <channel id="sports.2"/>',
                        '  <programme start="20260330010000 +0000" stop="20260330020000 +0000" channel="sports.2">',
                        "    <title>Sports Center</title>",
                        '    <category lang="en">Entertainment</category>',
                        "  </programme>",
                        "</tv>",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            rendered_path = service.prepare_xmltv_guide_path(
                guide={
                    "type": "xmltv",
                    "path": str(guide_xml),
                    "materialized_output_path": "jellyfin/livetv-guides/category-append.xml",
                    "enrich_program_categories_from_tuner_groups": True,
                },
                tuners=[
                    {
                        "type": "m3u",
                        "url": str(source_m3u),
                    }
                ],
                config_root=str(root),
            )

            self.assertEqual(rendered_path, "/config/livetv-guides/category-append.xml")
            output_path = root / "jellyfin" / "livetv-guides" / "category-append.xml"
            enriched = output_path.read_text(encoding="utf-8")
            self.assertIn('<category lang="en">Entertainment</category>', enriched)
            self.assertIn('<category lang="en">Sports</category>', enriched)


if __name__ == "__main__":
    unittest.main()
