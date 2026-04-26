import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.jellyfin.livetv_state_service import (  # noqa: E402
    JellyfinLiveTvStateService,
)


class JellyfinLiveTvStateServiceTests(unittest.TestCase):
    def test_load_state_parses_tuners_and_guides(self):
        with tempfile.TemporaryDirectory() as tmp:
            xml_path = Path(tmp) / "jellyfin" / "config" / "livetv.xml"
            xml_path.parent.mkdir(parents=True, exist_ok=True)
            xml_path.write_text(
                """
<LiveTv>
  <TunerHosts>
    <TunerHostInfo>
      <Id>tuner-1</Id>
      <Type>m3u</Type>
      <Url>http://example/tuner.m3u</Url>
    </TunerHostInfo>
  </TunerHosts>
  <ListingProviders>
    <ListingsProviderInfo>
      <Id>guide-1</Id>
      <Type>xmltv</Type>
      <Path>http://example/guide.xml</Path>
      <EnableAllTuners>false</EnableAllTuners>
      <EnabledTuners>
        <string>tuner-1</string>
      </EnabledTuners>
    </ListingsProviderInfo>
  </ListingProviders>
</LiveTv>
                """.strip(),
                encoding="utf-8",
            )

            service = JellyfinLiveTvStateService(
                coerce_list=lambda value: value if isinstance(value, list) else [value],
                resolve_path=lambda root, rel: Path(root) / Path(str(rel)),
                candidate_config_roots=lambda root: [Path(root)],
                jellyfin_request=lambda *_args, **_kwargs: (200, [], ""),
                log=lambda _msg: None,
            )

            state = service.load_state(tmp, {"livetv_xml_path": "jellyfin/config/livetv.xml"})
            self.assertIn(("m3u", "http://example/tuner.m3u"), state["tuner_keys"])
            self.assertIn(("xmltv", "http://example/guide.xml"), state["guide_keys"])
            self.assertEqual(
                state["tuner_ids_by_key"][("m3u", "http://example/tuner.m3u")], "tuner-1"
            )

    def test_normalize_enabled_tuner_ids_resolves_alias_tokens(self):
        service = JellyfinLiveTvStateService(
            coerce_list=lambda value: value if isinstance(value, list) else [value],
            resolve_path=lambda root, rel: Path(root) / Path(str(rel)),
            candidate_config_roots=lambda root: [Path(root)],
            jellyfin_request=lambda *_args, **_kwargs: (200, [], ""),
            log=lambda _msg: None,
        )
        state = {
            "tuner_ids_by_key": {
                ("m3u", "http://example/tuner.m3u"): "tuner-1",
                ("hdhr", "http://example/hdhr"): "tuner-2",
            }
        }
        normalized = service.normalize_enabled_tuner_ids(
            [
                "tuner-url:http://example/tuner.m3u",
                "tuner-type-url:hdhr|http://example/hdhr",
                "tuner-1",
            ],
            state,
        )
        self.assertEqual(normalized, ["tuner-1", "tuner-2"])


if __name__ == "__main__":
    unittest.main()
