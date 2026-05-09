"""Tests for the crashloop classifier.

Coverage targets:

- The 2026-04-20 Prowlarr signature (``Extra content at the end
  of the document``) maps to ``cause='config_xml_corrupt'`` with
  ``healable=True``.
- Pods below the restart threshold report ``cause='healthy'`` and
  do **not** trigger a log fetch (cheap path; expensive log calls
  in the hot loop would brown out the inspector).
- ``OOMKilled`` is detected from the runtime state without
  consulting logs.
- Unknown logs return ``cause='unclassified'``, never raise.
- A platform with no SDK still returns a valid response (Null
  inspector path).
- The signature library is ordered: more-specific signatures hit
  before generic ones."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.crashloop import (  # noqa: E402
    CrashloopClassifier,
)
from media_stack.core.service_registry.registry import ServiceDef  # noqa: E402
from media_stack.api.services.workload_inspector import (  # noqa: E402
    NullWorkloadInspector,
    WorkloadState,
)


def _svc(sid: str) -> ServiceDef:
    return ServiceDef(id=sid, name=sid.title())


class _FakeInspector:
    """Test double — returns canned states + logs per service id."""

    def __init__(
        self,
        states: dict[str, WorkloadState],
        logs: dict[str, str] | None = None,
    ) -> None:
        self._states = states
        self._logs = logs or {}
        self.previous_logs_calls: list[str] = []

    def list_workloads(self, service_ids):
        return {
            sid: self._states.get(
                sid,
                WorkloadState(sid, False, 0, "", -1),
            )
            for sid in service_ids
        }

    def previous_logs(self, service_id, *, tail_lines=200):
        self.previous_logs_calls.append(service_id)
        return self._logs.get(service_id, "")


class CrashloopClassifierTests(unittest.TestCase):

    # ------------------------------------------------------------------
    # The headline regression: 2026-04-20 Prowlarr.
    # ------------------------------------------------------------------

    def test_prowlarr_xml_corrupt_signature_classified_and_healable(self) -> None:
        """The classifier must turn the real Prowlarr log line
        into ``config_xml_corrupt`` so auto-heal picks it up."""
        prowlarr_log = (
            "[2026-04-20 13:55] starting Prowlarr...\n"
            "/config/config.xml:17.10: Extra content at the end of the document\n"
            "</Config>sm>\n"
        )
        inspector = _FakeInspector(
            states={"prowlarr": WorkloadState("prowlarr", False, 37, "", 1)},
            logs={"prowlarr": prowlarr_log},
        )
        classifier = CrashloopClassifier(
            inspector=inspector, services=[_svc("prowlarr")],
        )
        result = classifier.check_service("prowlarr")
        self.assertEqual(result["cause"], "config_xml_corrupt")
        self.assertTrue(result["healable"])
        self.assertEqual(result["restart_count"], 37)
        self.assertIn("Extra content", result["sample_log_line"])

    # ------------------------------------------------------------------
    # Cheap path
    # ------------------------------------------------------------------

    def test_below_threshold_returns_healthy_without_fetching_logs(self) -> None:
        inspector = _FakeInspector(
            states={"sonarr": WorkloadState("sonarr", True, 1, "", -1)},
            logs={"sonarr": "Extra content at the end of the document"},
        )
        classifier = CrashloopClassifier(
            inspector=inspector, services=[_svc("sonarr")],
            restart_threshold=3,
        )
        result = classifier.check_service("sonarr")
        self.assertEqual(result["cause"], "healthy")
        self.assertEqual(
            inspector.previous_logs_calls, [],
            "Healthy services must not trigger a log fetch.",
        )

    # ------------------------------------------------------------------
    # OOMKilled is detected from runtime state, not logs
    # ------------------------------------------------------------------

    def test_oomkilled_classified_without_log_fetch(self) -> None:
        inspector = _FakeInspector(
            states={
                "jellyfin": WorkloadState(
                    "jellyfin", False, 5, "OOMKilled", 137,
                ),
            },
        )
        classifier = CrashloopClassifier(
            inspector=inspector, services=[_svc("jellyfin")],
        )
        result = classifier.check_service("jellyfin")
        self.assertEqual(result["cause"], "out_of_memory")
        self.assertEqual(
            inspector.previous_logs_calls, [],
            "OOMKilled is from kubelet — no need to read logs.",
        )

    # ------------------------------------------------------------------
    # Other signatures
    # ------------------------------------------------------------------

    def test_database_locked_signature(self) -> None:
        inspector = _FakeInspector(
            states={"radarr": WorkloadState("radarr", False, 5, "", 1)},
            logs={"radarr": "OperationalError: database is locked"},
        )
        classifier = CrashloopClassifier(
            inspector=inspector, services=[_svc("radarr")],
        )
        result = classifier.check_service("radarr")
        self.assertEqual(result["cause"], "database_locked")
        self.assertFalse(result["healable"])

    def test_port_in_use_signature(self) -> None:
        inspector = _FakeInspector(
            states={"sonarr": WorkloadState("sonarr", False, 5, "", 1)},
            logs={"sonarr": "Bind: address already in use"},
        )
        classifier = CrashloopClassifier(
            inspector=inspector, services=[_svc("sonarr")],
        )
        result = classifier.check_service("sonarr")
        self.assertEqual(result["cause"], "port_in_use")

    def test_perm_denied_signature_is_healable(self) -> None:
        """A permission error is healable — we can chmod or restore
        from snapshot. Pin healable=True."""
        inspector = _FakeInspector(
            states={"qbittorrent": WorkloadState("qbittorrent", False, 5, "", 1)},
            logs={"qbittorrent": "open /config/qBittorrent/qBittorrent.conf: permission denied"},
        )
        classifier = CrashloopClassifier(
            inspector=inspector, services=[_svc("qbittorrent")],
        )
        result = classifier.check_service("qbittorrent")
        self.assertEqual(result["cause"], "perm_denied")
        self.assertTrue(result["healable"])

    def test_specific_signature_wins_over_generic_fatal(self) -> None:
        """``Extra content at the end of the document`` plus
        ``FATAL`` in the same log: must classify as XML corrupt
        (specific) not fatal (generic)."""
        inspector = _FakeInspector(
            states={"prowlarr": WorkloadState("prowlarr", False, 5, "", 1)},
            logs={"prowlarr": "FATAL: startup\nExtra content at the end of the document"},
        )
        classifier = CrashloopClassifier(
            inspector=inspector, services=[_svc("prowlarr")],
        )
        result = classifier.check_service("prowlarr")
        self.assertEqual(result["cause"], "config_xml_corrupt")

    # ------------------------------------------------------------------
    # Unknown / fallback paths
    # ------------------------------------------------------------------

    def test_unknown_log_returns_unclassified(self) -> None:
        inspector = _FakeInspector(
            states={"sonarr": WorkloadState("sonarr", False, 5, "", 1)},
            logs={"sonarr": "Something completely unexpected happened\n"},
        )
        classifier = CrashloopClassifier(
            inspector=inspector, services=[_svc("sonarr")],
        )
        result = classifier.check_service("sonarr")
        self.assertEqual(result["cause"], "unclassified")
        self.assertFalse(result["healable"])

    def test_no_logs_available_returns_unclassified(self) -> None:
        inspector = _FakeInspector(
            states={"sonarr": WorkloadState("sonarr", False, 5, "", 1)},
            logs={},  # empty -> previous_logs returns ""
        )
        classifier = CrashloopClassifier(
            inspector=inspector, services=[_svc("sonarr")],
        )
        result = classifier.check_service("sonarr")
        self.assertEqual(result["cause"], "unclassified")
        self.assertIn("no previous log", result["description"])

    def test_null_inspector_returns_healthy_for_unknown_pods(self) -> None:
        """No SDK initialised — every service reports healthy with
        zero restarts. Dashboard shows neutral, no false alarms."""
        classifier = CrashloopClassifier(
            inspector=NullWorkloadInspector(),
            services=[_svc("prowlarr"), _svc("qbittorrent")],
        )
        results = classifier.check_all()
        for r in results.values():
            self.assertEqual(r["cause"], "healthy")
            self.assertEqual(r["restart_count"], 0)

    # ------------------------------------------------------------------
    # check_all
    # ------------------------------------------------------------------

    def test_check_all_returns_one_entry_per_service(self) -> None:
        inspector = _FakeInspector(
            states={
                "a": WorkloadState("a", True, 1, "", -1),
                "b": WorkloadState("b", False, 5, "", 1),
            },
            logs={"b": "Extra content at the end of the document"},
        )
        classifier = CrashloopClassifier(
            inspector=inspector, services=[_svc("a"), _svc("b")],
        )
        results = classifier.check_all()
        self.assertEqual(set(results.keys()), {"a", "b"})
        self.assertEqual(results["a"]["cause"], "healthy")
        self.assertEqual(results["b"]["cause"], "config_xml_corrupt")


if __name__ == "__main__":
    unittest.main()
