"""Tests for shared key format readers and writers."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.key_formats import (  # noqa: E402
    READERS, WRITERS,
    read_xml, read_ini, read_yaml, read_json, read_sqlite,
    write_xml, write_ini, write_yaml, write_json,
)


class TestReaders(unittest.TestCase):
    def test_all_formats_registered(self):
        self.assertEqual(set(READERS.keys()), {"xml", "ini", "yaml", "json", "sqlite"})

    def test_read_xml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write("<Config><ApiKey>abc123</ApiKey></Config>")
        result = read_xml(Path(f.name))
        os.unlink(f.name)
        self.assertEqual(result, "abc123")

    def test_read_xml_missing(self):
        self.assertEqual(read_xml(Path("/nonexistent")), "")

    def test_read_ini(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
            f.write("[main]\napi_key = def456\n")
        result = read_ini(Path(f.name))
        os.unlink(f.name)
        self.assertEqual(result, "def456")

    def test_read_yaml_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("apikey: ghi789\n")
        result = read_yaml(Path(f.name))
        os.unlink(f.name)
        self.assertEqual(result, "ghi789")

    def test_read_json_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"main": {"apiKey": "jkl012"}}, f)
        result = read_json(Path(f.name))
        os.unlink(f.name)
        self.assertEqual(result, "jkl012")

    def test_read_json_missing_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"other": "data"}, f)
        result = read_json(Path(f.name))
        os.unlink(f.name)
        self.assertEqual(result, "")

    def test_read_sqlite(self):
        import sqlite3
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            pass
        conn = sqlite3.connect(f.name)
        conn.execute("CREATE TABLE ApiKeys (Id INTEGER PRIMARY KEY, AccessToken TEXT)")
        conn.execute("INSERT INTO ApiKeys (AccessToken) VALUES ('sqlkey1')")
        conn.commit()
        conn.close()
        result = read_sqlite(Path(f.name))
        os.unlink(f.name)
        self.assertEqual(result, "sqlkey1")

    def test_read_sqlite_missing(self):
        self.assertEqual(read_sqlite(Path("/nonexistent")), "")


class TestWriters(unittest.TestCase):
    def test_all_formats_registered(self):
        self.assertEqual(set(WRITERS.keys()), {"xml", "ini", "yaml", "json"})

    def test_write_xml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write("<Config><ApiKey>old</ApiKey></Config>")
        write_xml(Path(f.name), "new_key")
        content = Path(f.name).read_text()
        os.unlink(f.name)
        self.assertIn("<ApiKey>new_key</ApiKey>", content)

    def test_write_ini(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
            f.write("[main]\napi_key = old\n")
        write_ini(Path(f.name), "new_key")
        content = Path(f.name).read_text()
        os.unlink(f.name)
        self.assertIn("api_key = new_key", content)

    def test_write_yaml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("auth:\n  apikey: old\n")
        write_yaml(Path(f.name), "new_key")
        result = read_yaml(Path(f.name))
        os.unlink(f.name)
        self.assertEqual(result, "new_key")

    def test_write_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"main": {"apiKey": "old"}}, f)
        write_json(Path(f.name), "new_key")
        result = read_json(Path(f.name))
        os.unlink(f.name)
        self.assertEqual(result, "new_key")

    def test_write_xml_roundtrip(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write("<Config><Port>8989</Port><ApiKey>original</ApiKey><Other>x</Other></Config>")
        write_xml(Path(f.name), "rotated")
        result = read_xml(Path(f.name))
        # Verify other tags preserved
        content = Path(f.name).read_text()
        os.unlink(f.name)
        self.assertEqual(result, "rotated")
        self.assertIn("<Port>8989</Port>", content)

    def test_write_json_preserves_other_keys(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"main": {"apiKey": "old", "other": "data"}, "extra": True}, f)
        write_json(Path(f.name), "new")
        data = json.loads(Path(f.name).read_text())
        os.unlink(f.name)
        self.assertEqual(data["main"]["apiKey"], "new")
        self.assertEqual(data["main"]["other"], "data")
        self.assertTrue(data["extra"])


if __name__ == "__main__":
    unittest.main()
