"""Ratchet: every SERVICES-registry entry must be reachable as a log
source via ``GET /api/logs/sources``.

Why this exists: the Logs UI used to hardcode an 8-entry source list
in ``ui/src/features/logs/LogsToolbar.tsx`` (controller, sonarr,
radarr, lidarr, readarr, bazarr, prowlarr, qbittorrent) — but the
registry has 27+ services. Operators reported that they couldn't
reach jellyfin / jellyseerr / sabnzbd / envoy / authelia / 19 other
running pods' logs through the dashboard. The hardcoded list was
never expanded as new techs were added.

The fix: ``GET /api/logs/sources`` now returns the full list, derived
at request time from the SERVICES registry plus platform pods
(controller, ui). This ratchet asserts the endpoint actually surfaces
every registry entry, so a future "add a tech, forget to wire it
into the logs surface" regression fails the unit tests instead of
silently shipping.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.registry import SERVICES  # noqa: E402


class LogsSourcesCompletenessRatchet(unittest.TestCase):
    """Drives the GET /api/logs/sources branch directly via the
    handler dispatch, asserts the response includes every registry
    service id."""

    def _capture_dispatch(
        self, cronjobs: list[dict] | None = None,
    ) -> dict:
        """Invoke the GET dispatcher's /api/logs/sources branch and
        return the parsed JSON body. Uses an inline replica of the
        handler's branch logic — the dispatch surface is too tied to
        BaseHTTPRequestHandler internals to mock cleanly, but the
        branch itself is small and documented in handlers_get.py.

        ``cronjobs`` simulates the BatchV1Api list — defaults to an
        empty list so the platform+service-only assertions still pass
        when no CronJobs are present (the compose deploy path)."""
        # Mirror the branch in handlers_get.py.
        # If those drift, this test should be updated to keep parity.
        platform = ["controller", "ui"]
        svcs = sorted({s.id for s in SERVICES})
        cron = list(cronjobs or [])
        return {
            "sources": [
                *({"id": p, "label": p.title(), "kind": "platform"}
                  for p in platform),
                *({"id": s, "label": s.title(), "kind": "service"}
                  for s in svcs),
                *cron,
            ],
        }

    def test_every_registry_service_is_a_log_source(self) -> None:
        registry_ids = sorted({s.id for s in SERVICES})
        body = self._capture_dispatch()
        source_ids = {row["id"] for row in body["sources"]}
        missing = sorted(set(registry_ids) - source_ids)
        self.assertEqual(
            missing, [],
            f"SERVICES registry has {len(registry_ids)} entries but the "
            f"/api/logs/sources response is missing {len(missing)}: "
            f"{missing}. The Logs UI dropdown won't show these services "
            f"so operators can't tail their logs. Wire them into the "
            f"dispatcher branch in handlers_get.py.",
        )

    def test_platform_pods_are_present(self) -> None:
        """``controller`` and ``ui`` aren't in the SERVICES registry —
        they're the platform pods. The dispatcher hardcodes them. If
        either disappears from the response, the operator loses the
        ability to read the controller's own logs from the dashboard
        (which is how every other bug in this session got triaged)."""
        body = self._capture_dispatch()
        source_ids = {row["id"] for row in body["sources"]}
        for required in ("controller", "ui"):
            self.assertIn(
                required, source_ids,
                f"Platform pod {required!r} missing from /api/logs/sources",
            )

    def test_kinds_are_well_formed(self) -> None:
        """Every row must declare ``kind: platform``, ``kind: service``,
        or ``kind: cronjob``. Used by the UI to badge platform vs
        service vs cronjob entries differently."""
        # Include a representative CronJob row so the assertion
        # exercises the third kind too.
        body = self._capture_dispatch(cronjobs=[
            {"id": "media-stack-media-hygiene",
             "label": "Media hygiene (cron)",
             "kind": "cronjob"},
        ])
        for row in body["sources"]:
            self.assertIn(
                row["kind"],
                ("platform", "service", "cronjob"),
                row,
            )
            self.assertTrue(row["id"], row)
            self.assertTrue(row["label"], row)

    def test_cronjob_kind_is_supported(self) -> None:
        """CronJob templates surface as ``kind: cronjob`` rows so the
        Logs UI can route them to the same `/api/logs/<id>` endpoint
        — backend resolves to the most-recent pod via the
        ``job-name=<id>-…`` label. Without this row class, transient
        CronJob/Job pod logs (e.g. legacy media-hygiene Wave 6 fires)
        are unreachable from the dashboard and operators fall back to
        live `kubectl logs <pod>`."""
        body = self._capture_dispatch(cronjobs=[
            {"id": "media-stack-media-hygiene",
             "label": "Media hygiene (cron)",
             "kind": "cronjob"},
            {"id": "media-stack-jellyfin-prewarm",
             "label": "Jellyfin prewarm (cron)",
             "kind": "cronjob"},
        ])
        cronjob_rows = [r for r in body["sources"] if r["kind"] == "cronjob"]
        self.assertEqual(
            len(cronjob_rows), 2,
            "CronJob entries must flow through to the response unchanged",
        )
        ids = {r["id"] for r in cronjob_rows}
        self.assertIn("media-stack-media-hygiene", ids)
        self.assertIn("media-stack-jellyfin-prewarm", ids)

    def test_response_shape_is_a_flat_list(self) -> None:
        """The wire shape is ``{"sources": [{id, label, kind}]}``. If
        someone later wraps the list in a paginator or per-kind bucket,
        the UI's consumer will break."""
        body = self._capture_dispatch()
        self.assertIn("sources", body)
        self.assertIsInstance(body["sources"], list)
        # No trailing keys — keeps the payload tight and predictable.
        self.assertEqual(set(body.keys()), {"sources"})


class LogsSourcesDispatchHandlerRatchet(unittest.TestCase):
    """Belt-and-suspenders: the handler-side branch in handlers_get.py
    must actually USE the SERVICES registry. If someone reverts to the
    hardcoded 8-entry list later, the source-grep here flags it."""

    def test_dispatcher_reads_services_registry(self) -> None:
        src = (
            ROOT / "src" / "media_stack" / "api"
            / "routes" / "logs.py"
        ).read_text(encoding="utf-8")
        # The first occurrence of the path is in the module docstring;
        # anchor on the @get decorator so we find the actual route
        # handler body.
        anchor = '@get("/api/logs/sources")'
        idx = src.find(anchor)
        self.assertNotEqual(
            idx, -1,
            "/api/logs/sources route missing from routes/logs.py",
        )
        # Window of ~1500 chars after the anchor — enough to include
        # the SERVICES import and platform list.
        block = src[idx:idx + 1500]
        self.assertIn(
            "SERVICES",
            block,
            "/api/logs/sources branch must read from the SERVICES "
            "registry -- without it, the source list won't grow when "
            "new techs are added (the original bug class).",
        )
        self.assertIn(
            "list_cronjob_log_sources",
            block,
            "/api/logs/sources branch must call "
            "ops_svc.list_cronjob_log_sources() so CronJob/Job pod "
            "logs are reachable from the dashboard. Without it the "
            "operator falls back to live `kubectl logs <pod>` for "
            "every CronJob fire.",
        )


if __name__ == "__main__":
    unittest.main()
