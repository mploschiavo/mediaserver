"""Cross-signal consistency tests.

The four health signals (HTTP probe, config integrity, crashloop
classification, auto-heal events) are surfaced separately on the
dashboard. The bug pattern that drove this file: dashboard says
"running" while the underlying service is crashlooping silently
because the HTTP probe is the only signal the banner reads.

These tests pin invariants across the signals — combinations
that must never produce contradictory user-facing output. They
run against the composite ``health_stories`` layer because that's
where the truth-aggregation happens; if any signal disagrees,
the story should reflect the *worst* signal."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.health_stories import compose  # noqa: E402


def _all_health_ok():
    return {
        sid: {"status": "ok"}
        for sid in (
            "prowlarr", "sonarr", "radarr", "lidarr", "readarr",
            "qbittorrent", "sabnzbd", "jellyfin", "jellyseerr",
            "authelia",
        )
    }


class CrossSignalInvariantTests(unittest.TestCase):

    # ------------------------------------------------------------------
    # Invariant 1: HTTP probe ok + config corrupt → not "all healthy"
    #
    # 2026-04-20 root cause: HTTP returned 200 from a stale process
    # while the new pod crashlooped because the on-disk config was
    # corrupt. The dashboard banner can't say "downloads OK" if any
    # signal disagrees.
    # ------------------------------------------------------------------

    def test_http_ok_but_config_corrupt_does_not_emit_downloads_ok(self) -> None:
        health = _all_health_ok()  # everyone reachable
        integrity = {
            "prowlarr": {"status": "corrupt", "file": "/x"},
        }
        crashloops = {}
        stories = compose(
            health=health, integrity=integrity,
            crashloops=crashloops, heal_events=[], now_ts=1000.0,
        )
        ids = {s["id"] for s in stories}
        self.assertNotIn(
            "downloads_ok", ids,
            "Story emits 'downloads_ok' while Prowlarr's config "
            "is corrupt — the green badge would be a lie.",
        )
        self.assertIn(
            "downloads_broken", ids,
            "Corrupt config must surface as 'downloads_broken'.",
        )

    # ------------------------------------------------------------------
    # Invariant 2: HTTP probe ok + crashloop high → not "all healthy"
    # ------------------------------------------------------------------

    def test_http_ok_but_crashlooping_does_not_emit_downloads_ok(self) -> None:
        health = _all_health_ok()
        crashloops = {
            "prowlarr": {
                "cause": "config_xml_corrupt", "healable": True,
                "restart_count": 12,
            },
        }
        # Even with empty integrity, a crashloop classification
        # alone would flag the downloads chain as broken once the
        # rule engine consults it. The current rule keys off
        # integrity OR crashloop — pin both.
        integrity = {"prowlarr": {"status": "ok", "file": "/x"}}
        stories = compose(
            health=health, integrity=integrity,
            crashloops=crashloops, heal_events=[], now_ts=1000.0,
        )
        ids = {s["id"] for s in stories}
        self.assertNotIn("downloads_ok", ids)
        self.assertIn("downloads_broken", ids)

    # ------------------------------------------------------------------
    # Invariant 3: Auto-heal "restored" event → next_action mentions
    # auto-heal so users don't think they need to do something.
    # ------------------------------------------------------------------

    def test_recent_restore_event_changes_next_action(self) -> None:
        now = 1000.0
        health = _all_health_ok()
        health["prowlarr"] = {"status": "error"}
        integrity = {"prowlarr": {"status": "corrupt", "file": "/x"}}
        crashloops = {
            "prowlarr": {"cause": "config_xml_corrupt", "healable": True,
                         "restart_count": 5},
        }
        heal_events = [{
            "service_id": "prowlarr",
            "timestamp": now - 10,
            "action": "restored",
            "restarted": True,
        }]
        stories = compose(
            health=health, integrity=integrity,
            crashloops=crashloops, heal_events=heal_events, now_ts=now,
        )
        downloads = next(s for s in stories if s["id"] == "downloads_broken")
        self.assertIn("Auto-heal", downloads["next_action"])

    # ------------------------------------------------------------------
    # Invariant 4: Auto-heal "skipped_no_snapshot" → next_action says
    # manual recovery is needed (otherwise the user thinks the system
    # has it under control).
    # ------------------------------------------------------------------

    def test_skipped_no_snapshot_event_says_needs_manual(self) -> None:
        now = 1000.0
        health = _all_health_ok()
        health["prowlarr"] = {"status": "error"}
        integrity = {"prowlarr": {"status": "corrupt", "file": "/x"}}
        crashloops = {
            "prowlarr": {"cause": "config_xml_corrupt", "healable": True,
                         "restart_count": 5},
        }
        heal_events = [{
            "service_id": "prowlarr",
            "timestamp": now - 10,
            "action": "skipped_no_snapshot",
            "restarted": False,
        }]
        stories = compose(
            health=health, integrity=integrity,
            crashloops=crashloops, heal_events=heal_events, now_ts=now,
        )
        downloads = next(s for s in stories if s["id"] == "downloads_broken")
        self.assertEqual(downloads["auto_heal_status"], "needs_manual")
        self.assertIn("couldn't fix", downloads["next_action"].lower())

    # ------------------------------------------------------------------
    # Invariant 5: Stale heal event (older than 5 min) is ignored —
    # otherwise an old "healed_recently" badge would lie about
    # current state.
    # ------------------------------------------------------------------

    def test_stale_heal_event_does_not_affect_story(self) -> None:
        now = 1000.0
        old_event = [{
            "service_id": "prowlarr",
            "timestamp": now - 999,  # well over 5 min
            "action": "restored",
            "restarted": True,
        }]
        # Service is now healthy.
        stories = compose(
            health=_all_health_ok(), integrity={}, crashloops={},
            heal_events=old_event, now_ts=now,
        )
        ids = {s["id"] for s in stories}
        self.assertNotIn(
            "auto_heal_active", ids,
            "Stale heal events (>5 min old) must not surface the "
            "'auto_heal_active' info banner — that'd lie about "
            "current state.",
        )

    # ------------------------------------------------------------------
    # Invariant 6: Severity sort is stable and worst-first.
    # ------------------------------------------------------------------

    def test_severity_sort_critical_before_warn_before_info_before_ok(self) -> None:
        health = _all_health_ok()
        # Trigger one of each severity:
        health["prowlarr"] = {"status": "error"}    # critical
        health["jellyseerr"] = {"status": "error"}  # warn
        now = 1000.0
        heal_events = [{
            "service_id": "prowlarr",
            "timestamp": now - 30,
            "action": "restored",
            "restarted": True,
        }]
        stories = compose(
            health=health, integrity={}, crashloops={},
            heal_events=heal_events, now_ts=now,
        )
        order = [s["severity"] for s in stories]
        # Should be a non-decreasing severity rank under our
        # _SEVERITY_ORDER mapping (critical=0, warn=1, info=2, ok=3).
        rank = {"critical": 0, "warn": 1, "info": 2, "ok": 3}
        ranks = [rank[s] for s in order]
        self.assertEqual(ranks, sorted(ranks),
                         f"Stories not sorted worst-first: {order}")

    # ------------------------------------------------------------------
    # Invariant 7: An empty registry produces an empty (or all-ok)
    # response — never a confusing red banner with no services.
    # ------------------------------------------------------------------

    def test_empty_inputs_produces_no_critical_stories(self) -> None:
        stories = compose(
            health={}, integrity={}, crashloops={}, heal_events=[],
            now_ts=1000.0,
        )
        for s in stories:
            self.assertNotEqual(
                s["severity"], "critical",
                "An empty cluster shouldn't produce a critical "
                "banner; nothing's deployed.",
            )


if __name__ == "__main__":
    unittest.main()
