"""Tests for the composite health stories rule engine.

We build the four input dicts directly (no live services) so each
test is one scenario in one place. The rules are pure — same
inputs give same outputs — so this is the right boundary for
fast tests.

Coverage:

- The 2026-04-20 Prowlarr scenario produces a "downloads broken"
  critical story whose ``next_action`` says auto-heal is in
  progress when a recent restore event exists.
- All-green produces ``ok`` stories for downloads, playback,
  search, auth.
- Severity sort puts critical first.
- Missing services don't fire rules (e.g., no playback story
  when the user runs without Jellyfin/Plex).
- A buggy rule doesn't break the rest of the engine."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.health_stories import compose  # noqa: E402


def _ok(*svc_ids):
    return {sid: {"status": "ok"} for sid in svc_ids}


def _err(*svc_ids):
    return {sid: {"status": "error"} for sid in svc_ids}


def _all_apps_health():
    """Healthy state for every app the rules check, so tests
    that flip *one* signal don't accidentally trigger another
    rule's failure path."""
    apps = [
        "prowlarr", "sonarr", "radarr", "lidarr", "readarr",
        "qbittorrent", "sabnzbd", "jellyfin", "jellyseerr",
        "authelia",
    ]
    return _ok(*apps)


class HealthStoryTests(unittest.TestCase):

    # ------------------------------------------------------------------
    # The headline regression
    # ------------------------------------------------------------------

    def test_prowlarr_corrupt_produces_downloads_broken_story(self) -> None:
        health = _all_apps_health()
        # The HTTP probe will report Prowlarr as down (it can't
        # bind on :9696 with a corrupt config) — both signals
        # converge on the same root cause.
        health["prowlarr"] = {"status": "error"}
        integrity = {
            "prowlarr": {
                "status": "corrupt",
                "reason": "XML parse error: line 17",
                "file": "/srv-config/prowlarr/config.xml",
            },
        }
        crashloops = {
            "prowlarr": {
                "cause": "config_xml_corrupt", "healable": True,
                "restart_count": 37,
            },
        }
        heal_events: list[dict] = []  # not yet healed
        stories = compose(
            health=health, integrity=integrity,
            crashloops=crashloops, heal_events=heal_events,
            now_ts=1000.0,
        )
        # First story (sorted by severity) is the critical one.
        first = stories[0]
        self.assertEqual(first["id"], "downloads_broken")
        self.assertEqual(first["severity"], "critical")
        self.assertIn("prowlarr", first["affected_services"])
        self.assertIn("config", first["headline"].lower())

    def test_recent_restore_changes_next_action_to_healing(self) -> None:
        """When a heal event for the affected service exists in
        the recent window, the story's ``next_action`` must say
        auto-heal is taking care of it."""
        health = _all_apps_health()
        health["prowlarr"] = {"status": "error"}
        integrity = {
            "prowlarr": {"status": "corrupt", "file": "/x"},
        }
        crashloops = {
            "prowlarr": {"cause": "config_xml_corrupt", "healable": True,
                         "restart_count": 5},
        }
        now = 1000.0
        heal_events = [{
            "service_id": "prowlarr",
            "timestamp": now - 30,
            "action": "restored",
            "restarted": True,
        }]
        stories = compose(
            health=health, integrity=integrity,
            crashloops=crashloops, heal_events=heal_events,
            now_ts=now,
        )
        downloads = next(
            s for s in stories if s["id"] == "downloads_broken"
        )
        self.assertEqual(downloads["auto_heal_status"], "healed_recently")
        self.assertIn("Auto-heal", downloads["next_action"])

    # ------------------------------------------------------------------
    # All-green
    # ------------------------------------------------------------------

    def test_all_healthy_produces_ok_stories(self) -> None:
        stories = compose(
            health=_all_apps_health(),
            integrity={},
            crashloops={},
            heal_events=[],
            now_ts=1000.0,
        )
        ids = {s["id"] for s in stories}
        self.assertIn("downloads_ok", ids)
        self.assertIn("playback_ok", ids)
        self.assertIn("auth_ok", ids)
        self.assertIn("search_ok", ids)
        # Severity sort: ok stories come last.
        self.assertEqual(stories[-1]["severity"], "ok")

    # ------------------------------------------------------------------
    # Sort order
    # ------------------------------------------------------------------

    def test_critical_sorts_before_warn_before_info(self) -> None:
        health = _all_apps_health()
        health["prowlarr"] = {"status": "error"}    # critical: downloads
        health["jellyseerr"] = {"status": "error"}  # warn: search
        # Plus a recent heal event → info story fires too.
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
        severities = [s["severity"] for s in stories]
        crit_idx = severities.index("critical")
        warn_idx = severities.index("warn")
        info_idx = severities.index("info")
        self.assertLess(crit_idx, warn_idx)
        self.assertLess(warn_idx, info_idx)

    # ------------------------------------------------------------------
    # Service not deployed
    # ------------------------------------------------------------------

    def test_no_playback_app_means_no_playback_story(self) -> None:
        """A user with no Jellyfin or Plex shouldn't see a
        "playback" row at all."""
        # Health dict doesn't include jellyfin or plex.
        health = {
            "prowlarr": {"status": "ok"}, "sonarr": {"status": "ok"},
            "radarr": {"status": "ok"}, "qbittorrent": {"status": "ok"},
        }
        stories = compose(
            health=health, integrity={}, crashloops={},
            heal_events=[], now_ts=1000.0,
        )
        ids = {s["id"] for s in stories}
        self.assertNotIn("playback_ok", ids)
        self.assertNotIn("playback_broken", ids)

    # ------------------------------------------------------------------
    # qBit + SAB both down
    # ------------------------------------------------------------------

    def test_both_download_clients_down_fires_downloads_broken(self) -> None:
        health = _all_apps_health()
        health.update(_err("qbittorrent", "sabnzbd"))
        stories = compose(
            health=health, integrity={}, crashloops={},
            heal_events=[], now_ts=1000.0,
        )
        downloads = next(
            (s for s in stories if s["id"] == "downloads_broken"),
            None,
        )
        self.assertIsNotNone(downloads)
        self.assertIn("qbittorrent", downloads["affected_services"])
        self.assertIn("sabnzbd", downloads["affected_services"])

    def test_only_one_download_client_down_does_not_fire(self) -> None:
        """If qBit is up, SAB being down doesn't break downloads —
        usenet just isn't usable. We don't want a critical banner
        for a degraded-but-working state."""
        health = _all_apps_health()
        health["sabnzbd"] = {"status": "error"}
        stories = compose(
            health=health, integrity={}, crashloops={},
            heal_events=[], now_ts=1000.0,
        )
        ids = {s["id"] for s in stories}
        self.assertIn("downloads_ok", ids)
        self.assertNotIn("downloads_broken", ids)

    # ------------------------------------------------------------------
    # Resilience
    # ------------------------------------------------------------------

    def test_api_keys_missing_rule_fires_when_discovery_left_services_empty(self) -> None:
        """v1.0.181: ``LibraryStatsTiles`` showed 1 of each because the
        K8s Secret had every API key as empty string. Without this
        rule the operator sees an apparently-healthy stack with zero
        content. The rule must fire when ``services_missing_keys()``
        returns a non-empty list, regardless of the other signals."""
        from unittest import mock as _mock
        with _mock.patch(
            "media_stack.api.services.runtime_keys.services_missing_keys",
            return_value=["sonarr", "radarr"],
        ):
            stories = compose(
                health=_all_apps_health(), integrity={}, crashloops={},
                heal_events=[], now_ts=1000.0,
            )
        story = next(
            (s for s in stories if s["id"] == "api_keys_missing"),
            None,
        )
        self.assertIsNotNone(story, "api_keys_missing rule did not fire")
        self.assertEqual(story["severity"], "warn")
        self.assertEqual(story["affected_services"], ["radarr", "sonarr"])

    def test_api_keys_missing_rule_does_not_fire_when_all_keys_present(self) -> None:
        from unittest import mock as _mock
        with _mock.patch(
            "media_stack.api.services.runtime_keys.services_missing_keys",
            return_value=[],
        ):
            stories = compose(
                health=_all_apps_health(), integrity={}, crashloops={},
                heal_events=[], now_ts=1000.0,
            )
        ids = {s["id"] for s in stories}
        self.assertNotIn("api_keys_missing", ids)

    def test_buggy_rule_does_not_break_the_engine(self) -> None:
        """If one rule raises, the others still run. We patch a
        rule to always raise."""
        from media_stack.api.services import health_stories as hs
        original = list(hs._RULES)

        def boom(**kwargs):
            raise RuntimeError("intentional")

        try:
            hs._RULES.insert(0, boom)
            stories = compose(
                health=_all_apps_health(),
                integrity={}, crashloops={}, heal_events=[],
                now_ts=1000.0,
            )
            # Other rules still produce results.
            self.assertTrue(any(
                s["id"] == "downloads_ok" for s in stories
            ))
        finally:
            hs._RULES[:] = original


if __name__ == "__main__":
    unittest.main()
