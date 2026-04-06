import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.adapters.defaults import load_json_default


class BootstrapDefaultsTests(unittest.TestCase):
    def test_load_json_default_raises_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            defaults_dir = Path(tmp)
            with self.assertRaises(FileNotFoundError):
                load_json_default(defaults_dir, "missing.json", {"enabled": True})

    def test_load_json_default_reads_json_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            defaults_dir = Path(tmp)
            target = defaults_dir / "sample.json"
            target.write_text(json.dumps({"name": "demo", "count": 3}), encoding="utf-8")
            loaded = load_json_default(defaults_dir, "sample.json", {"name": "fallback"})
            self.assertEqual(loaded, {"name": "demo", "count": 3})

    def test_repo_maintainerr_default_is_valid(self):
        repo_root = Path(__file__).resolve().parents[2]
        defaults_dir = repo_root / "src" / "media_stack" / "contracts"
        loaded = load_json_default(defaults_dir, "maintainerr_policy.json", {})
        self.assertIsInstance(loaded, dict)
        self.assertEqual(loaded.get("version"), 1)
        self.assertIsInstance(loaded.get("rules"), list)

    def test_repo_maintainerr_rule_library_defaults_are_valid(self):
        repo_root = Path(__file__).resolve().parents[2]
        rules_dir = repo_root / "src" / "media_stack" / "contracts" / "maintainerr_rules"
        files = sorted(rules_dir.glob("*.json"))
        if not files:
            files = sorted((rules_dir / "json").glob("*.json"))
        self.assertGreaterEqual(len(files), 5)
        for rule_file in files:
            raw = json.loads(rule_file.read_text(encoding="utf-8"))
            if "rule" in raw:
                self.assertIsInstance(raw.get("rule"), dict, msg=f"invalid rule in {rule_file}")
            else:
                self.assertIsInstance(raw, dict, msg=f"invalid object in {rule_file}")


if __name__ == "__main__":
    unittest.main()
