"""Tests for ``orchestrator:satisfy-shadow`` job handler — ADR-0003 Phase 4c.

Pin the contract that the auto-heal cycle relies on:
  * Handler returns the framework-expected result dict.
  * ``dry_run=True`` is hardcoded (Phase 4c shadow mode); a future
    Phase 5 commit will flip this. The test pins the current value
    so the flip is intentional.
  * ``platform`` detected from ``KUBERNETES_SERVICE_HOST`` /
    ``MEDIA_STACK_RUNTIME``.
  * Per-promise records are NOT emitted (the no-op default keeps
    run-history bounded).
  * Handler is registered in the guardrails contract YAML.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class _StubCtx:
    pass


class TestPlatformDetection:
    def test_kubernetes_service_host_implies_k8s(self, monkeypatch) -> None:
        monkeypatch.delenv("MEDIA_STACK_RUNTIME", raising=False)
        monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
        from media_stack.application.jobs.orchestrator_satisfy import (
            _detect_platform,
        )
        assert _detect_platform() == "k8s"

    def test_no_env_defaults_to_compose(self, monkeypatch) -> None:
        monkeypatch.delenv("MEDIA_STACK_RUNTIME", raising=False)
        monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
        from media_stack.application.jobs.orchestrator_satisfy import (
            _detect_platform,
        )
        assert _detect_platform() == "compose"

    def test_explicit_override_wins(self, monkeypatch) -> None:
        # Operator may set MEDIA_STACK_RUNTIME=compose on a host that
        # otherwise looks like k8s (sidecar deploys, CI runners, etc.).
        monkeypatch.setenv("MEDIA_STACK_RUNTIME", "compose")
        monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
        from media_stack.application.jobs.orchestrator_satisfy import (
            _detect_platform,
        )
        assert _detect_platform() == "compose"


class TestHandlerContract:
    def test_calls_satisfy_promises_in_dry_run(self) -> None:
        # Phase 4c MUST run shadow mode (dry_run=True). Phase 5 flips
        # this; if you're updating this test for Phase 5, double-
        # check that legacy ensurers have been retired so the
        # orchestrator running them isn't a double-mutation.
        fake_summary = MagicMock()
        fake_summary.has_failures = False
        fake_summary.elapsed_seconds = 0.5
        fake_summary.summary_line.return_value = "10 ok"
        fake_summary.total = 10
        fake_summary.ok = 10
        fake_summary.failed_transient = 0
        fake_summary.failed_permanent = 0
        fake_summary.dep_failed = 0
        fake_summary.skipped_cooldown = 0
        fake_summary.skipped_platform = 0
        fake_summary.unknown = 0

        with patch(
            "media_stack.application.services.orchestrator.satisfy_promises",
            return_value=fake_summary,
        ) as mock_satisfy:
            from media_stack.application.jobs.orchestrator_satisfy import (
                satisfy_shadow,
            )
            result = satisfy_shadow(_StubCtx())

        kwargs = mock_satisfy.call_args.kwargs
        assert kwargs["dry_run"] is True, (
            "Phase 4c shadow mode REQUIRES dry_run=True. Phase 5 flips "
            "this — retire the legacy hooks first."
        )
        assert kwargs["platform"] in ("compose", "k8s")
        assert result["status"] == "ok"
        assert result["total"] == 10
        assert result["ok_count"] == 10

    def test_per_promise_emit_is_no_op(self) -> None:
        # The handler MUST pass a no-op history_emit so per-promise
        # records don't flood run-history. Cooldown state file holds
        # the per-promise current state; tick-level record holds the
        # aggregate.
        from media_stack.application.jobs.orchestrator_satisfy import _no_op_emit
        # Calling it should do nothing (return None) without raising.
        assert _no_op_emit(None, None, "probe") is None

    def test_returns_summary_fields_for_run_history(self) -> None:
        # JobRunner stores the result dict's fields verbatim — they
        # surface in /api/jobs/history. Operators chart "ok vs failed
        # over time" without parsing logs.
        fake_summary = MagicMock()
        fake_summary.has_failures = True
        fake_summary.elapsed_seconds = 1.5
        fake_summary.summary_line.return_value = "8 ok, 2 transient"
        fake_summary.total = 10
        fake_summary.ok = 8
        fake_summary.failed_transient = 2
        fake_summary.failed_permanent = 0
        fake_summary.dep_failed = 0
        fake_summary.skipped_cooldown = 0
        fake_summary.skipped_platform = 0
        fake_summary.unknown = 0

        with patch(
            "media_stack.application.services.orchestrator.satisfy_promises",
            return_value=fake_summary,
        ):
            from media_stack.application.jobs.orchestrator_satisfy import (
                satisfy_shadow,
            )
            result = satisfy_shadow(_StubCtx())

        # Operator-visible bucket counts
        assert result["total"] == 10
        assert result["ok_count"] == 8
        assert result["failed_transient_count"] == 2
        assert result["failed_permanent_count"] == 0
        assert result["elapsed"] == pytest.approx(1.5)


class TestContractRegistration:
    def test_handler_registered_in_guardrails_yaml(self) -> None:
        # The auto-heal cycle calls run_job("orchestrator:satisfy-shadow"),
        # which resolves through the contract registry. If the YAML
        # entry drifts or moves, run_job returns "unknown job" and
        # the auto-heal silently skips us every minute.
        from pathlib import Path
        import yaml

        contract = (
            Path(__file__).resolve().parents[3]
            / "contracts" / "services" / "guardrails.yaml"
        )
        data = yaml.safe_load(contract.read_text(encoding="utf-8"))
        jobs = (data.get("plugin") or {}).get("jobs") or {}
        assert "orchestrator:satisfy-shadow" in jobs, (
            "orchestrator:satisfy-shadow not registered in "
            "contracts/services/guardrails.yaml::plugin.jobs. The "
            "auto-heal cycle's run_job call would fail with 'unknown "
            "job' on every tick."
        )
        handler_path = jobs["orchestrator:satisfy-shadow"].get("handler", "")
        assert handler_path == (
            "media_stack.application.jobs.orchestrator_satisfy:satisfy_shadow"
        ), (
            f"unexpected handler path: {handler_path!r}. Auto-heal "
            "won't reach the orchestrator if the path drifts."
        )
