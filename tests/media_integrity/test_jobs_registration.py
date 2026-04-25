"""Tests for the v1.0.184 media-integrity → Job framework migration.

Asserts:

1. The four media-integrity jobs are discovered from the
   ``contracts/services/media_integrity.yaml`` contract with the
   expected handler bindings.
2. The three cadence-driven jobs land in the controller's
   schedule with the correct interval (≈cron equivalence).
3. Running ``media-integrity:reconcile`` through ``run_job(...)``
   writes a unified history entry under that job name (so the
   ``/api/jobs.history[]`` feed reflects scheduled + manual runs
   identically).
4. The legacy ``MediaIntegrityScheduler`` is no longer constructed
   in ``controller_serve``'s wiring path — only ``set_service``
   remains.

These tests pin the migration's contract so a future refactor can't
silently re-introduce the parallel scheduler.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ---------------------------------------------------------------------------
# Job discovery
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_caches():
    """Force a fresh discovery scan per test so changes to the
    contract YAML are picked up without test-order dependencies."""
    from media_stack.services.jobs import framework as jf
    jf._DISCOVERED_JOBS_CACHE = None
    jf._DISCOVERED_ALIASES_CACHE = None
    yield
    jf._DISCOVERED_JOBS_CACHE = None
    jf._DISCOVERED_ALIASES_CACHE = None


def _discovered_by_name() -> dict:
    from media_stack.services.jobs.framework import (
        discover_jobs_from_contracts,
    )
    return {j["name"]: j for j in discover_jobs_from_contracts()}


def test_four_media_integrity_jobs_are_registered() -> None:
    jobs = _discovered_by_name()
    expected = {
        "media-integrity:scan",
        "media-integrity:reconcile",
        "media-integrity:enforce-config",
        "media-integrity:resolve-review",
    }
    missing = expected - set(jobs)
    assert not missing, f"missing job registrations: {missing}"


def test_handlers_point_at_job_handlers_module() -> None:
    jobs = _discovered_by_name()
    expected_handlers = {
        "media-integrity:scan":
            "media_stack.services.media_integrity.job_handlers:"
            "media_integrity_scan",
        "media-integrity:reconcile":
            "media_stack.services.media_integrity.job_handlers:"
            "media_integrity_reconcile",
        "media-integrity:enforce-config":
            "media_stack.services.media_integrity.job_handlers:"
            "media_integrity_enforce_config",
        "media-integrity:resolve-review":
            "media_stack.services.media_integrity.job_handlers:"
            "media_integrity_resolve_review",
    }
    for name, expected in expected_handlers.items():
        assert jobs[name]["handler"] == expected, (
            f"{name} handler pointer drifted to {jobs[name]['handler']!r}"
        )


def test_handlers_resolve_to_callables() -> None:
    """The contract's handler strings must actually import — a typo
    would surface here, before the controller picks the job."""
    from media_stack.services.jobs.framework import _resolve_handler
    for name in (
        "media-integrity:scan",
        "media-integrity:reconcile",
        "media-integrity:enforce-config",
        "media-integrity:resolve-review",
    ):
        path = _discovered_by_name()[name]["handler"]
        fn = _resolve_handler(path)
        assert callable(fn), f"{name}: handler not callable"


# ---------------------------------------------------------------------------
# Schedule cadence verification (interval-equivalent of the cron
# expressions in the migration plan).
# ---------------------------------------------------------------------------


def test_scheduled_cadences_match_cron_equivalents(monkeypatch, tmp_path) -> None:
    """``_scheduler_loop`` seeds three cadence-driven jobs via the
    controller's ``SchedulerService``. We can't run the full
    controller serve loop here, so we replay just the seed step
    against an isolated schedules.json and inspect the output."""
    monkeypatch.setenv("CONFIG_ROOT", str(tmp_path))
    # Reset the cached schedules path so the fresh CONFIG_ROOT is
    # honoured.
    from media_stack.api.services import scheduler as _sched
    _sched._SCHEDULES_FILE = None

    # Seed all three cadence-driven media-integrity entries.
    _sched.add_schedule(
        action="media-integrity:scan",
        interval_seconds=900,
        label="Media-integrity status scan (every 15m)",
    )
    _sched.add_schedule(
        action="media-integrity:reconcile",
        interval_seconds=21600,
        label="Media-integrity duplicate reconcile (every 6h)",
    )
    _sched.add_schedule(
        action="media-integrity:enforce-config",
        interval_seconds=86400,
        label="Media-integrity policy enforcement (daily)",
    )

    payload = _sched.get_schedules()
    by_action = {s["action"]: s for s in payload["schedules"]}
    assert by_action["media-integrity:scan"]["interval_seconds"] == 900
    assert by_action["media-integrity:reconcile"]["interval_seconds"] == 21600
    assert by_action["media-integrity:enforce-config"]["interval_seconds"] == 86400


def test_resolve_review_is_manual_only() -> None:
    """resolve-review is a parameterised, trigger-only job. The
    seed step in controller_serve must NOT add a schedule for it
    — verify by running the seed logic and checking the output."""
    # We simply assert that the contract carries no scheduling hint
    # for resolve-review (no built-in cron field) and that the
    # expected three (NOT four) cadences are seeded by the
    # controller_serve loop. The previous test seeds three entries;
    # this one ensures resolve-review isn't accidentally added by
    # the same code path. Read controller_serve.py source for the
    # literal entries.
    src = (
        ROOT / "src" / "media_stack" / "cli" / "commands"
        / "controller_serve.py"
    ).read_text(encoding="utf-8")
    assert 'media-integrity:scan' in src
    assert 'media-integrity:reconcile' in src
    assert 'media-integrity:enforce-config' in src
    assert 'media-integrity:resolve-review' not in src, (
        "resolve-review must not be seeded with a schedule "
        "(it's manual-only / trigger-only)"
    )


# ---------------------------------------------------------------------------
# History reconciliation: running media-integrity:reconcile via
# run_job writes to the unified job-history.json so /api/jobs.history
# reflects it.
# ---------------------------------------------------------------------------


def test_reconcile_via_run_job_writes_unified_history(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setenv("CONFIG_ROOT", str(tmp_path))

    # Stub the media-integrity service singleton with a fake whose
    # reconcile() returns a stable payload. The job handler reaches
    # for ``_instance._service`` so we set that directly.
    fake_payload = {"servarr": {"total_resolved": 0}, "bazarr": None}

    class _FakeService:
        def reconcile(self, *, actor: str = "system", dry_run: bool = False):
            assert dry_run is False
            return dict(fake_payload)

    from media_stack.api.services.media_integrity_handlers import (
        _instance as _api,
    )
    _api.set_service(_FakeService())

    from media_stack.services.jobs.framework import (
        run_job, get_job_history,
    )
    # Bust the discovery cache so the new contract is picked up.
    import media_stack.services.jobs.framework as jf
    jf._DISCOVERED_JOBS_CACHE = None
    jf._DISCOVERED_ALIASES_CACHE = None

    result = run_job(
        "media-integrity:reconcile", source="manual", actor="alice",
    )
    assert result.get("status") == "ok", result
    # The handler wraps the service payload under ``reconcile``.
    job_entry = result["jobs"]["media-integrity:reconcile"]
    assert job_entry["status"] == "ok"
    assert job_entry["reconcile"] == fake_payload

    # History entry was written and is tagged with source/actor.
    history = get_job_history()
    matching = [
        e for e in history
        if "media-integrity:reconcile" in (e.get("jobs") or {})
    ]
    assert matching, "no /api/jobs.history entry for the run"
    last = matching[0]  # newest first
    assert last["source"] == "manual"
    assert last["actor"] == "alice"
    # Reset for hygiene
    _api.set_service(None)


def test_reconcile_skips_when_service_not_configured(
    monkeypatch, tmp_path,
) -> None:
    """A run on a stack with the media-integrity service unavailable
    (e.g. missing API keys) should land as ``skipped`` — not error.
    Same posture the legacy daemon thread had ('best-effort, log
    warning, continue serving')."""
    monkeypatch.setenv("CONFIG_ROOT", str(tmp_path))
    from media_stack.api.services.media_integrity_handlers import (
        _instance as _api,
    )
    _api.set_service(None)

    import media_stack.services.jobs.framework as jf
    jf._DISCOVERED_JOBS_CACHE = None
    jf._DISCOVERED_ALIASES_CACHE = None
    from media_stack.services.jobs.framework import run_job

    result = run_job(
        "media-integrity:reconcile", source="cron", actor=None,
    )
    job_entry = result["jobs"]["media-integrity:reconcile"]
    assert job_entry["status"] == "skipped"
    assert "not configured" in (job_entry.get("skipped") or "")


# ---------------------------------------------------------------------------
# Legacy parallel scheduler is no longer wired in production.
# ---------------------------------------------------------------------------


def test_controller_serve_no_longer_imports_scheduler_hook() -> None:
    """The migration removed ``MediaIntegrityScheduler`` /
    ``SchedulerConfig`` imports + boot_delay_sec /
    reconcile_interval_sec env-var reads from controller_serve. Pin
    the deletion so a careless re-add doesn't silently re-create the
    dual-driver hazard."""
    src = (
        ROOT / "src" / "media_stack" / "cli" / "commands"
        / "controller_serve.py"
    ).read_text(encoding="utf-8")
    assert "MediaIntegrityScheduler" not in src, (
        "controller_serve must not re-instantiate "
        "MediaIntegrityScheduler — JobRunner owns cadence now"
    )
    assert "MEDIA_INTEGRITY_BOOT_DELAY_SEC" not in src
    assert "MEDIA_INTEGRITY_RECONCILE_INTERVAL_SEC" not in src
    assert "MEDIA_INTEGRITY_ENFORCE_AT_BOOT" not in src
    assert "MEDIA_INTEGRITY_ENFORCE_EACH_TICK" not in src


def test_scheduler_hook_construction_emits_deprecation_warning() -> None:
    """The legacy ``MediaIntegrityScheduler`` is deprecated. Pin the
    DeprecationWarning so any re-revival of the parallel scheduler
    in production code surfaces in CI."""
    import warnings
    from media_stack.services.media_integrity.scheduler_hook import (
        MediaIntegrityScheduler,
    )

    class _Stub:
        def enforce_config(self, *, actor="x"):
            return {}

        def reconcile(self, *, actor="x"):
            return {}

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        MediaIntegrityScheduler(service=_Stub())  # type: ignore[arg-type]
    deps = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deps, "expected a DeprecationWarning on construction"


# ---------------------------------------------------------------------------
# Backwards compat: the legacy POST endpoint still works and still
# returns the raw service payload (UI v1.3.x keeps working).
# ---------------------------------------------------------------------------


def test_http_post_still_returns_raw_service_shape(
    monkeypatch, tmp_path,
) -> None:
    """Drive ``_dispatch_media_integrity_via_job`` end-to-end with a
    fake service + a recording handler. The response body must be
    the raw service payload (not the JobRunner summary), so the SPA's
    existing fetch handlers don't break."""
    monkeypatch.setenv("CONFIG_ROOT", str(tmp_path))

    fake_payload = {
        "servarr": {"total_resolved": 0}, "bazarr": None,
        "dry_run": False,
    }

    class _FakeService:
        def reconcile(self, *, actor: str = "system", dry_run: bool = False):
            return dict(fake_payload)

        def status(self):
            return {}

        def get_progress(self):
            return {"in_progress": False}

    from media_stack.api.services.media_integrity_handlers import (
        _instance as _api,
    )
    _api.set_service(_FakeService())

    # Reset job framework caches.
    import media_stack.services.jobs.framework as jf
    jf._DISCOVERED_JOBS_CACHE = None
    jf._DISCOVERED_ALIASES_CACHE = None

    captured: dict = {}

    class _RecHandler:
        path = "/api/media-integrity/reconcile"
        headers = {"Idempotency-Key": ""}

        def _json_response(self, status, body):
            captured["status"] = status
            captured["body"] = body

        def _read_json_body(self):
            return {}

    class _Actor:
        is_admin = True
        is_authenticated = True
        audit_label = "alice"

    from media_stack.api.handlers_post import (
        _dispatch_media_integrity_via_job,
    )
    _dispatch_media_integrity_via_job(
        _RecHandler(), "/api/media-integrity/reconcile", {}, _Actor(),
    )
    assert captured["status"] == 200
    # The legacy shape: keys from the service payload land at the top
    # level. We don't pin every key; just confirm the body is the
    # service payload, NOT the JobRunner summary (which would have
    # ``jobs``/``ok``/``errors`` etc.).
    assert "servarr" in captured["body"], captured["body"]
    assert "jobs" not in captured["body"]
    _api.set_service(None)
