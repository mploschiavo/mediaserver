"""Tests for the safe XML read/write helpers in
``media_stack.core.config_io``.

These cover the failure modes that drove the module:

- A corrupt input (`</Config>sm>\n</Config>`, the real Prowlarr
  artifact) must be detected — we cannot silently round-trip it.
- A successful atomic write must replace the file in one step
  and re-parse cleanly.
- A simulated write that produces invalid XML must roll back to
  the pre-write contents.
- ``set_or_create_child`` must add missing nodes and skip writes
  when the value is already correct.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.config_io import (  # noqa: E402
    ConfigParseError,
    atomic_write_xml,
    read_and_parse_xml,
    set_or_create_child,
)


# Real corruption observed in production on 2026-04-20 —
# Prowlarr's config.xml after a SIGTERM mid-write.
_PROWLARR_CORRUPT = (
    b"<Config>\n"
    b"  <Port>9696</Port>\n"
    b"  <UrlBase>/app/prowlarr</UrlBase>\n"
    b"</Config>sm>\n"
    b"</Config>\n"
)

_PROWLARR_VALID = (
    b"<Config>\n"
    b"  <Port>9696</Port>\n"
    b"  <UrlBase>/app/prowlarr</UrlBase>\n"
    b"</Config>\n"
)


class ReadAndParseXmlTests(unittest.TestCase):

    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_valid_xml_returns_tree(self) -> None:
        path = self.tmp / "config.xml"
        path.write_bytes(_PROWLARR_VALID)
        tree = read_and_parse_xml(path)
        self.assertEqual(tree.getroot().tag, "Config")
        self.assertEqual(
            tree.getroot().find("UrlBase").text, "/app/prowlarr",
        )

    def test_corrupt_xml_raises_with_path_in_message(self) -> None:
        """The real Prowlarr artifact must raise — silent
        ``errors='replace'`` is a regression we won't accept."""
        path = self.tmp / "config.xml"
        path.write_bytes(_PROWLARR_CORRUPT)
        with self.assertRaises(ConfigParseError) as ctx:
            read_and_parse_xml(path)
        self.assertIn("XML parse error", str(ctx.exception))
        self.assertIn(str(path), str(ctx.exception))

    def test_missing_file_raises(self) -> None:
        path = self.tmp / "missing.xml"
        with self.assertRaises(ConfigParseError):
            read_and_parse_xml(path)


class AtomicWriteXmlTests(unittest.TestCase):

    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _write_initial(self, contents: bytes) -> Path:
        path = self.tmp / "config.xml"
        path.write_bytes(contents)
        return path

    def test_atomic_write_replaces_file(self) -> None:
        path = self._write_initial(_PROWLARR_VALID)
        tree = read_and_parse_xml(path)
        tree.getroot().find("UrlBase").text = "/changed"
        atomic_write_xml(path, tree)
        re_parsed = read_and_parse_xml(path)
        self.assertEqual(
            re_parsed.getroot().find("UrlBase").text, "/changed",
        )

    def test_no_temp_file_left_behind(self) -> None:
        path = self._write_initial(_PROWLARR_VALID)
        tree = read_and_parse_xml(path)
        atomic_write_xml(path, tree)
        leftovers = [
            p.name for p in self.tmp.iterdir() if p.suffix == ".new"
        ]
        self.assertEqual(leftovers, [])

    def test_post_write_parse_failure_rolls_back(self) -> None:
        """Simulate a write that lands as garbage — verify we
        restore the original contents from the .bak."""
        path = self._write_initial(_PROWLARR_VALID)
        tree = read_and_parse_xml(path)

        original_replace = __import__("os").replace

        def corrupting_replace(src, dst):
            # Mutate the temp file to corrupt bytes immediately
            # before the rename, then proceed normally.
            Path(src).write_bytes(b"<Config><BadlyClosed>\n")
            return original_replace(src, dst)

        with patch("media_stack.core.config_io.os.replace",
                   corrupting_replace):
            with self.assertRaises(ConfigParseError):
                atomic_write_xml(path, tree)

        # Original contents restored from .bak.
        self.assertEqual(path.read_bytes(), _PROWLARR_VALID)

    def test_no_existing_file_writes_clean(self) -> None:
        """First-boot path: destination doesn't exist yet — there
        is no backup to take, but the write must still succeed and
        re-parse."""
        path = self.tmp / "fresh.xml"
        tree = ET.ElementTree(ET.fromstring(_PROWLARR_VALID))
        atomic_write_xml(path, tree)
        self.assertTrue(path.exists())
        re_parsed = read_and_parse_xml(path)
        self.assertEqual(re_parsed.getroot().tag, "Config")


class SetOrCreateChildTests(unittest.TestCase):

    def test_creates_when_missing(self) -> None:
        root = ET.fromstring(b"<Config></Config>")
        changed = set_or_create_child(root, "UrlBase", "/app/foo")
        self.assertTrue(changed)
        self.assertEqual(root.find("UrlBase").text, "/app/foo")

    def test_overwrites_when_different(self) -> None:
        root = ET.fromstring(
            b"<Config><UrlBase>/old</UrlBase></Config>",
        )
        changed = set_or_create_child(root, "UrlBase", "/new")
        self.assertTrue(changed)
        self.assertEqual(root.find("UrlBase").text, "/new")

    def test_noop_when_unchanged(self) -> None:
        root = ET.fromstring(
            b"<Config><UrlBase>/same</UrlBase></Config>",
        )
        changed = set_or_create_child(root, "UrlBase", "/same")
        self.assertFalse(changed)


class HttpPreflightCorruptInputTests(unittest.TestCase):
    """End-to-end: the production preflight must skip a corrupt
    config.xml instead of round-tripping it. This is the regression
    test for the 2026-04-20 Prowlarr crashloop."""

    def test_preflight_skips_corrupt_config_without_writing(self) -> None:
        import tempfile
        from media_stack.services.apps.servarr.http_preflight import (
            ServarrHttpPreflight,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "prowlarr").mkdir()
            corrupt_path = root / "prowlarr" / "config.xml"
            corrupt_path.write_bytes(_PROWLARR_CORRUPT)
            mtime_before = corrupt_path.stat().st_mtime_ns

            messages: list[str] = []
            preflight = ServarrHttpPreflight(env={})
            preflight.run_preflight(
                config_root=str(root), log=messages.append,
            )

            # The preflight may have called the API reconciler —
            # we only care that the on-disk file is untouched.
            self.assertEqual(
                corrupt_path.stat().st_mtime_ns, mtime_before,
                "Corrupt config.xml was modified — preflight must "
                "refuse to write to a file it can't parse.",
            )
            self.assertTrue(
                any("unparseable" in m for m in messages),
                f"Expected an 'unparseable' log line. Got: {messages}",
            )


if __name__ == "__main__":
    unittest.main()
