"""Tests for EPG merge service — fuzzy matching, caching, stream parse."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.infrastructure.jellyfin.epg_merge_service import (
    _extract_m3u_tvg_ids,
    _stream_extract_channels,
    _stream_extract_programmes_for_ids,
    _normalize_for_match,
    _tokenize,
    _token_similarity,
    _build_id_mapping,
    _get_cached_or_download,
    merge_epgs,
)


class TestExtractM3uTvgIds(unittest.TestCase):
    def test_extracts_ids(self):
        m3u = '#EXTM3U\n#EXTINF:-1 tvg-id="CNN.us" tvg-logo="x",CNN\nhttp://stream\n'
        ids = _extract_m3u_tvg_ids(m3u)
        self.assertEqual(ids, {"CNN.us": "CNN"})

    def test_empty_tvg_id_skipped(self):
        m3u = '#EXTINF:-1 tvg-id="" tvg-logo="x",Empty\nhttp://s\n'
        self.assertEqual(_extract_m3u_tvg_ids(m3u), {})

    def test_multiple_channels(self):
        m3u = '#EXTINF:-1 tvg-id="A.us",ChA\nhttp://a\n#EXTINF:-1 tvg-id="B.us",ChB\nhttp://b\n'
        ids = _extract_m3u_tvg_ids(m3u)
        self.assertEqual(len(ids), 2)
        self.assertIn("A.us", ids)
        self.assertIn("B.us", ids)

    def test_no_channels(self):
        self.assertEqual(_extract_m3u_tvg_ids("#EXTM3U\n"), {})


class TestStreamExtractChannels(unittest.TestCase):
    def test_extracts_channel(self):
        xml = '<tv><channel id="CNN"><display-name>CNN</display-name></channel></tv>'
        ch = _stream_extract_channels(xml)
        self.assertEqual(ch, {"CNN": ["CNN"]})

    def test_multiple_display_names(self):
        xml = '<channel id="X"><display-name>Name1</display-name><display-name>Name2</display-name></channel>'
        ch = _stream_extract_channels(xml)
        self.assertEqual(ch["X"], ["Name1", "Name2"])

    def test_no_channels(self):
        self.assertEqual(_stream_extract_channels("<tv></tv>"), {})


class TestStreamExtractProgrammes(unittest.TestCase):
    def test_extracts_matching(self):
        xml = '<programme channel="A">P1</programme><programme channel="B">P2</programme><programme channel="A">P3</programme>'
        progs = _stream_extract_programmes_for_ids(xml, {"A"})
        self.assertEqual(len(progs.get("A", [])), 2)
        self.assertNotIn("B", progs)

    def test_no_match(self):
        xml = '<programme channel="X">P</programme>'
        self.assertEqual(_stream_extract_programmes_for_ids(xml, {"Y"}), {})

    def test_empty_xml(self):
        self.assertEqual(_stream_extract_programmes_for_ids("", {"A"}), {})


class TestNormalize(unittest.TestCase):
    def test_removes_resolution(self):
        self.assertEqual(_normalize_for_match("CNN (1080p)"), "cnn")

    def test_removes_brackets(self):
        self.assertEqual(_normalize_for_match("BBC [HD]"), "bbc")

    def test_lowercases(self):
        self.assertEqual(_normalize_for_match("FOX News"), "foxnews")


class TestTokenSimilarity(unittest.TestCase):
    def test_identical(self):
        self.assertAlmostEqual(_token_similarity("Fox News", "Fox News"), 1.0)

    def test_similar(self):
        score = _token_similarity("Fox News Channel", "Fox News")
        self.assertGreater(score, 0.5)

    def test_different(self):
        score = _token_similarity("CNN", "BBC World")
        self.assertLess(score, 0.3)

    def test_empty(self):
        self.assertEqual(_token_similarity("", ""), 0.0)


class TestBuildIdMapping(unittest.TestCase):
    def test_exact_match(self):
        m3u = {"CNN.us": "CNN"}
        epg = {"CNN.us": ["CNN"]}
        mapping = _build_id_mapping(m3u, epg)
        self.assertEqual(mapping["CNN.us"], "CNN.us")

    def test_case_insensitive(self):
        m3u = {"CNN.us": "CNN"}
        epg = {"cnn.us": ["CNN"]}
        mapping = _build_id_mapping(m3u, epg)
        self.assertEqual(mapping["cnn.us"], "CNN.us")

    def test_normalized_match(self):
        m3u = {"FoxNews.us": "Fox News"}
        epg = {"foxnews.us": ["Fox News"]}
        mapping = _build_id_mapping(m3u, epg)
        self.assertIn("foxnews.us", mapping)

    def test_display_name_match(self):
        m3u = {"ABC.us": "ABC News"}
        epg = {"different-id": ["ABC News"]}
        mapping = _build_id_mapping(m3u, epg)
        self.assertEqual(mapping["different-id"], "ABC.us")

    def test_no_match(self):
        m3u = {"CNN.us": "CNN"}
        epg = {"totally-different": ["Totally Different Channel"]}
        mapping = _build_id_mapping(m3u, epg)
        self.assertNotIn("totally-different", mapping)

    def test_substring_match(self):
        m3u = {"BBCAmerica.us": "BBC America"}
        epg = {"bbcamerica": ["BBC America (HD)"]}
        mapping = _build_id_mapping(m3u, epg)
        self.assertIn("bbcamerica", mapping)

    def test_token_similarity_match(self):
        m3u = {"ESPNNews.us": "ESPN News Channel"}
        epg = {"espn-news-ch": ["ESPN News"]}
        mapping = _build_id_mapping(m3u, epg)
        # Should match via token similarity
        self.assertIn("espn-news-ch", mapping)


class TestCache(unittest.TestCase):
    def test_caches_to_disk(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("media_stack.infrastructure.jellyfin.epg_merge_service._download_xml",
                       return_value="<tv></tv>"):
                result = _get_cached_or_download("https://example.com/epg.xml", td)
            self.assertEqual(result, "<tv></tv>")
            # Check cache file exists
            cache_dir = Path(td) / ".controller" / "epg-cache"
            self.assertTrue(any(cache_dir.glob("*.xml")))

    def test_returns_cached(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("media_stack.infrastructure.jellyfin.epg_merge_service._download_xml",
                       return_value="<tv>first</tv>") as mock_dl:
                r1 = _get_cached_or_download("https://example.com/epg.xml", td)
                r2 = _get_cached_or_download("https://example.com/epg.xml", td)
            self.assertEqual(r1, "<tv>first</tv>")
            self.assertEqual(r2, "<tv>first</tv>")
            mock_dl.assert_called_once()  # Only downloaded once


class TestMergeEpgs(unittest.TestCase):
    def test_merge_basic(self):
        with tempfile.TemporaryDirectory() as td:
            # Create an M3U file
            m3u_path = Path(td) / "test.m3u"
            m3u_path.write_text('#EXTINF:-1 tvg-id="CNN.us",CNN\nhttp://stream\n')

            # Mock EPG download
            epg_xml = '<tv><channel id="CNN.us"><display-name>CNN</display-name></channel>' \
                      '<programme channel="CNN.us" start="20260411">News</programme></tv>'

            with patch("media_stack.infrastructure.jellyfin.epg_merge_service._download_all_parallel",
                       return_value=[({"url": "http://epg", "name": "Test"}, epg_xml, None)]):
                result = merge_epgs(
                    m3u_paths=[str(m3u_path)],
                    epg_sources=[{"url": "http://epg", "name": "Test"}],
                    output_path=str(Path(td) / "merged.xml"),
                    config_root=td,
                )

            self.assertEqual(result["channels"], 1)
            self.assertEqual(result["programmes"], 1)
            self.assertTrue(Path(td, "merged.xml").is_file())

    def test_no_tvg_ids(self):
        with tempfile.TemporaryDirectory() as td:
            m3u_path = Path(td) / "empty.m3u"
            m3u_path.write_text("#EXTM3U\n")
            result = merge_epgs(
                m3u_paths=[str(m3u_path)],
                epg_sources=[],
                output_path=str(Path(td) / "merged.xml"),
                config_root=td,
            )
            self.assertIn("error", result)

    def test_download_failure_handled(self):
        with tempfile.TemporaryDirectory() as td:
            m3u_path = Path(td) / "test.m3u"
            m3u_path.write_text('#EXTINF:-1 tvg-id="A",ChA\nhttp://s\n')
            with patch("media_stack.infrastructure.jellyfin.epg_merge_service._download_all_parallel",
                       return_value=[({"url": "http://fail"}, None, "timeout")]):
                result = merge_epgs(
                    m3u_paths=[str(m3u_path)],
                    epg_sources=[{"url": "http://fail"}],
                    output_path=str(Path(td) / "merged.xml"),
                    config_root=td,
                )
            self.assertEqual(result["sources_failed"], 1)


if __name__ == "__main__":
    unittest.main()
