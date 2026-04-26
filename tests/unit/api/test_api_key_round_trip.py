"""Round-trip tests for API key rotation.

The rotate-keys flow writes a new key into a service's config file
(Sonarr's XML, Prowlarr's XML, Bazarr's INI, Jellyseerr's JSON, etc.)
then expects the service to authenticate with that new key on the
next call. The subtle failure mode is "the writer wrote something
the reader can't parse" — a format change in one half, not the
other. Either the client sends a stale key (no service works) or
the client sends the new key but the service can't read the file
(the service 401s all controller-admin requests).

These tests lock in the invariant: WRITERS[fmt](path, key) produces
a file such that READERS[fmt](path) returns that exact key. A
silent format drift turns into a loud test failure.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.key_formats import READERS, WRITERS  # noqa: E402


_SAMPLE_KEY = "b07f0e97a78d4c4e" + "8bfe0a5c4cf1" + "a62a9c1b"  # valid hex


class WriterReaderRoundTripTests(unittest.TestCase):
    """For every format that supports rotation, a write followed by
    a read must return the original key. Catches the "rotate writes
    new format, discovery reads old format, every Arr* call 401s"
    class of bug."""

    def test_xml_roundtrip(self):
        """Sonarr, Radarr, Prowlarr, Lidarr, Readarr — their XML
        config files are round-tripped via read_xml/write_xml."""
        with tempfile.TemporaryDirectory() as d:
            # Write an initial file so the writer has something to
            # patch (real config.xml has lots of surrounding tags).
            target = Path(d) / "config.xml"
            target.write_text(
                '<Config>\n  <ApiKey>old_key_here</ApiKey>\n  '
                '<OtherSetting>keep-me</OtherSetting>\n</Config>',
                encoding="utf-8",
            )
            WRITERS["xml"](target, _SAMPLE_KEY)
            self.assertEqual(READERS["xml"](target), _SAMPLE_KEY)
            # Adjacent tags must not be disturbed.
            self.assertIn("keep-me", target.read_text(encoding="utf-8"))

    def test_ini_roundtrip(self):
        """qBittorrent uses INI-format credentials — writer edit in
        place, reader finds the key under its [Section] heading."""
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "config.ini"
            target.write_text(
                "[Section]\napi_key = old_value\nother = keep-me\n",
                encoding="utf-8",
            )
            WRITERS["ini"](target, _SAMPLE_KEY)
            self.assertEqual(READERS["ini"](target), _SAMPLE_KEY)
            self.assertIn("keep-me", target.read_text(encoding="utf-8"))

    def test_yaml_roundtrip(self):
        """SABnzbd-style yaml config."""
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "config.yaml"
            target.write_text(
                "misc:\n  api_key: old_value\n  other: keep-me\n",
                encoding="utf-8",
            )
            WRITERS["yaml"](target, _SAMPLE_KEY)
            self.assertEqual(READERS["yaml"](target), _SAMPLE_KEY)
            self.assertIn("keep-me", target.read_text(encoding="utf-8"))

    def test_json_roundtrip(self):
        """Jellyseerr stores its api_key in settings.json."""
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "settings.json"
            target.write_text(
                '{"api_key": "old_value", "other": "keep-me"}',
                encoding="utf-8",
            )
            WRITERS["json"](target, _SAMPLE_KEY)
            self.assertEqual(READERS["json"](target), _SAMPLE_KEY)
            import json as _json
            self.assertEqual(
                _json.loads(target.read_text())["other"], "keep-me",
            )

    def test_random_keys_survive_roundtrip(self):
        """Fuzz a bit — some key formats (e.g. base64 with '=' padding)
        have historically broken formatters that split on equals."""
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "config.xml"
            target.write_text(
                '<Config><ApiKey>placeholder</ApiKey></Config>',
                encoding="utf-8",
            )
            for _ in range(5):
                key = uuid.uuid4().hex
                WRITERS["xml"](target, key)
                self.assertEqual(READERS["xml"](target), key)


class ReaderSafetyTests(unittest.TestCase):
    """Readers must handle missing/corrupt files without raising —
    a rotation dry-run reads the existing key first; an exception
    there aborts the whole rotate cycle for that service."""

    def test_missing_file_returns_empty_not_raises(self):
        for fmt, reader in READERS.items():
            if fmt == "sqlite":
                continue  # sqlite reader needs a real DB file
            with tempfile.TemporaryDirectory() as d:
                missing = Path(d) / "does-not-exist"
                self.assertEqual(
                    reader(missing), "",
                    f"{fmt} reader raised on missing file; "
                    "rotation would abort prematurely.",
                )


if __name__ == "__main__":
    unittest.main()
