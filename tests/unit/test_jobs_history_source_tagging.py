"""Tests for v1.0.180 history-source tagging.

The UI's persona-questions agent reads ``GET /api/jobs.history[]``
and renders one of three badges per row: "ran via cron",
"operator-triggered", or "auto-heal". To do that, every history
entry needs a ``source`` field. These tests pin the contract:

* The writer (``_record_history``) accepts ``source`` / ``actor``
  kwargs and stamps them on the entry (default ``"unknown"``).
* The reader (``get_job_history``) backfills ``source = "unknown"``
  on pre-v1.0.180 entries on disk so the UI never sees a missing
  key — required for compatibility with PVCs that already have a
  ``job-history.json`` from an older controller.
* ``JobRunner`` / ``run_job`` thread the kwargs through to the
  writer so every call site can opt in.
* The HTTP ``/actions/{name}`` handler stamps
  ``_source = "manual"`` plus ``_actor_username`` from the auth
  header into ``overrides``; ``_dispatch_action`` extracts both
  and passes them to ``run_job``.
* The k8s CronJob entrypoint (``controller.py --mode <X>``) writes
  a ``cron:<mode>`` history entry around the legacy runner.
* The auto-heal cycle records an ``auto-heal`` entry when it
  actually performs work.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.jobs import framework as job_framework  # noqa: E402
from media_stack.services.jobs.framework import (  # noqa: E402
    Job, JobContext, JobRunner, _record_history, get_job_history,
    run_job, _normalize_source,
)


class _HistoryDirMixin:
    """Redirect the on-disk history file into a tmp dir per test."""

    def setUp(self) -> None:  # type: ignore[override]
        import tempfile
        self._tmpdir = tempfile.mkdtemp(prefix="jobs-history-")
        self._env_patcher = patch.dict(
            os.environ, {"CONFIG_ROOT": self._tmpdir},
        )
        self._env_patcher.start()

    def tearDown(self) -> None:  # type: ignore[override]
        self._env_patcher.stop()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class TestRecordHistorySource(_HistoryDirMixin, unittest.TestCase):
    def test_default_source_is_unknown(self):
        _record_history({
            "elapsed": 0.1, "ok": 1, "skipped": 0, "errors": 0,
            "jobs": {},
        })
        history = get_job_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["source"], "unknown")
        self.assertIsNone(history[0]["actor"])

    def test_cron_source_persists(self):
        _record_history(
            {"elapsed": 1.0, "ok": 1, "skipped": 0, "errors": 0, "jobs": {}},
            source="cron:reconcile",
        )
        history = get_job_history()
        self.assertEqual(history[0]["source"], "cron:reconcile")

    def test_manual_source_with_actor(self):
        _record_history(
            {"elapsed": 0.5, "ok": 1, "skipped": 0, "errors": 0, "jobs": {}},
            source="manual",
            actor="alice",
        )
        history = get_job_history()
        self.assertEqual(history[0]["source"], "manual")
        self.assertEqual(history[0]["actor"], "alice")

    def test_auto_heal_source_persists(self):
        _record_history(
            {"elapsed": 0.2, "ok": 1, "skipped": 0, "errors": 0, "jobs": {}},
            source="auto-heal",
        )
        history = get_job_history()
        self.assertEqual(history[0]["source"], "auto-heal")
        self.assertIsNone(history[0]["actor"])

    def test_unknown_source_token_collapses_to_unknown(self):
        # Anything outside the canonical set must NOT be persisted
        # verbatim — the UI's enum-driven badge would silently
        # render an unstyled fallback. Collapsing to ``unknown``
        # keeps every row matched against a known badge.
        _record_history(
            {"elapsed": 0.1, "ok": 1, "skipped": 0, "errors": 0, "jobs": {}},
            source="some-future-token-that-doesnt-exist",
        )
        history = get_job_history()
        self.assertEqual(history[0]["source"], "unknown")

    def test_normalize_source_helper(self):
        self.assertEqual(_normalize_source(None), "unknown")
        self.assertEqual(_normalize_source(""), "unknown")
        self.assertEqual(_normalize_source("cron"), "cron")
        self.assertEqual(_normalize_source("cron:reconcile"), "cron:reconcile")
        self.assertEqual(_normalize_source("manual"), "manual")
        self.assertEqual(_normalize_source("auto-heal"), "auto-heal")
        self.assertEqual(_normalize_source("scheduler"), "scheduler")
        self.assertEqual(_normalize_source("anything-else"), "unknown")


class TestGetJobHistoryBackfill(_HistoryDirMixin, unittest.TestCase):
    """Pre-v1.0.180 entries on disk lack ``source`` / ``actor``.

    Operators with a long-running PVC will hit this on first read
    after upgrade; we backfill in-memory so the UI never sees a
    KeyError or unset field. We must not rewrite the file from
    the reader — that would race with concurrent writers.
    """

    def test_legacy_entry_gets_unknown_source(self):
        path = Path(self._tmpdir) / ".controller" / "job-history.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        legacy_entry = {
            "ts": 1777097614.06,
            "elapsed": 0.10,
            "ok": 1, "skipped": 0, "errors": 0,
            "jobs": {"scan-completed-downloads": {
                "status": "ok", "elapsed": 0.0,
            }},
        }
        path.write_text(json.dumps([legacy_entry]), encoding="utf-8")
        history = get_job_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["source"], "unknown")
        self.assertIsNone(history[0]["actor"])
        # Existing fields untouched.
        self.assertEqual(history[0]["ts"], 1777097614.06)
        self.assertEqual(history[0]["elapsed"], 0.10)


class TestJobRunnerSourceThreading(_HistoryDirMixin, unittest.TestCase):
    """``JobRunner.run`` accepts ``source`` / ``actor`` and stamps
    them on the recorded entry. ``run`` keeps its existing return
    contract — the source plumbing is purely additive."""

    def test_jobrunner_passes_source_through(self):
        job = Job("test-handler", lambda ctx: {"key": "val"})
        ctx = JobContext()
        runner = JobRunner(job, ctx, source="cron:reconcile")
        result = runner.run()
        self.assertEqual(result["status"], "ok")
        history = get_job_history()
        self.assertEqual(history[0]["source"], "cron:reconcile")

    def test_jobrunner_default_source_is_unknown(self):
        job = Job("test-handler", lambda ctx: {})
        runner = JobRunner(job, JobContext())
        runner.run()
        history = get_job_history()
        self.assertEqual(history[0]["source"], "unknown")

    def test_run_job_propagates_source_and_actor(self):
        # Build a tiny synthetic job tree the runner can find.
        with patch.object(
            job_framework, "build_job_framework",
            return_value=Job(
                "bootstrap", lambda ctx: {},
                max_attempts=1,
            ),
        ):
            with patch.object(
                job_framework, "_find_job_in_tree",
                return_value=Job("bootstrap", lambda ctx: {"ok": 1}),
            ):
                run_job(
                    "bootstrap", source="manual", actor="alice",
                )
        history = get_job_history()
        self.assertEqual(history[0]["source"], "manual")
        self.assertEqual(history[0]["actor"], "alice")


class TestDispatchActionSourceExtraction(unittest.TestCase):
    """``_dispatch_action`` pulls ``_source`` / ``_actor_username``
    out of overrides before forwarding to ``run_job``. The legacy
    ``_triggered_by`` field is mapped: ``"system"`` keeps source
    ``None`` (defaults to ``unknown``), anything else becomes
    ``"manual"`` with the trigger-name as the actor."""

    def _capture(self, overrides):
        from media_stack.cli.commands import controller_dispatch
        captured: dict = {}
        def fake_run_job(name, *, source=None, actor=None):
            captured.update({"name": name, "source": source, "actor": actor})
            return {"status": "ok"}
        with patch.object(
            controller_dispatch, "_apply_overrides", lambda o: None,
        ):
            with patch(
                "media_stack.services.jobs.framework.run_job",
                fake_run_job,
            ):
                import argparse
                controller_dispatch._dispatch_action(
                    "reconcile", overrides, argparse.Namespace(), None,
                )
        return captured

    def test_explicit_manual_source(self):
        captured = self._capture(
            {"_source": "manual", "_actor_username": "alice"},
        )
        self.assertEqual(captured["source"], "manual")
        self.assertEqual(captured["actor"], "alice")

    def test_explicit_auto_heal_source(self):
        captured = self._capture({"_source": "auto-heal"})
        self.assertEqual(captured["source"], "auto-heal")
        self.assertIsNone(captured["actor"])

    def test_legacy_triggered_by_maps_to_manual(self):
        captured = self._capture({"_triggered_by": "alice"})
        self.assertEqual(captured["source"], "manual")
        self.assertEqual(captured["actor"], "alice")

    def test_legacy_triggered_by_system_stays_none(self):
        captured = self._capture({"_triggered_by": "system"})
        self.assertIsNone(captured["source"])

    def test_legacy_triggered_by_scheduler(self):
        captured = self._capture({"_triggered_by": "scheduler"})
        self.assertEqual(captured["source"], "scheduler")


class TestCronOneShotWritesHistory(_HistoryDirMixin, unittest.TestCase):
    """The k8s CronJobs invoke ``controller.py --mode <X>`` directly.
    ``_run_oneshot`` must write a ``cron:<mode>`` history entry so
    the dashboard's "ran via cron" badge has data to render
    against. The legacy adapter pipeline doesn't write history on
    its own."""

    def test_oneshot_writes_cron_history_entry(self):
        from media_stack.cli.commands import controller_main
        import argparse
        args = argparse.Namespace(
            config="/dev/null", config_root=self._tmpdir,
            wait_timeout=1, auto_prowlarr_indexers=False,
            mode="reconcile", env="test", serve=False,
            auto_run=False, api_port=9100,
        )
        # Stub the runner build + run so we don't actually touch
        # any service. The history entry is the contract under
        # test, not the legacy runner's behaviour.
        fake_runner = MagicMock()
        fake_runner.run = MagicMock(return_value=None)
        with patch.object(
            controller_main, "_build_runner",
            return_value=(fake_runner, MagicMock()),
        ):
            controller_main.ControllerMainCommand._run_oneshot(args)
        history = get_job_history()
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0]["source"], "cron:reconcile")
        self.assertEqual(history[0]["errors"], 0)

    def test_oneshot_history_records_error(self):
        from media_stack.cli.commands import controller_main
        import argparse
        args = argparse.Namespace(
            config="/dev/null", config_root=self._tmpdir,
            wait_timeout=1, auto_prowlarr_indexers=False,
            mode="media-hygiene", env="test", serve=False,
            auto_run=False, api_port=9100,
        )
        fake_runner = MagicMock()
        fake_runner.run = MagicMock(side_effect=RuntimeError("boom"))
        with patch.object(
            controller_main, "_build_runner",
            return_value=(fake_runner, MagicMock()),
        ):
            with self.assertRaises(RuntimeError):
                controller_main.ControllerMainCommand._run_oneshot(args)
        history = get_job_history()
        self.assertEqual(history[0]["source"], "cron:media-hygiene")
        self.assertEqual(history[0]["errors"], 1)


class TestAutoHealCycleRecordsHistory(_HistoryDirMixin, unittest.TestCase):
    """``AutoHealService.run_cycle`` writes an ``auto-heal``-tagged
    history entry whenever it took action (snapshotted or healed).
    A no-op tick (which fires every minute) does NOT record so
    the 20-entry ring buffer doesn't get crowded out."""

    def test_run_cycle_records_when_snapshot_taken(self):
        from media_stack.api.services import auto_heal as autoheal_mod
        svc = autoheal_mod.AutoHealService(
            config_root=Path(self._tmpdir),
            services=[],
        )
        # Force the snapshot pass to report 1 new snapshot, no
        # heals — covers the "took action but no corruption"
        # branch.
        svc.snapshot_healthy = lambda: 1  # type: ignore[assignment]
        svc.heal_corrupt = lambda: []  # type: ignore[assignment]
        svc.run_cycle()
        history = get_job_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["source"], "auto-heal")

    def test_run_cycle_no_action_does_not_record(self):
        from media_stack.api.services import auto_heal as autoheal_mod
        svc = autoheal_mod.AutoHealService(
            config_root=Path(self._tmpdir),
            services=[],
        )
        svc.snapshot_healthy = lambda: 0  # type: ignore[assignment]
        svc.heal_corrupt = lambda: []  # type: ignore[assignment]
        svc.run_cycle()
        history = get_job_history()
        self.assertEqual(len(history), 0)


class TestOpenAPISchemaTightened(unittest.TestCase):
    """The OpenAPI schema for ``/api/jobs.history[]`` items must
    declare the new ``source`` field with the agreed enum so the
    UI's ``pnpm gen:api`` step generates the right TypeScript
    union. Without this, ``ui/src/api/types.ts`` would still type
    ``source`` as ``never`` and the badge code wouldn't compile."""

    def test_history_item_has_source_field(self):
        import re
        import yaml
        spec_path = (
            ROOT / "contracts" / "api" / "openapi.yaml"
        )
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        history_item = (
            spec["paths"]["/api/jobs"]["get"]["responses"]["200"]
            ["content"]["application/json"]["schema"]
            ["properties"]["history"]["items"]
        )
        props = history_item.get("properties", {})
        self.assertIn(
            "source", props,
            "/api/jobs.history items must declare a 'source' field "
            "so pnpm gen:api emits the right TypeScript union",
        )
        source_schema = props["source"]
        self.assertEqual(source_schema["type"], "string")
        # The schema uses ``pattern`` (not ``enum``) so sub-tagged
        # forms like ``cron:reconcile`` are valid. The set of
        # canonical heads is captured separately via the
        # ``x-source-heads`` extension for tooling that wants the
        # closed set.
        self.assertIn("pattern", source_schema)
        pattern = re.compile(source_schema["pattern"])
        for token in ("cron", "manual", "auto-heal",
                      "scheduler", "unknown",
                      "cron:reconcile", "cron:media-hygiene"):
            self.assertTrue(
                pattern.match(token),
                f"source pattern rejects '{token}' but the writer "
                "produces it",
            )
        for bad in ("", "ran-by-bob", "cron:", ":reconcile"):
            self.assertIsNone(
                pattern.match(bad),
                f"source pattern accepts invalid value '{bad}'",
            )
        heads = source_schema.get("x-source-heads", [])
        for required_head in ("cron", "manual", "auto-heal", "unknown"):
            self.assertIn(
                required_head, heads,
                f"x-source-heads missing '{required_head}' — "
                "the UI persona-questions agent's badge selector "
                "reads this list to enumerate canonical badges.",
            )
        self.assertIn(
            "actor", props,
            "/api/jobs.history items must declare an optional "
            "'actor' field so the manual-source badge can show "
            "who triggered the run.",
        )
        self.assertTrue(
            props["actor"].get("nullable", False),
            "history.actor must be nullable — cron / auto-heal "
            "runs have no operator username.",
        )


if __name__ == "__main__":
    unittest.main()
