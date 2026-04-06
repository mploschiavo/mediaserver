import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.state_store import CheckpointStateStore  # noqa: E402


class CheckpointStateStoreTests(unittest.TestCase):
    def test_mark_and_reload_phase(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            store = CheckpointStateStore(path)
            store.load()
            self.assertFalse(store.is_phase_done("demo"))
            store.mark_phase("demo", "ok", note="done")

            store2 = CheckpointStateStore(path)
            store2.load()
            self.assertTrue(store2.is_phase_done("demo"))
            self.assertEqual(store2.phase_status("demo"), "ok")


if __name__ == "__main__":
    unittest.main()
