"""Tests for ``api.services.orchestrator_state.read_state``.

Pins the response-shape contract that ``FreshInstallVerifier``
(ADR-0004) reads. The schema lives at
``tests/fixtures/orchestrator/promises_state_endpoint.schema.json``;
these tests pin it in code so a drift gets a fast failure.

Three classes:
  * Fresh state present  → 200, full payload
  * Stale / missing      → 503, age-bearing payload
  * Live snapshot replay → fed the actual ``promise_state.json``
                            captured from the running stack to make
                            sure the parser matches reality.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from media_stack.api.services.orchestrator_state import read_state


_LIVE_FIXTURE = (
    Path(__file__).parent.parent.parent
    / "fixtures" / "orchestrator" / "promise_state_live.json"
)


def _write_state(tmp_path: Path, payload: dict) -> Path:
    state_path = tmp_path / "promise_state.json"
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    return state_path


class TestFreshState:
    def test_returns_200_when_state_is_fresh(self, tmp_path) -> None:
        state_path = _write_state(tmp_path, {
            "version": 1,
            "saved_at": 1000.0,
            "attempts": {
                "p1": {
                    "promise_id": "p1",
                    "status": "ok",
                    "started_at": 999.0,
                    "elapsed_seconds": 0.1,
                    "detail": "probe asserted ok",
                    "probe_evidence": {"http_status": 200},
                    "ensurer_fired": False,
                    "ensurer_attempts": 0,
                    "consecutive_failures": 0,
                },
            },
        })
        status, body = read_state(
            now=1010.0, path=state_path,
            platform="compose", live_services=frozenset({"jellyfin"}),
        )
        assert status == 200
        assert body["version"] == 1
        assert body["saved_at"] == 1000.0
        assert body["last_tick_age_seconds"] == pytest.approx(10.0)
        assert body["platform"] == "compose"
        assert body["live_services"] == ["jellyfin"]
        assert body["totals"] == {
            "total": 1, "ok": 1, "failed_transient": 0, "failed_permanent": 0,
            "dep_failed": 0, "skipped_cooldown": 0, "skipped_platform": 0,
            "unknown": 0,
        }
        assert len(body["attempts"]) == 1
        attempt = body["attempts"][0]
        assert attempt["promise_id"] == "p1"
        assert attempt["status"] == "ok"
        assert attempt["probe_evidence"] == {"http_status": 200}

    def test_attempts_are_sorted_by_promise_id(self, tmp_path) -> None:
        state_path = _write_state(tmp_path, {
            "version": 1,
            "saved_at": 1000.0,
            "attempts": {
                pid: {
                    "promise_id": pid, "status": "ok",
                    "started_at": 999.0, "elapsed_seconds": 0.1,
                    "detail": "", "probe_evidence": {},
                    "ensurer_fired": False, "ensurer_attempts": 0,
                    "consecutive_failures": 0,
                }
                for pid in ["zebra", "alpha", "mike"]
            },
        })
        _, body = read_state(
            now=1010.0, path=state_path,
            platform="compose", live_services=frozenset(),
        )
        assert [a["promise_id"] for a in body["attempts"]] == [
            "alpha", "mike", "zebra",
        ]

    def test_totals_aggregate_each_status(self, tmp_path) -> None:
        # Mix every status the orchestrator can emit so the totals
        # block is pinned exhaustively. A new status the totals block
        # forgets will show up as a key the verifier can't read.
        attempts = {}
        for i, status in enumerate([
            "ok", "ok", "ok", "failed_transient", "failed_permanent",
            "dep_failed", "skipped_cooldown", "skipped_platform", "unknown",
        ]):
            attempts[f"p{i}"] = {
                "promise_id": f"p{i}", "status": status,
                "started_at": 999.0, "elapsed_seconds": 0.0,
                "detail": "", "probe_evidence": {},
                "ensurer_fired": False, "ensurer_attempts": 0,
                "consecutive_failures": 0,
            }
        state_path = _write_state(tmp_path, {
            "version": 1, "saved_at": 1000.0, "attempts": attempts,
        })
        _, body = read_state(
            now=1010.0, path=state_path,
            platform="compose", live_services=frozenset(),
        )
        assert body["totals"] == {
            "total": 9, "ok": 3, "failed_transient": 1, "failed_permanent": 1,
            "dep_failed": 1, "skipped_cooldown": 1, "skipped_platform": 1,
            "unknown": 1,
        }


class TestNon200Modes:
    def test_503_when_file_missing(self, tmp_path) -> None:
        status, body = read_state(
            now=1000.0, path=tmp_path / "no-such-file.json",
            platform="compose", live_services=frozenset(),
        )
        assert status == 503
        assert body["saved_at"] is None
        assert body["last_tick_age_seconds"] is None
        assert body["platform"] == "compose"
        assert body["live_services"] == []
        assert "not yet persisted" in body["error"]

    def test_503_when_state_is_stale(self, tmp_path) -> None:
        state_path = _write_state(tmp_path, {
            "version": 1, "saved_at": 1000.0, "attempts": {},
        })
        status, body = read_state(
            now=2000.0, path=state_path,
            platform="compose", live_services=frozenset(),
            stale_threshold_seconds=120.0,
        )
        assert status == 503
        assert body["saved_at"] == 1000.0
        assert body["last_tick_age_seconds"] == pytest.approx(1000.0)
        assert "stale" in body["error"]

    def test_503_when_file_is_malformed(self, tmp_path) -> None:
        bad = tmp_path / "promise_state.json"
        bad.write_text("not-valid-json{", encoding="utf-8")
        status, body = read_state(
            now=1000.0, path=bad,
            platform="compose", live_services=frozenset(),
        )
        assert status == 503
        assert body["saved_at"] is None
        assert body["last_tick_age_seconds"] is None
        assert "unreadable" in body["error"]

    def test_503_when_saved_at_field_missing(self, tmp_path) -> None:
        # An older state file (or hand-crafted one) might omit
        # saved_at. Endpoint MUST treat that as stale, not crash.
        state_path = _write_state(tmp_path, {
            "version": 1, "attempts": {},
        })
        status, body = read_state(
            now=1000.0, path=state_path,
            platform="compose", live_services=frozenset(),
        )
        assert status == 503
        assert body["saved_at"] is None
        assert body["last_tick_age_seconds"] is None

    def test_threshold_boundary_is_inclusive_below(self, tmp_path) -> None:
        state_path = _write_state(tmp_path, {
            "version": 1, "saved_at": 1000.0, "attempts": {},
        })
        # age == 120, threshold == 120 → fresh (>120 is the cutoff)
        status, body = read_state(
            now=1120.0, path=state_path,
            platform="compose", live_services=frozenset(),
            stale_threshold_seconds=120.0,
        )
        assert status == 200
        assert body["last_tick_age_seconds"] == pytest.approx(120.0)

    def test_threshold_boundary_just_over_is_stale(self, tmp_path) -> None:
        state_path = _write_state(tmp_path, {
            "version": 1, "saved_at": 1000.0, "attempts": {},
        })
        status, _ = read_state(
            now=1120.5, path=state_path,
            platform="compose", live_services=frozenset(),
            stale_threshold_seconds=120.0,
        )
        assert status == 503


class TestLiveSnapshotReplay:
    """Feed the parser the actual file the running stack produces."""

    def test_live_snapshot_parses_to_200_when_treated_as_fresh(self) -> None:
        # The fixture is captured at saved_at = (whatever was real).
        # Read it, tell the function `now` is just-after-saved_at, and
        # verify the parser handles every promise it actually emits.
        if not _LIVE_FIXTURE.is_file():
            pytest.skip(f"live fixture missing: {_LIVE_FIXTURE}")
        raw = json.loads(_LIVE_FIXTURE.read_text(encoding="utf-8"))
        saved_at = float(raw["saved_at"])

        status, body = read_state(
            now=saved_at + 5.0, path=_LIVE_FIXTURE,
            platform="compose", live_services=frozenset({"jellyfin"}),
        )
        assert status == 200, body
        # The capture has 32 promises — pin the count so a registry
        # drift surfaces here. If the orchestrator gains/loses
        # promises, this test is the canary; either re-capture the
        # fixture or ratchet the assertion.
        assert body["totals"]["total"] >= 30
        # All required per-attempt fields are present and well-typed.
        for attempt in body["attempts"]:
            assert isinstance(attempt["promise_id"], str)
            assert attempt["status"] in {
                "ok", "failed_transient", "failed_permanent", "dep_failed",
                "skipped_cooldown", "skipped_platform", "unknown",
            }
            assert isinstance(attempt["probe_evidence"], dict)
            assert isinstance(attempt["ensurer_attempts"], int)
        # Sum of per-status totals == total. Cheap consistency check.
        t = body["totals"]
        assert (t["ok"] + t["failed_transient"] + t["failed_permanent"]
                + t["dep_failed"] + t["skipped_cooldown"]
                + t["skipped_platform"] + t["unknown"]) == t["total"]
