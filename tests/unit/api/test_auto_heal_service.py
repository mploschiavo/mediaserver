"""Round-trip tests for the auto-heal service.

The headline regression: the 2026-04-20 Prowlarr corruption.
A snapshot of the healthy file is taken, the file is corrupted
in place, and the heal cycle restores + restarts.

Other coverage:

- Snapshot is only taken when content changes (not every cycle).
- Pruning keeps only ``keep_per_service`` snapshots per file.
- Throttle prevents repeated heal of the same service inside the
  window — pathological cases (snapshot also bad) won't flood the
  audit log.
- ``CONTROLLER_AUTO_HEAL_ENABLED=false`` short-circuits the heal
  pass.
- "Heal needed but no snapshot exists" reports ``skipped_no_snapshot``
  with a useful explanation, doesn't raise.
- Audit hook is called once per heal event with the right shape."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.auto_heal import (  # noqa: E402
    AutoHealService,
    HealEvent,
    SnapshotStore,
)
from media_stack.api.services.config_integrity import (  # noqa: E402
    ConfigIntegrityService,
)
from media_stack.api.services.crashloop import (  # noqa: E402
    CrashloopClassifier,
)
from media_stack.api.services.registry import ServiceDef  # noqa: E402
from media_stack.api.services.workload_inspector import (  # noqa: E402
    NullWorkloadInspector,
)


_PROWLARR_VALID = (
    b"<Config>\n"
    b"  <Port>9696</Port>\n"
    b"  <UrlBase>/app/prowlarr</UrlBase>\n"
    b"</Config>\n"
)

_PROWLARR_CORRUPT = (
    b"<Config>\n"
    b"  <Port>9696</Port>\n"
    b"  <UrlBase>/app/prowlarr</UrlBase>\n"
    b"</Config>sm>\n"
    b"</Config>\n"
)


def _svc(sid: str, *, cfg: str = "", fmt: str = "") -> ServiceDef:
    return ServiceDef(
        id=sid, name=sid.title(),
        api_key_config=cfg, api_key_format=fmt,
    )


class _Restarter:

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, service_id: str) -> bool:
        self.calls.append(service_id)
        return True


class _AuditCollector:

    def __init__(self) -> None:
        self.events: list[HealEvent] = []

    def __call__(self, event: HealEvent) -> None:
        self.events.append(event)


class AutoHealRoundTripTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "prowlarr").mkdir()
        self.cfg = self.root / "prowlarr" / "config.xml"
        self.cfg.write_bytes(_PROWLARR_VALID)
        self.services = [
            _svc("prowlarr", cfg="prowlarr/config.xml", fmt="xml"),
        ]
        self.restart = _Restarter()
        self.audit = _AuditCollector()

    def _build(self, throttle: int = 0) -> AutoHealService:
        return AutoHealService(
            config_root=self.root,
            services=self.services,
            integrity_svc=ConfigIntegrityService(
                config_root=self.root, services=self.services,
            ),
            classifier=CrashloopClassifier(
                inspector=NullWorkloadInspector(),
                services=self.services,
            ),
            inspector=NullWorkloadInspector(),
            restart_fn=self.restart,
            audit_fn=self.audit,
            throttle_seconds=throttle,
            enabled=True,
        )

    # ------------------------------------------------------------------
    # The headline scenario: corrupt -> restored.
    # ------------------------------------------------------------------

    def test_corrupt_config_is_restored_from_snapshot(self) -> None:
        """Snapshot the healthy file, corrupt the live file, run
        the cycle. The live file must come back to the healthy
        contents and the restart hook must fire."""
        svc = self._build()

        # First cycle: file is healthy → take snapshot, no heal.
        first = svc.run_cycle()
        self.assertEqual(first["snapshots_taken"], 1)
        self.assertEqual(first["heals_performed"], [])
        self.assertEqual(self.restart.calls, [])

        # Corrupt the live file.
        self.cfg.write_bytes(_PROWLARR_CORRUPT)

        # Second cycle: detect corruption, restore from snapshot,
        # restart pod.
        second = svc.run_cycle()
        self.assertEqual(self.cfg.read_bytes(), _PROWLARR_VALID,
                         "Restore did not reach the live file.")
        self.assertEqual(self.restart.calls, ["prowlarr"])
        self.assertEqual(len(second["heals_performed"]), 1)
        evt = second["heals_performed"][0]
        self.assertEqual(evt["service_id"], "prowlarr")
        self.assertEqual(evt["action"], "restored")
        self.assertTrue(evt["restarted"])

    def test_audit_hook_called_with_event(self) -> None:
        svc = self._build()
        svc.run_cycle()  # snapshot
        self.cfg.write_bytes(_PROWLARR_CORRUPT)
        svc.run_cycle()  # heal
        self.assertEqual(len(self.audit.events), 1)
        self.assertEqual(self.audit.events[0].service_id, "prowlarr")
        self.assertEqual(self.audit.events[0].action, "restored")

    # ------------------------------------------------------------------
    # Snapshot dedup
    # ------------------------------------------------------------------

    def test_snapshot_skipped_when_content_unchanged(self) -> None:
        """Running the cycle twice on an unchanged file must not
        produce two snapshots — the dedup is by content hash."""
        svc = self._build()
        first = svc.run_cycle()
        self.assertEqual(first["snapshots_taken"], 1)
        second = svc.run_cycle()
        self.assertEqual(second["snapshots_taken"], 0)

    # ------------------------------------------------------------------
    # No snapshot available
    # ------------------------------------------------------------------

    def test_corrupt_with_no_snapshot_reports_skipped(self) -> None:
        """If the controller has never seen a healthy version,
        we can't restore. The event must say so explicitly so the
        dashboard can surface a manual-fix banner."""
        # Start with a corrupt file — no healthy snapshot to take.
        self.cfg.write_bytes(_PROWLARR_CORRUPT)
        svc = self._build()
        result = svc.run_cycle()
        self.assertEqual(result["snapshots_taken"], 0)
        self.assertEqual(len(result["heals_performed"]), 1)
        evt = result["heals_performed"][0]
        self.assertEqual(evt["action"], "skipped_no_snapshot")
        self.assertFalse(evt["restarted"])
        # Live file untouched.
        self.assertEqual(self.cfg.read_bytes(), _PROWLARR_CORRUPT)

    # ------------------------------------------------------------------
    # Disabled flag
    # ------------------------------------------------------------------

    def test_disabled_short_circuits_heal_but_still_snapshots(self) -> None:
        """A user who disables auto-heal should still benefit from
        snapshots being collected — that way they can manually
        restore later. Only the *act* of healing is skipped."""
        svc = self._build()
        svc.set_enabled(False)
        first = svc.run_cycle()
        self.assertEqual(first["snapshots_taken"], 1)
        self.cfg.write_bytes(_PROWLARR_CORRUPT)
        second = svc.run_cycle()
        self.assertEqual(second["heals_performed"], [])
        self.assertEqual(self.restart.calls, [])

    # ------------------------------------------------------------------
    # Throttle
    # ------------------------------------------------------------------

    def test_throttle_prevents_double_heal_inside_window(self) -> None:
        svc = self._build(throttle=300)
        svc.run_cycle()  # snapshot
        self.cfg.write_bytes(_PROWLARR_CORRUPT)
        first = svc.run_cycle()
        # Re-corrupt and try again immediately.
        self.cfg.write_bytes(_PROWLARR_CORRUPT)
        second = svc.run_cycle()
        self.assertEqual(len(first["heals_performed"]), 1)
        self.assertEqual(len(second["heals_performed"]), 0)
        # Restart fired once, not twice.
        self.assertEqual(self.restart.calls, ["prowlarr"])


class SnapshotStoreTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_prune_keeps_n_per_basename(self) -> None:
        """Different basenames in the same service shouldn't
        crowd each other out of the keep window."""
        store = SnapshotStore(self.root, keep_per_service=2)
        src_a = self.root / "src_a"
        src_a.mkdir()
        src_a_cfg = src_a / "config.xml"
        src_a_other = src_a / "other.xml"
        for body, target in [
            (b"<a>1</a>", src_a_cfg),
            (b"<o>1</o>", src_a_other),
            (b"<a>2</a>", src_a_cfg),
            (b"<o>2</o>", src_a_other),
            (b"<a>3</a>", src_a_cfg),
            (b"<o>3</o>", src_a_other),
        ]:
            target.write_bytes(body)
            store.save_snapshot("svc", target)
            # Tiny pause so timestamps differ on fast filesystems.
            time.sleep(0.01)

        snaps_cfg = store.list_snapshots("svc", basename="config.xml")
        snaps_other = store.list_snapshots("svc", basename="other.xml")
        self.assertEqual(len(snaps_cfg), 2,
                         "config.xml snapshots not capped at keep=2")
        self.assertEqual(len(snaps_other), 2,
                         "other.xml snapshots not capped at keep=2")

    def test_latest_hash_returns_none_when_empty(self) -> None:
        store = SnapshotStore(self.root)
        self.assertIsNone(store.latest_hash("missing", "config.xml"))


if __name__ == "__main__":
    unittest.main()
