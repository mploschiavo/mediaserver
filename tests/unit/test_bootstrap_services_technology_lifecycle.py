import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.technology_lifecycle_service import (  # noqa: E402
    TechnologyLifecycle,
    TechnologyLifecycleManager,
)


class TechnologyLifecycleTests(unittest.TestCase):
    def test_manager_runs_phases_and_updates_state(self):
        calls = []

        def _record(phase):
            def _inner(_runtime, state):
                calls.append((phase, state.key))
                return None

            return _inner

        lifecycle = TechnologyLifecycle(
            key="jellyfin",
            load_fn=_record("load"),
            precheck_fn=_record("precheck"),
            prepare_fn=_record("prepare"),
            configure_fn=_record("configure"),
            ensure_fn=_record("ensure"),
            clean_hygiene_fn=_record("clean_hygiene"),
            status_fn=lambda _runtime, _state: {"health": "ok"},
        )
        manager = TechnologyLifecycleManager(lifecycles={"jellyfin": lifecycle})

        for phase in (
            "load",
            "precheck",
            "prepare",
            "configure",
            "ensure",
            "status",
            "clean_hygiene",
        ):
            manager.run_phase(phase, runtime={})

        state = manager.state("jellyfin")
        assert state is not None
        self.assertTrue(state.loaded)
        self.assertTrue(state.prechecked)
        self.assertTrue(state.prepared)
        self.assertTrue(state.configured)
        self.assertTrue(state.ensured)
        self.assertTrue(state.hygiene_cleaned)
        self.assertEqual(state.status, "ok")
        self.assertEqual(state.details.get("health"), "ok")
        self.assertEqual(
            calls,
            [
                ("load", "jellyfin"),
                ("precheck", "jellyfin"),
                ("prepare", "jellyfin"),
                ("configure", "jellyfin"),
                ("ensure", "jellyfin"),
                ("clean_hygiene", "jellyfin"),
            ],
        )

    def test_phase_error_marks_state(self):
        lifecycle = TechnologyLifecycle(
            key="readarr",
            ensure_fn=lambda _runtime, _state: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        manager = TechnologyLifecycleManager(lifecycles={"readarr": lifecycle})

        with self.assertRaises(RuntimeError):
            manager.run_phase("ensure", runtime={})

        state = manager.state("readarr")
        assert state is not None
        self.assertEqual(state.status, "error")
        self.assertTrue(any("ensure:boom" in err for err in state.errors))


if __name__ == "__main__":
    unittest.main()
