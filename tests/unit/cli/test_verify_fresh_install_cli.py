"""Tests for ``media-stack-verify`` CLI (ADR-0004 Phase 6.3).

Pin the contract that ``verify-fresh-install.sh`` (Phase 6.4) and
CI consume:

  * Exit codes: 0 (pass), 1 (probe failed), 2 (unreachable / state-
    not-yet / stale).
  * Flag shape matches the legacy CLI (``--controller-url``,
    ``--admin-user``, ``--admin-pass``, ``--compose-file``, ``--json``,
    ``--filter``).
  * Env fallbacks for creds (``CONTROLLER_URL``, ``ADMIN_USER``,
    ``ADMIN_PASS``).
  * ``--filter`` recomputes acceptance against the matching subset.
  * ``--wait`` invokes wait_for_steady_state instead of single-shot.

The CLI is mocked at the FreshInstallVerifier seam — the verifier's
own behavior is pinned in test_verifier_fresh_install.py.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from media_stack.application.verifier.fresh_install import (
    VerificationResult,
    VerifierAttempt,
)
from media_stack.cli.commands import verify_fresh_install as cli


def _attempt(promise_id: str, status: str = "ok") -> VerifierAttempt:
    return VerifierAttempt(
        promise_id=promise_id,
        status=status,
        started_at=999.0,
        elapsed_seconds=0.1,
        detail=f"{status} probe",
        probe_evidence={},
        ensurer_fired=False,
        ensurer_attempts=0,
        consecutive_failures=0,
    )


def _result(
    *,
    is_pass: bool = True,
    passed: tuple[VerifierAttempt, ...] = (),
    failed: tuple[VerifierAttempt, ...] = (),
    unknown: tuple[VerifierAttempt, ...] = (),
    skipped: tuple[VerifierAttempt, ...] = (),
    controller_reachable: bool = True,
    error: str | None = None,
    total_override: int | None = None,
) -> VerificationResult:
    total = total_override if total_override is not None else (
        len(passed) + len(failed) + len(unknown) + len(skipped)
    )
    return VerificationResult(
        started_at=1000.0,
        elapsed_seconds=0.5,
        total=total,
        passed=len(passed),
        failed=failed,
        skipped=skipped,
        unknown=unknown,
        passed_attempts=passed,
        is_acceptance_pass=is_pass,
        saved_at=1000.0,
        last_tick_age_seconds=5.0,
        platform="compose",
        controller_reachable=controller_reachable,
        error=error,
        detail_lines=(
            f"orchestrator: {len(passed)}/{total} promises ok (platform=compose)",
        ),
    )


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


class TestExitCodes:
    def test_acceptance_pass_returns_0(self, capsys, monkeypatch) -> None:
        result = _result(passed=(_attempt("a"), _attempt("b")), is_pass=True)
        verifier = MagicMock()
        verifier.verify.return_value = result
        with patch.object(cli, "FreshInstallVerifier", return_value=verifier):
            code = cli.main(["--controller-url", "http://x"])
        assert code == 0

    def test_failed_promise_returns_1(self, capsys) -> None:
        result = _result(
            passed=(_attempt("a"),),
            failed=(_attempt("b", status="failed_transient"),),
            is_pass=False,
        )
        verifier = MagicMock()
        verifier.verify.return_value = result
        with patch.object(cli, "FreshInstallVerifier", return_value=verifier):
            code = cli.main(["--controller-url", "http://x"])
        assert code == 1

    def test_unreachable_returns_2(self, capsys) -> None:
        result = _result(
            controller_reachable=False, total_override=0,
            error="ConnectionRefused", is_pass=False,
        )
        verifier = MagicMock()
        verifier.verify.return_value = result
        with patch.object(cli, "FreshInstallVerifier", return_value=verifier):
            code = cli.main(["--controller-url", "http://x"])
        assert code == 2

    def test_state_not_yet_persisted_returns_2(self, capsys) -> None:
        # Controller reachable, 503 from endpoint, total=0 + error set.
        result = _result(
            total_override=0, error="not yet persisted", is_pass=False,
        )
        verifier = MagicMock()
        verifier.verify.return_value = result
        with patch.object(cli, "FreshInstallVerifier", return_value=verifier):
            code = cli.main(["--controller-url", "http://x"])
        assert code == 2


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------


class TestTextOutput:
    def test_summary_line_emitted(self, capsys) -> None:
        result = _result(passed=(_attempt("a"), _attempt("b")), is_pass=True)
        verifier = MagicMock()
        verifier.verify.return_value = result
        with patch.object(cli, "FreshInstallVerifier", return_value=verifier):
            cli.main(["--controller-url", "http://x"])
        out = capsys.readouterr().out
        assert "2/2 promises ok" in out
        assert "2/2 promises pass" in out

    def test_failure_line_lists_failing_promise(self, capsys) -> None:
        result = _result(
            passed=(_attempt("a"),),
            failed=(_attempt("bazarr-language-profile",
                             status="failed_transient"),),
            is_pass=False,
        )
        # Detail lines are passed through from the verifier; pin the
        # CLI's "tally summary" line at the end so CI grep patterns
        # are stable.
        result = VerificationResult(
            **{**result.__dict__,
               "detail_lines": (
                   "orchestrator: 1/2 promises ok (platform=compose)",
                   "  FAIL  bazarr-language-profile: failed_transient",
               )},
        )
        verifier = MagicMock()
        verifier.verify.return_value = result
        with patch.object(cli, "FreshInstallVerifier", return_value=verifier):
            cli.main(["--controller-url", "http://x"])
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "bazarr-language-profile" in out
        # Summary tally line is on the result.
        assert "1/2 promises pass" in out


class TestJsonOutput:
    def test_json_output_parses_to_jsonable_dict(self, capsys) -> None:
        result = _result(passed=(_attempt("a"),), is_pass=True)
        verifier = MagicMock()
        verifier.verify.return_value = result
        with patch.object(cli, "FreshInstallVerifier", return_value=verifier):
            cli.main(["--controller-url", "http://x", "--json"])
        out = capsys.readouterr().out
        payload = json.loads(out)
        # Pin the field set CI consumers will read.
        assert payload["is_acceptance_pass"] is True
        assert payload["passed"] == 1
        assert payload["total"] == 1
        assert payload["controller_reachable"] is True
        assert payload["platform"] == "compose"
        assert isinstance(payload["passed_attempts"], list)


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


class TestFilterFlag:
    def test_filter_narrows_to_matching_promise_ids(self, capsys) -> None:
        # Three passed (one bazarr, two non-bazarr) — filter for bazarr.
        result = _result(
            passed=(
                _attempt("bazarr-language-profile"),
                _attempt("jellyfin-running"),
                _attempt("qbit-running"),
            ),
            is_pass=True,
        )
        verifier = MagicMock()
        verifier.verify.return_value = result
        with patch.object(cli, "FreshInstallVerifier", return_value=verifier):
            code = cli.main([
                "--controller-url", "http://x",
                "--filter", "bazarr",
            ])
        out = capsys.readouterr().out
        assert code == 0
        assert "1/1 promises pass" in out
        assert "filter='bazarr'" in out

    def test_filter_returns_failure_when_only_match_failed(self, capsys) -> None:
        result = _result(
            passed=(_attempt("jellyfin-running"),),
            failed=(_attempt("bazarr-language-profile",
                             status="failed_transient"),),
            is_pass=False,
        )
        verifier = MagicMock()
        verifier.verify.return_value = result
        with patch.object(cli, "FreshInstallVerifier", return_value=verifier):
            code = cli.main([
                "--controller-url", "http://x",
                "--filter", "bazarr",
            ])
        out = capsys.readouterr().out
        assert code == 1
        assert "FAIL" in out
        assert "bazarr" in out
        # The non-matching jellyfin-running shouldn't show up.
        assert "jellyfin-running" not in out


class TestEnvFallback:
    def test_admin_pass_falls_back_to_env(self, monkeypatch) -> None:
        monkeypatch.setenv("ADMIN_PASS", "from-env")
        result = _result(passed=(_attempt("a"),), is_pass=True)
        verifier = MagicMock()
        verifier.verify.return_value = result
        with patch.object(cli, "FreshInstallVerifier",
                          return_value=verifier) as patched_cls:
            cli.main(["--controller-url", "http://x"])
        # Pin the constructor kwargs the CLI passes to the verifier.
        kwargs = patched_cls.call_args.kwargs
        assert kwargs["admin_pass"] == "from-env"

    def test_explicit_flag_beats_env(self, monkeypatch) -> None:
        monkeypatch.setenv("ADMIN_PASS", "from-env")
        result = _result(passed=(_attempt("a"),), is_pass=True)
        verifier = MagicMock()
        verifier.verify.return_value = result
        with patch.object(cli, "FreshInstallVerifier",
                          return_value=verifier) as patched_cls:
            cli.main(["--controller-url", "http://x",
                      "--admin-pass", "from-flag"])
        kwargs = patched_cls.call_args.kwargs
        assert kwargs["admin_pass"] == "from-flag"


class TestWaitFlag:
    def test_wait_above_zero_invokes_wait_for_steady_state(self) -> None:
        result = _result(passed=(_attempt("a"),), is_pass=True)
        verifier = MagicMock()
        verifier.wait_for_steady_state.return_value = result
        with patch.object(cli, "FreshInstallVerifier", return_value=verifier):
            cli.main(["--controller-url", "http://x", "--wait", "60"])
        verifier.wait_for_steady_state.assert_called_once()
        verifier.verify.assert_not_called()

    def test_wait_zero_uses_single_shot_verify(self) -> None:
        result = _result(passed=(_attempt("a"),), is_pass=True)
        verifier = MagicMock()
        verifier.verify.return_value = result
        with patch.object(cli, "FreshInstallVerifier", return_value=verifier):
            cli.main(["--controller-url", "http://x"])
        verifier.verify.assert_called_once()
        verifier.wait_for_steady_state.assert_not_called()


class TestLegacyFlagCompat:
    def test_compose_file_flag_is_accepted_and_ignored(self) -> None:
        # The wrapper script passes --compose-file regardless of the
        # underlying CLI's needs. Accept it without error.
        result = _result(passed=(_attempt("a"),), is_pass=True)
        verifier = MagicMock()
        verifier.verify.return_value = result
        with patch.object(cli, "FreshInstallVerifier", return_value=verifier):
            code = cli.main([
                "--controller-url", "http://x",
                "--compose-file", "deploy/compose/docker-compose.yml",
            ])
        assert code == 0

    def test_k8s_and_unified_flags_are_accepted_and_ignored(self) -> None:
        # The legacy CLI dispatched on --k8s; the new CLI doesn't
        # need to (the controller knows). Accept silently for wrapper
        # parity.
        result = _result(passed=(_attempt("a"),), is_pass=True)
        verifier = MagicMock()
        verifier.verify.return_value = result
        with patch.object(cli, "FreshInstallVerifier", return_value=verifier):
            code = cli.main([
                "--controller-url", "http://x", "--k8s", "--unified",
            ])
        assert code == 0
