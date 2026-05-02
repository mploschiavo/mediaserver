"""Tests for ``FreshInstallVerifier`` (ADR-0004 Phase 6.2).

Pin the contract that ``media-stack-verify`` (Phase 6.3) and
``verify-fresh-install.sh`` (Phase 6.4) rely on:

  * verify() always returns a result (never raises) — even on
    network errors, auth failures, or 5xx responses.
  * is_acceptance_pass is True iff every applicable promise is ok.
  * Stale ticks fail verification with a clear error.
  * wait_for_steady_state polls until pass / timeout / fail-fast on
    failed_permanent.

The verifier is mocked at the URL-opener seam so tests pin behavior
end-to-end through ``_fetch_state`` without spinning a real HTTP
server.
"""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any
from unittest.mock import MagicMock

import pytest

from media_stack.application.verifier.fresh_install import (
    FreshInstallVerifier,
    VerificationResult,
)


# ---------------------------------------------------------------------------
# Fake HTTP plumbing
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal urlopen-context-manager response."""

    def __init__(self, status: int, body: dict | bytes) -> None:
        self._status = status
        if isinstance(body, dict):
            self._raw = json.dumps(body).encode("utf-8")
        else:
            self._raw = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._raw

    def getcode(self) -> int:
        return self._status


def _opener_returning(status: int, body: dict | bytes):
    def _open(req, timeout=None):  # noqa: ARG001
        return _FakeResponse(status, body)
    return _open


def _opener_raising(exc: Exception):
    def _open(req, timeout=None):  # noqa: ARG001
        raise exc
    return _open


def _make_verifier(opener, **kw) -> FreshInstallVerifier:
    return FreshInstallVerifier(
        controller_url="http://localhost:9100",
        admin_user="admin",
        admin_pass="admin",
        url_opener=opener,
        **kw,
    )


def _ok_payload(promise_ids: list[str], **overrides) -> dict:
    """Build a 200 endpoint response with all promises ok."""
    attempts = [
        {
            "promise_id": pid,
            "status": "ok",
            "started_at": 999.0,
            "elapsed_seconds": 0.1,
            "detail": "probe asserted ok",
            "probe_evidence": {"http_status": 200},
            "ensurer_fired": False,
            "ensurer_attempts": 0,
            "consecutive_failures": 0,
        }
        for pid in promise_ids
    ]
    payload = {
        "version": 1,
        "saved_at": 1000.0,
        "last_tick_age_seconds": 5.0,
        "platform": "compose",
        "live_services": ["jellyfin"],
        "totals": {
            "total": len(attempts), "ok": len(attempts),
            "failed_transient": 0, "failed_permanent": 0,
            "dep_failed": 0, "skipped_cooldown": 0,
            "skipped_platform": 0, "unknown": 0,
        },
        "attempts": attempts,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# verify() — happy paths
# ---------------------------------------------------------------------------


class TestAcceptancePass:
    def test_all_ok_is_acceptance_pass(self) -> None:
        v = _make_verifier(
            _opener_returning(200, _ok_payload(["jellyfin-running", "qbit-categories"])),
        )
        result = v.verify()
        assert result.is_acceptance_pass is True
        assert result.total == 2
        assert result.passed == 2
        assert result.failed == ()
        assert result.unknown == ()
        assert result.skipped == ()
        assert result.platform == "compose"
        assert result.last_tick_age_seconds == pytest.approx(5.0)
        assert result.controller_reachable is True
        assert result.error is None

    def test_summary_lines_include_platform_and_count(self) -> None:
        v = _make_verifier(
            _opener_returning(200, _ok_payload(["a", "b", "c"])),
        )
        result = v.verify()
        assert any("3/3 promises ok" in line for line in result.detail_lines)
        assert any("platform=compose" in line for line in result.detail_lines)

    def test_zero_attempts_is_not_acceptance_pass(self) -> None:
        # An empty attempts list means the orchestrator hasn't ticked
        # against any promise — that's not "all ok", that's "nothing
        # measured". MUST fail acceptance.
        v = _make_verifier(_opener_returning(200, _ok_payload([])))
        result = v.verify()
        assert result.is_acceptance_pass is False
        assert result.total == 0


# ---------------------------------------------------------------------------
# verify() — failure modes
# ---------------------------------------------------------------------------


class TestFailureBuckets:
    def test_failed_transient_fails_acceptance(self) -> None:
        body = _ok_payload(["jellyfin-running"])
        body["attempts"].append({
            "promise_id": "qbit-running", "status": "failed_transient",
            "started_at": 999.0, "elapsed_seconds": 0.5,
            "detail": "qbit not yet ready", "probe_evidence": {},
            "ensurer_fired": False, "ensurer_attempts": 0,
            "consecutive_failures": 1,
        })
        v = _make_verifier(_opener_returning(200, body))
        result = v.verify()
        assert result.is_acceptance_pass is False
        assert len(result.failed) == 1
        assert result.failed[0].promise_id == "qbit-running"
        assert any("FAIL" in line and "qbit-running" in line
                   for line in result.detail_lines)

    def test_unknown_fails_acceptance(self) -> None:
        # Important: unknown is NOT a pass. The compose envoy:8080 SSL
        # case in production is unknown, and an external reviewer
        # would expect that to NOT be silently green.
        body = _ok_payload(["jellyfin-running"])
        body["attempts"].append({
            "promise_id": "gateway-http", "status": "unknown",
            "started_at": 999.0, "elapsed_seconds": 0.1,
            "detail": "SSL: WRONG_VERSION_NUMBER",
            "probe_evidence": {"error": "ssl error"},
            "ensurer_fired": False, "ensurer_attempts": 0,
            "consecutive_failures": 5,
        })
        v = _make_verifier(_opener_returning(200, body))
        result = v.verify()
        assert result.is_acceptance_pass is False
        assert len(result.unknown) == 1
        assert result.unknown[0].promise_id == "gateway-http"

    def test_skipped_does_not_fail_acceptance(self) -> None:
        # Cooldown / platform skips aren't failures — the orchestrator
        # is just deferring an evaluation. They appear in the bucket
        # but don't flip the acceptance bool.
        body = _ok_payload(["jellyfin-running"])
        body["attempts"].append({
            "promise_id": "k8s-only-promise", "status": "skipped_platform",
            "started_at": 999.0, "elapsed_seconds": 0.0,
            "detail": "promise applies to k8s only",
            "probe_evidence": {},
            "ensurer_fired": False, "ensurer_attempts": 0,
            "consecutive_failures": 0,
        })
        v = _make_verifier(_opener_returning(200, body))
        result = v.verify()
        assert result.is_acceptance_pass is True
        assert len(result.skipped) == 1


# ---------------------------------------------------------------------------
# verify() — non-200 / unreachable
# ---------------------------------------------------------------------------


class TestController5xxAnd503:
    def test_503_stale_payload_is_actionable(self) -> None:
        stale_body = {
            "error": "orchestrator state stale (controller mid-restart?)",
            "saved_at": 100.0,
            "last_tick_age_seconds": 9999.0,
            "platform": "compose",
            "live_services": [],
        }
        opener = _opener_raising(urllib.error.HTTPError(
            url="http://x/api/orchestrator/promises/state",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=io.BytesIO(json.dumps(stale_body).encode()),
        ))
        v = _make_verifier(opener)
        result = v.verify()
        assert result.is_acceptance_pass is False
        assert result.controller_reachable is True
        assert result.last_tick_age_seconds == pytest.approx(9999.0)
        assert result.error and "stale" in result.error.lower()

    def test_unexpected_500_does_not_raise(self) -> None:
        opener = _opener_raising(urllib.error.HTTPError(
            url="http://x/api/orchestrator/promises/state",
            code=500, msg="Server Error", hdrs=None,
            fp=io.BytesIO(b"{}"),
        ))
        v = _make_verifier(opener)
        result = v.verify()
        assert result.is_acceptance_pass is False
        assert result.controller_reachable is True
        assert result.error and "500" in result.error


class TestUnreachable:
    def test_url_error_is_not_reachable(self) -> None:
        opener = _opener_raising(urllib.error.URLError("connection refused"))
        v = _make_verifier(opener)
        result = v.verify()
        assert result.is_acceptance_pass is False
        assert result.controller_reachable is False
        assert result.error and "connection refused" in result.error

    def test_timeout_is_not_reachable(self) -> None:
        opener = _opener_raising(TimeoutError("read timed out"))
        v = _make_verifier(opener)
        result = v.verify()
        assert result.controller_reachable is False
        assert result.error and "timed out" in result.error.lower()


# ---------------------------------------------------------------------------
# verify() — staleness
# ---------------------------------------------------------------------------


class TestVerifierStalenessRecheck:
    def test_endpoint_200_but_age_above_verifier_threshold_fails(self) -> None:
        # Endpoint may use a 120s stale threshold but the verifier
        # may want a tighter 90s. Pin the verifier's own check.
        body = _ok_payload(["jellyfin"])
        body["last_tick_age_seconds"] = 100.0  # endpoint thinks fresh
        v = _make_verifier(
            _opener_returning(200, body),
            require_fresh_tick_within_seconds=90.0,
        )
        result = v.verify()
        assert result.is_acceptance_pass is False
        assert result.error and "100s old" in result.error

    def test_endpoint_200_and_age_below_threshold_passes(self) -> None:
        body = _ok_payload(["jellyfin"])
        body["last_tick_age_seconds"] = 30.0
        v = _make_verifier(
            _opener_returning(200, body),
            require_fresh_tick_within_seconds=90.0,
        )
        result = v.verify()
        assert result.is_acceptance_pass is True


# ---------------------------------------------------------------------------
# wait_for_steady_state()
# ---------------------------------------------------------------------------


class TestWaitForSteadyState:
    def test_returns_first_pass_result(self) -> None:
        # First poll: one promise still failed_transient. Second: ok.
        attempts_pending = _ok_payload(["jellyfin"])
        attempts_pending["attempts"][0]["status"] = "failed_transient"
        attempts_pending["attempts"][0]["detail"] = "warming up"

        attempts_done = _ok_payload(["jellyfin"])

        responses = [attempts_pending, attempts_done]

        def opener(req, timeout=None):  # noqa: ARG001
            return _FakeResponse(200, responses.pop(0))

        v = _make_verifier(opener)
        sleeps: list[float] = []
        result = v.wait_for_steady_state(
            max_wait_seconds=60.0,
            poll_interval_seconds=2.0,
            sleep=sleeps.append,
        )
        assert result.is_acceptance_pass is True
        assert sleeps == [2.0]  # exactly one inter-poll sleep
        assert responses == []  # both responses consumed

    def test_fail_fast_on_failed_permanent(self) -> None:
        body = _ok_payload(["jellyfin"])
        body["attempts"].append({
            "promise_id": "qbit-categories", "status": "failed_permanent",
            "started_at": 999.0, "elapsed_seconds": 0.0,
            "detail": "QBITTORRENT_PASSWORD unset",
            "probe_evidence": {},
            "ensurer_fired": True, "ensurer_attempts": 3,
            "consecutive_failures": 5,
        })
        v = _make_verifier(_opener_returning(200, body))
        sleeps: list[float] = []
        result = v.wait_for_steady_state(
            max_wait_seconds=300.0, poll_interval_seconds=5.0,
            sleep=sleeps.append,
        )
        # Returns immediately on first poll — no sleep, no waiting
        # for the deadline. Bad operator config doesn't get fixed by
        # waiting longer.
        assert sleeps == []
        assert result.is_acceptance_pass is False
        assert any(a.status == "failed_permanent" for a in result.failed)

    def test_returns_last_result_after_timeout(self, monkeypatch) -> None:
        # Always-failing world. Verify the loop respects the deadline
        # rather than hanging forever.
        body = _ok_payload(["jellyfin"])
        body["attempts"][0]["status"] = "failed_transient"

        v = _make_verifier(_opener_returning(200, body))

        # Drive the clock manually so the test is deterministic.
        clock = iter([0.0, 0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0])
        monkeypatch.setattr(
            "media_stack.application.verifier.fresh_install.time.time",
            lambda: next(clock),
        )
        sleeps: list[float] = []
        result = v.wait_for_steady_state(
            max_wait_seconds=10.0, poll_interval_seconds=5.0,
            sleep=sleeps.append,
        )
        assert result.is_acceptance_pass is False
        # Loop exited via deadline, not fail-fast or pass.
        assert all(a.status != "failed_permanent" for a in result.failed)


# ---------------------------------------------------------------------------
# constructor + auth wiring
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_blank_url_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="controller_url"):
            FreshInstallVerifier(controller_url="")

    def test_basic_auth_header_is_attached(self) -> None:
        captured: list[Any] = []

        def opener(req, timeout=None):  # noqa: ARG001
            captured.append(req)
            return _FakeResponse(200, _ok_payload(["x"]))

        v = FreshInstallVerifier(
            controller_url="http://localhost:9100/",
            admin_user="alice", admin_pass="hunter2",
            url_opener=opener,
        )
        v.verify()

        req = captured[0]
        auth = req.headers.get("Authorization")
        assert auth and auth.startswith("Basic ")
        # Check trailing-slash stripped from controller_url so the
        # final URL doesn't have a double slash.
        assert req.full_url == "http://localhost:9100/api/orchestrator/promises/state"

    def test_empty_creds_omits_auth_header(self) -> None:
        captured: list[Any] = []

        def opener(req, timeout=None):  # noqa: ARG001
            captured.append(req)
            return _FakeResponse(200, _ok_payload(["x"]))

        v = FreshInstallVerifier(
            controller_url="http://localhost:9100",
            admin_user="", admin_pass="",
            url_opener=opener,
        )
        v.verify()
        assert captured[0].headers.get("Authorization") is None
