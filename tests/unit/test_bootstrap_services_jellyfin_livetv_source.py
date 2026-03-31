import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.jellyfin_livetv_source_service import (  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
