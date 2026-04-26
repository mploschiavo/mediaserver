"""Tests for the atomic YAML editor used for users_database.yml edits."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.safe_yaml_edit import SafeYamlEditor, SafeYamlEditError  # noqa: E402


class SafeYamlEditorTests(unittest.TestCase):
    def test_creates_file_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.yml"
            editor = SafeYamlEditor(path)
            editor.edit(lambda _: {"hello": "world"})
            self.assertEqual(yaml.safe_load(path.read_text()), {"hello": "world"})

    def test_atomic_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.yml"
            path.write_text(yaml.safe_dump({"counter": 1}))
            editor = SafeYamlEditor(path)
            editor.edit(lambda d: {**d, "counter": d["counter"] + 1})
            self.assertEqual(yaml.safe_load(path.read_text()), {"counter": 2})

    def test_backup_created_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.yml"
            path.write_text(yaml.safe_dump({"v": 1}))
            editor = SafeYamlEditor(path)
            editor.edit(lambda d: {**d, "v": 2})
            backups = list((path.parent / "backups").glob(f"{path.name}.*"))
            self.assertEqual(len(backups), 1)

    def test_mutator_exception_leaves_file_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.yml"
            path.write_text(yaml.safe_dump({"v": 1}))
            editor = SafeYamlEditor(path)
            def _bad(_):
                raise RuntimeError("mutator broke")
            with self.assertRaises(SafeYamlEditError):
                editor.edit(_bad)
            self.assertEqual(yaml.safe_load(path.read_text()), {"v": 1})

    def test_validator_rejects_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.yml"
            path.write_text(yaml.safe_dump({"v": 1}))
            def _validator(data):
                if data.get("v", 0) < 0:
                    raise ValueError("v must be >=0")
            editor = SafeYamlEditor(path, validator=_validator)
            with self.assertRaises(SafeYamlEditError):
                editor.edit(lambda d: {"v": -1})
            # File unchanged
            self.assertEqual(yaml.safe_load(path.read_text()), {"v": 1})

    def test_mutator_returning_non_dict_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.yml"
            editor = SafeYamlEditor(path)
            with self.assertRaises(SafeYamlEditError):
                editor.edit(lambda _: ["not", "a", "dict"])


if __name__ == "__main__":
    unittest.main()
