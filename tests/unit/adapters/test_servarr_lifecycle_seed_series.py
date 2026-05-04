"""Tests for ``ServarrLifecycle.probe_has_series`` and
``ensure_has_series`` — the lifecycle-method port of the legacy
``ensure_sonarr_seed_series`` job handler (ADR-0005 Phase 3
cutover, wide-handler delegation addendum).

Three families of behavior:

  * Unsupported services (radarr, lidarr, readarr, prowlarr) short-
    circuit to ok / success — only Sonarr carries a seed-series
    promise. The lifecycle methods exist on every ServarrLifecycle
    instance (one class covers all five *arrs) but become no-ops
    for non-sonarr ids.
  * Probe maps Sonarr's ``/series`` list length to the tri-state
    ProbeResult: ``ok`` when count >= 5 (matches the previous
    ``http_json`` probe's threshold verbatim), ``failed`` when
    short, ``unknown`` when prereqs missing or the probe can't
    reach Sonarr.
  * Ensurer is the wide-handler delegation pattern (Jellyseerr's
    lesson from f241f639): probe-skip when already configured;
    otherwise build a JobContext via the injected factory and call
    the injected handler. Factory / handler exceptions surface as
    transient failures.

No real HTTP and no real Sonarr API roundtrips — urllib + the
configure handler + JobContext factory are all mocked / injected.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from media_stack.adapters.servarr.lifecycle import ServarrLifecycle
from media_stack.adapters.servarr.seed_series_wiring import (
    SeedSeriesWirer,
)
from media_stack.domain.services import OrchestrationContext


_SONARR_KEY = "sonarr-test-key-1234567890"


@pytest.fixture(autouse=True)
def _clear_envs():
    import os as _os
    yield
    for var in (
        "SONARR_API_KEY", "RADARR_API_KEY", "LIDARR_API_KEY",
        "READARR_API_KEY", "PROWLARR_API_KEY",
    ):
        _os.environ.pop(var, None)


def _ctx(
    service_id: str = "sonarr",
    *,
    arr_key: str = _SONARR_KEY,
) -> OrchestrationContext:
    """Build an ``OrchestrationContext`` pre-populated with the
    Sonarr api key in ``secrets``."""
    secrets: dict[str, str] = {}
    if arr_key:
        secrets[f"{service_id.upper()}_API_KEY"] = arr_key
    cfg = {
        "host": service_id,
        "port": 8989,
        "scheme": "http",
        "api_key_env": f"{service_id.upper()}_API_KEY",
    }
    return OrchestrationContext(
        service_id=service_id,
        config=cfg,
        secrets=secrets,
        now=lambda: 1700000000.0,
    )


def _http_response(body: bytes, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda *_: None
    return resp


def _series_payload(count: int) -> bytes:
    return json.dumps([
        {"id": idx, "title": f"Series {idx}", "tvdbId": 1000 + idx}
        for idx in range(count)
    ]).encode()


# --- Unsupported-service short-circuit --------------------------------


class TestUnsupportedServicesShortCircuit:
    """Only ``sonarr`` carries a seed-series promise. The methods
    exist on every ServarrLifecycle (one class covers all five *arrs)
    but for non-sonarr ids both methods short-circuit without any
    HTTP call or handler invocation."""

    @pytest.mark.parametrize(
        "sid", ["radarr", "lidarr", "readarr", "prowlarr"],
    )
    def test_probe_returns_ok_with_unsupported_reason(
        self, sid: str,
    ) -> None:
        sl = ServarrLifecycle(sid)
        result = sl.probe_has_series(_ctx(sid))
        assert result.is_ok
        assert result.evidence.get("reason") == "unsupported_service"

    @pytest.mark.parametrize(
        "sid", ["radarr", "lidarr", "readarr", "prowlarr"],
    )
    def test_ensure_returns_success_with_unsupported_reason(
        self, sid: str,
    ) -> None:
        # The ensurer's short-circuit fires BEFORE the lazy import
        # of the legacy handler; verify by patching urllib so any
        # accidental probe call would crash, then asserting it
        # didn't.
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = AssertionError(
                "probe must not run for unsupported service",
            )
            sl = ServarrLifecycle(sid)
            outcome = sl.ensure_has_series(_ctx(sid))
        assert outcome.ok
        assert outcome.evidence.get("reason") == "unsupported_service"


# --- Probe behavior ---------------------------------------------------


class TestProbeHasSeries:

    @patch("urllib.request.urlopen")
    def test_ok_when_threshold_met(
        self, mock_open: MagicMock,
    ) -> None:
        # 5 series matches the previous ``http_json`` assert verbatim.
        mock_open.return_value = _http_response(_series_payload(5))

        sl = ServarrLifecycle("sonarr")
        result = sl.probe_has_series(_ctx("sonarr"))
        assert result.is_ok
        assert result.evidence.get("series_count") == 5
        assert result.evidence.get("threshold") == 5

    @patch("urllib.request.urlopen")
    def test_ok_when_threshold_exceeded(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.return_value = _http_response(_series_payload(12))

        sl = ServarrLifecycle("sonarr")
        result = sl.probe_has_series(_ctx("sonarr"))
        assert result.is_ok
        assert result.evidence.get("series_count") == 12

    @patch("urllib.request.urlopen")
    def test_failed_when_short_of_threshold(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.return_value = _http_response(_series_payload(3))

        sl = ServarrLifecycle("sonarr")
        result = sl.probe_has_series(_ctx("sonarr"))
        assert result.status == "failed"
        assert result.evidence.get("series_count") == 3

    @patch("urllib.request.urlopen")
    def test_failed_when_empty_series_list(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.return_value = _http_response(b"[]")

        sl = ServarrLifecycle("sonarr")
        result = sl.probe_has_series(_ctx("sonarr"))
        assert result.status == "failed"
        assert result.evidence.get("series_count") == 0

    @patch("urllib.request.urlopen")
    def test_unknown_when_sonarr_unreachable(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.side_effect = urllib.error.URLError("dns")

        sl = ServarrLifecycle("sonarr")
        result = sl.probe_has_series(_ctx("sonarr"))
        assert result.status == "unknown"

    def test_unknown_when_no_arr_api_key(self) -> None:
        sl = ServarrLifecycle("sonarr")
        result = sl.probe_has_series(_ctx("sonarr", arr_key=""))
        assert result.status == "unknown"
        assert "api key" in result.detail.lower()

    def test_unknown_when_no_host_in_config(self) -> None:
        ctx = OrchestrationContext(
            service_id="sonarr",
            config={"port": 8989},
            secrets={"SONARR_API_KEY": "k"},
            now=lambda: 0.0,
        )
        sl = ServarrLifecycle("sonarr")
        result = sl.probe_has_series(ctx)
        assert result.status == "unknown"

    @patch("urllib.request.urlopen")
    def test_endpoint_targets_prefixed_path(
        self, mock_open: MagicMock,
    ) -> None:
        # Sonarr's ``/api/v3/series`` direct path 307s; the wirer
        # always goes through the ``/app/sonarr/`` URL base prefix
        # like IndexerPipelineWirer + the legacy handler.
        mock_open.return_value = _http_response(_series_payload(7))

        sl = ServarrLifecycle("sonarr")
        sl.probe_has_series(_ctx("sonarr"))
        assert mock_open.call_count == 1
        req = mock_open.call_args.args[0]
        assert "/app/sonarr/api/v3/series" in req.full_url


# --- Ensurer behavior (wide-handler delegation) ----------------------


class TestEnsureHasSeries:
    """``ensure_has_series`` is the wide-handler delegation flow:
    probe-skip when already configured; otherwise build a JobContext
    via the injected factory and call the injected handler. The
    lifecycle method itself lazy-imports the real handler +
    JobContext through the ``services/`` shim path; tests exercise
    the wirer directly with mock callables to keep the assertions
    focused on delegation semantics."""

    @patch("urllib.request.urlopen")
    def test_idempotent_skip_when_probe_already_ok(
        self, mock_open: MagicMock,
    ) -> None:
        # Probe sees 8 series (>= 5 threshold). Ensurer must NOT
        # call the handler.
        mock_open.return_value = _http_response(_series_payload(8))
        wirer = SeedSeriesWirer()
        configure = MagicMock(return_value=None)
        factory = MagicMock()
        outcome = wirer.ensure(
            "sonarr", _SONARR_KEY, _ctx("sonarr"),
            configure_handler=configure,
            job_context_factory=factory,
        )
        assert outcome.ok
        assert outcome.evidence.get("reason") == "already_configured"
        assert outcome.evidence.get("series_count") == 8
        configure.assert_not_called()
        factory.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_invokes_handler_when_probe_failed(
        self, mock_open: MagicMock,
    ) -> None:
        # Probe sees 0 series. Ensurer builds the JobContext and
        # forwards it to the handler.
        mock_open.return_value = _http_response(b"[]")
        wirer = SeedSeriesWirer()
        sentinel_ctx = object()
        factory = MagicMock(return_value=sentinel_ctx)
        configure = MagicMock(return_value={"action": "ok", "added": 5})
        outcome = wirer.ensure(
            "sonarr", _SONARR_KEY, _ctx("sonarr"),
            configure_handler=configure,
            job_context_factory=factory,
        )
        assert outcome.ok
        configure.assert_called_once_with(sentinel_ctx)
        assert outcome.evidence.get("result") == {"action": "ok", "added": 5}
        assert outcome.evidence.get("service_id") == "sonarr"

    @patch("urllib.request.urlopen")
    def test_invokes_handler_when_probe_unknown(
        self, mock_open: MagicMock,
    ) -> None:
        # Probe URL builds, but Sonarr isn't reachable yet — probe is
        # ``unknown``, NOT ``ok``. Ensurer must still attempt the
        # handler (the orchestrator dispatches based on probe-not-ok,
        # and the handler itself bails out cleanly when prereqs are
        # missing).
        mock_open.side_effect = urllib.error.URLError("warmup")
        wirer = SeedSeriesWirer()
        configure = MagicMock(return_value={"skipped": "no url/key"})
        factory = MagicMock(return_value=object())
        outcome = wirer.ensure(
            "sonarr", _SONARR_KEY, _ctx("sonarr"),
            configure_handler=configure,
            job_context_factory=factory,
        )
        assert outcome.ok
        configure.assert_called_once()

    def test_transient_when_factory_raises(self) -> None:
        wirer = SeedSeriesWirer()

        def boom() -> None:
            raise RuntimeError("env not ready")

        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = _http_response(b"[]")
            outcome = wirer.ensure(
                "sonarr", _SONARR_KEY, _ctx("sonarr"),
                configure_handler=MagicMock(),
                job_context_factory=boom,
            )
        assert not outcome.ok
        assert outcome.transient is True
        assert "JobContext" in outcome.error

    def test_transient_when_handler_raises(self) -> None:
        wirer = SeedSeriesWirer()

        def configure(ctx) -> None:
            raise RuntimeError("Sonarr API 503")

        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = _http_response(b"[]")
            outcome = wirer.ensure(
                "sonarr", _SONARR_KEY, _ctx("sonarr"),
                configure_handler=configure,
                job_context_factory=MagicMock(return_value=object()),
            )
        assert not outcome.ok
        assert outcome.transient is True
        assert "ensure_sonarr_seed_series raised" in outcome.error


# --- Lifecycle-level binding ------------------------------------------


class TestLifecycleDelegation:
    """The lifecycle methods are thin delegators — they pass
    service_id + discovered api key + ctx straight through to the
    module-level wirer singleton. ``ensure_has_series`` additionally
    lazy-imports the legacy handler + JobContext through the
    ``services/`` shim path (NOT ``application/``) so the
    adapters → application hexagon ratchet stays clean."""

    @patch("urllib.request.urlopen")
    def test_probe_returns_probe_result_through_lifecycle(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.return_value = _http_response(_series_payload(6))
        sl = ServarrLifecycle("sonarr")
        result = sl.probe_has_series(_ctx("sonarr"))
        assert result.is_ok
        assert result.evidence.get("series_count") == 6

    @patch("urllib.request.urlopen")
    def test_ensure_lifecycle_path_resolves_handler(
        self, mock_open: MagicMock,
    ) -> None:
        # Force the probe-ok path so we exercise the lazy import of
        # the legacy handler + JobContext (verifies the shim path
        # resolves) without actually invoking them.
        mock_open.return_value = _http_response(_series_payload(7))
        sl = ServarrLifecycle("sonarr")
        outcome = sl.ensure_has_series(_ctx("sonarr"))
        assert outcome.ok
        assert outcome.evidence.get("reason") == "already_configured"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
