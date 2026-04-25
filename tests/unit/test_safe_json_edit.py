"""Tests for the atomic JSON editor used for controller-side state files."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.safe_json_edit import (  # noqa: E402
    SafeJsonEditError,
    SafeJsonEditor,
)


class SafeJsonEditorTests(unittest.TestCase):
    def test_creates_file_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            editor = SafeJsonEditor(path)
            editor.edit(lambda _: {"hello": "world"})
            self.assertEqual(json.loads(path.read_text()), {"hello": "world"})

    def test_atomic_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text(json.dumps({"counter": 1}))
            editor = SafeJsonEditor(path)
            editor.edit(lambda d: {**d, "counter": d["counter"] + 1})
            self.assertEqual(json.loads(path.read_text()), {"counter": 2})

    def test_read_empty_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text("")
            editor = SafeJsonEditor(path)
            self.assertEqual(editor.read(), {})

    def test_read_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            editor = SafeJsonEditor(path)
            self.assertEqual(editor.read(), {})

    def test_read_malformed_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text("{not json")
            editor = SafeJsonEditor(path)
            with self.assertRaises(SafeJsonEditError):
                editor.read()

    def test_read_non_object_top_level_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text("[1, 2, 3]")
            editor = SafeJsonEditor(path)
            with self.assertRaises(SafeJsonEditError):
                editor.read()

    def test_backup_created_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text(json.dumps({"v": 1}))
            editor = SafeJsonEditor(path)
            editor.edit(lambda d: {**d, "v": 2})
            backups = list((path.parent / "backups").glob(f"{path.name}.*"))
            self.assertEqual(len(backups), 1)

    def test_mutator_exception_leaves_file_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text(json.dumps({"v": 1}))
            editor = SafeJsonEditor(path)

            def _bad(_):
                raise RuntimeError("mutator broke")

            with self.assertRaises(SafeJsonEditError):
                editor.edit(_bad)
            self.assertEqual(json.loads(path.read_text()), {"v": 1})

    def test_validator_rejects_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text(json.dumps({"v": 1}))

            def _validator(data):
                if data.get("v", 0) < 0:
                    raise ValueError("v must be >=0")

            editor = SafeJsonEditor(path, validator=_validator)
            with self.assertRaises(SafeJsonEditError):
                editor.edit(lambda d: {"v": -1})
            self.assertEqual(json.loads(path.read_text()), {"v": 1})

    def test_mutator_returning_non_dict_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            editor = SafeJsonEditor(path)
            with self.assertRaises(SafeJsonEditError):
                editor.edit(lambda _: ["not", "a", "dict"])

    def test_mutator_returning_none_becomes_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            editor = SafeJsonEditor(path)
            editor.edit(lambda _: None)
            self.assertEqual(json.loads(path.read_text()), {})

    def test_non_serializable_value_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            editor = SafeJsonEditor(path)
            with self.assertRaises(SafeJsonEditError):
                editor.edit(lambda _: {"bad": object()})

    def test_atomic_write_crash_cleans_temp_and_preserves_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text(json.dumps({"v": 1}))
            editor = SafeJsonEditor(path)
            with mock.patch(
                "media_stack.core.auth.users.safe_json_edit.os.replace",
                side_effect=OSError("boom"),
            ):
                with self.assertRaises(SafeJsonEditError):
                    editor.edit(lambda d: {**d, "v": 2})
            # original file intact
            self.assertEqual(json.loads(path.read_text()), {"v": 1})
            # no leftover temp file
            leftovers = [
                p
                for p in path.parent.iterdir()
                if p.name.startswith(path.name + ".") and p.name.endswith(".tmp")
            ]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
