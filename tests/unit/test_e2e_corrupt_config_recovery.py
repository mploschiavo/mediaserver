"""End-to-end regression for the 2026-04-20 Prowlarr crashloop.

The original failure: Prowlarr's ``config.xml`` ended up with
trailing junk after the closing ``</Config>``. The pod
crashlooped silently for hours; the dashboard showed a generic
"unhealthy" badge with no actionable cause; nothing could heal
it without manual ``kubectl exec``.

This test wires the four new components (config_integrity,
crashloop classifier, auto_heal, health_stories) together
against a realistic on-disk layout and a fake workload
inspector that returns a high restart count + the actual
Prowlarr log line. It walks through:

1. Snapshot pass on a healthy file.
2. Inject the real corruption byte sequence.
3. Confirm the integrity probe reports ``corrupt``.
4. Confirm the crashloop classifier reports
   ``cause=config_xml_corrupt`` and ``healable=True``.
5. Confirm the composite story layer emits a critical
   ``downloads_broken`` story.
6. Run the auto-heal cycle.
7. Confirm: file restored, restart fired, audit hook called,
   integrity goes back to ``ok``, story flips to ``ok``."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.auto_heal import (  # noqa: E402
    AutoHealService, HealEvent,
)
from media_stack.api.services.config_integrity import (  # noqa: E402
    ConfigIntegrityService,
)
from media_stack.api.services.crashloop import (  # noqa: E402
    CrashloopClassifier,
)
from media_stack.api.services.health_stories import compose  # noqa: E402
from media_stack.api.services.registry import ServiceDef  # noqa: E402
from media_stack.api.services.workload_inspector import (  # noqa: E402
    WorkloadState,
)


_PROWLARR_VALID = (
    b"<Config>\n"
    b"  <Port>9696</Port>\n"
    b"  <UrlBase>/app/prowlarr</UrlBase>\n"
    b"  <ApiKey>e38192d22f4b4f9490e061196b5cdd2a</ApiKey>\n"
    b"</Config>\n"
)

# Real artifact captured 2026-04-20 from prowlarr-56bfdb5666-lwndb.
_PROWLARR_CORRUPT = (
    b"<Config>\n"
    b"  <Port>9696</Port>\n"
    b"  <UrlBase>/app/prowlarr</UrlBase>\n"
    b"  <ApiKey>e38192d22f4b4f9490e061196b5cdd2a</ApiKey>\n"
    b"</Config>sm>\n"
    b"</Config>\n"
)

# Snippet from the real prowlarr.txt log around the crash.
_PROWLARR_LOG = (
    "[2026-04-20 13:55] Application starting\n"
    "/config/config.xml:17.10: Extra content at the end of the document\n"
    "</Config>sm>\n"
    "         ^\n"
    "Application failed to start: System.Xml.XmlException\n"
)


class _FakeInspector:
    """Returns the canonical 'high restart count + Prowlarr log
    line' state for service 'prowlarr', anything else is healthy."""

    def __init__(self) -> None:
        self.restart_calls: list[str] = []

    def list_workloads(self, service_ids):
        out = {}
        for sid in service_ids:
            if sid == "prowlarr":
                out[sid] = WorkloadState(
                    service_id="prowlarr", running=False,
                    restart_count=37,
                    last_terminated_reason="Error",
                    last_terminated_exit_code=1,
                )
            else:
                out[sid] = WorkloadState(sid, True, 0, "", -1)
        return out

    def previous_logs(self, service_id, *, tail_lines=200):
        return _PROWLARR_LOG if service_id == "prowlarr" else ""


class E2ECorruptConfigRecoveryTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_root = Path(self._tmp.name)
        (self.config_root / "prowlarr").mkdir()
        self.cfg = self.config_root / "prowlarr" / "config.xml"
        self.cfg.write_bytes(_PROWLARR_VALID)

        # Realistic-ish service registry — Prowlarr plus a couple
        # of *arrs plus qBit so the stories layer has the right
        # peers to evaluate.
        self.services = [
            ServiceDef(id="prowlarr", name="Prowlarr",
                       api_key_config="prowlarr/config.xml",
                       api_key_format="xml"),
            ServiceDef(id="sonarr", name="Sonarr"),
            ServiceDef(id="radarr", name="Radarr"),
            ServiceDef(id="qbittorrent", name="qBittorrent"),
        ]

        self.integrity = ConfigIntegrityService(
            config_root=self.config_root, services=self.services,
        )
        self.inspector = _FakeInspector()
        self.classifier = CrashloopClassifier(
            inspector=self.inspector, services=self.services,
        )

        self.audit_events: list[HealEvent] = []

        def restart(service_id):
            self.inspector.restart_calls.append(service_id)
            return True

        self.heal = AutoHealService(
            config_root=self.config_root,
            services=self.services,
            integrity_svc=self.integrity,
            classifier=self.classifier,
            inspector=self.inspector,
            restart_fn=restart,
            audit_fn=self.audit_events.append,
            throttle_seconds=0,
            enabled=True,
        )

    def test_full_recovery_pipeline(self) -> None:
        # ----- 1. Healthy: cycle takes a snapshot ----------------------
        first = self.heal.run_cycle()
        self.assertEqual(first["snapshots_taken"], 1,
                         "First cycle should snapshot the healthy file.")
        self.assertEqual(first["heals_performed"], [])

        # ----- 2. Inject corruption ------------------------------------
        self.cfg.write_bytes(_PROWLARR_CORRUPT)

        # ----- 3. Integrity probe reports corrupt ----------------------
        integrity_result = self.integrity.check_all()
        self.assertEqual(integrity_result["prowlarr"]["status"], "corrupt")
        self.assertIn("XML parse error",
                      integrity_result["prowlarr"]["reason"])

        # ----- 4. Crashloop classifier names the cause -----------------
        crash_result = self.classifier.check_all()
        self.assertEqual(crash_result["prowlarr"]["cause"],
                         "config_xml_corrupt")
        self.assertTrue(crash_result["prowlarr"]["healable"])

        # ----- 5. Composite story layer surfaces "downloads broken" ----
        # Synthesise a health probe — Prowlarr is unreachable
        # (port 9696 not bound), peers are fine.
        health_synthetic = {
            "prowlarr": {"status": "error"},
            "sonarr": {"status": "ok"},
            "radarr": {"status": "ok"},
            "qbittorrent": {"status": "ok"},
        }
        stories_before = compose(
            health=health_synthetic,
            integrity=integrity_result,
            crashloops=crash_result,
            heal_events=[],
            now_ts=time.time(),
        )
        critical = [s for s in stories_before if s["severity"] == "critical"]
        self.assertTrue(critical,
                        "Expected at least one critical story.")
        self.assertEqual(critical[0]["id"], "downloads_broken")
        self.assertIn("prowlarr", critical[0]["affected_services"])

        # ----- 6. Auto-heal cycle restores + restarts ------------------
        second = self.heal.run_cycle()
        self.assertEqual(len(second["heals_performed"]), 1)
        evt = second["heals_performed"][0]
        self.assertEqual(evt["action"], "restored")
        self.assertTrue(evt["restarted"])
        self.assertEqual(self.inspector.restart_calls, ["prowlarr"])
        # File is back to the healthy bytes.
        self.assertEqual(self.cfg.read_bytes(), _PROWLARR_VALID)
        # Audit hook was called once with the heal event.
        self.assertEqual(len(self.audit_events), 1)
        self.assertEqual(self.audit_events[0].service_id, "prowlarr")

        # ----- 7. Integrity flips back to ok; story green --------------
        integrity_after = self.integrity.check_all()
        self.assertEqual(integrity_after["prowlarr"]["status"], "ok")

        # In a real deployment the pod takes ~30s to restart and
        # bind :9696; for the synthetic story input we set health
        # back to ok to confirm the story layer gets out of the
        # critical state once the underlying signals recover.
        health_after = dict(health_synthetic)
        health_after["prowlarr"] = {"status": "ok"}
        stories_after = compose(
            health=health_after,
            integrity=integrity_after,
            crashloops={
                # Crashloop classifier may still see history but we
                # only care that integrity & health are green.
                "prowlarr": {"cause": "healthy", "healable": False,
                             "restart_count": 37},
            },
            heal_events=[evt],
            now_ts=time.time(),
        )
        downloads = next(
            s for s in stories_after if s["id"].startswith("downloads_")
        )
        self.assertEqual(downloads["severity"], "ok")


if __name__ == "__main__":
    unittest.main()
