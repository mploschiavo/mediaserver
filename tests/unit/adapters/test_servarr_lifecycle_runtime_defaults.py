"""Tests for ``ServarrLifecycle.probe_quality_profiles`` /
``probe_import_lists_auto`` / ``ensure_runtime_defaults`` — the
lifecycle-method port of the legacy ``apply_arr_runtime_defaults``
job handler (ADR-0005 Phase 3 cutover, three promises sharing one
ensurer per the Bazarr monolithic-handler lesson).

Three families of behavior:

  * Unsupported services short-circuit: ``probe_import_lists_auto``
    returns ``ok`` with ``reason: unsupported_service`` for non-radarr
    service ids (sonarr has no import-lists promise; lifecycle
    instances would never call it via the orchestrator, but the
    wirer guards anyway — pattern from ``JellyfinNotifierWirer``).
  * Probes map the *arr's quality-profile / import-list response to
    the tri-state ProbeResult (ok=non-empty / all-auto, failed=empty
    / missing-auto, unknown=can't reach / no key).
  * Ensurer is a wide-handler delegator (Jellyseerr pattern). The
    legacy ``apply_arr_runtime_defaults`` does ~100 LoC of multi-arr
    orchestration; the wirer takes injected ``configure_handler`` +
    ``job_context_factory`` callables so the legacy implementation
    stays the source of truth.

No real HTTP — urllib is mocked. The probe parses JSON shapes that
match what real *arrs return.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from media_stack.adapters.servarr.lifecycle import ServarrLifecycle
from media_stack.adapters.servarr.runtime_defaults_wiring import (
    RuntimeDefaultsWirer,
)
from media_stack.domain.services import OrchestrationContext


_ARR_KEY = "arr-test-key-abcdef"


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
    *, arr_key: str = _ARR_KEY,
    port: int = 8989,
) -> OrchestrationContext:
    """Build an ``OrchestrationContext`` pre-populated with the *arr
    api key in ``secrets``."""
    secrets: dict[str, str] = {}
    if arr_key:
        secrets[f"{service_id.upper()}_API_KEY"] = arr_key
    cfg = {
        "host": service_id,
        "port": port,
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


# --- Probe: quality_profiles -----------------------------------------


class TestProbeQualityProfiles:
    """``probe_quality_profiles`` is supported on sonarr + radarr;
    its tri-state shape is identical to ``IndexerPipelineWirer``'s."""

    @patch("urllib.request.urlopen")
    def test_ok_when_profile_list_non_empty_for_sonarr(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps([
            {"id": 1, "name": "HD-1080p"},
            {"id": 2, "name": "Any"},
        ]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("sonarr")
        result = sl.probe_quality_profiles(_ctx("sonarr"))
        assert result.is_ok
        assert result.evidence.get("profile_count") == 2

    @patch("urllib.request.urlopen")
    def test_ok_when_profile_list_non_empty_for_radarr(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps([{"id": 1, "name": "HD-1080p"}]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("radarr")
        result = sl.probe_quality_profiles(_ctx("radarr", port=7878))
        assert result.is_ok
        assert result.evidence.get("profile_count") == 1

    @patch("urllib.request.urlopen")
    def test_failed_when_profile_list_empty(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.return_value = _http_response(b"[]")
        sl = ServarrLifecycle("sonarr")
        result = sl.probe_quality_profiles(_ctx("sonarr"))
        assert result.status == "failed"
        assert result.evidence.get("profile_count") == 0

    @patch("urllib.request.urlopen")
    def test_unknown_when_arr_unreachable(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.side_effect = urllib.error.URLError("dns")
        sl = ServarrLifecycle("sonarr")
        result = sl.probe_quality_profiles(_ctx("sonarr"))
        assert result.status == "unknown"

    def test_unknown_when_no_arr_api_key(self) -> None:
        # No api key in secrets, no env var — unknown (orchestrator
        # retries after probe_has_api_key reaches ok).
        sl = ServarrLifecycle("sonarr")
        result = sl.probe_quality_profiles(_ctx("sonarr", arr_key=""))
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
        result = sl.probe_quality_profiles(ctx)
        assert result.status == "unknown"

    @pytest.mark.parametrize("sid", ["lidarr", "readarr", "prowlarr"])
    def test_unsupported_services_short_circuit_ok(self, sid: str) -> None:
        # No promise binds quality-profiles on lidarr / readarr /
        # prowlarr today — the wirer short-circuits ``ok`` so the
        # orchestrator records "no signal here" rather than firing
        # an HTTP probe at the wrong service.
        sl = ServarrLifecycle(sid)
        result = sl.probe_quality_profiles(_ctx(sid))
        assert result.is_ok
        assert result.evidence.get("reason") == "unsupported_service"


# --- Probe: import_lists_auto ----------------------------------------


class TestProbeImportListsAuto:

    @pytest.mark.parametrize(
        "sid", ["sonarr", "lidarr", "readarr", "prowlarr"],
    )
    def test_unsupported_services_short_circuit_ok(self, sid: str) -> None:
        # Only radarr-import-lists-auto exists today — every other
        # *arr short-circuits ``ok`` with ``reason: unsupported_service``.
        sl = ServarrLifecycle(sid)
        result = sl.probe_import_lists_auto(_ctx(sid))
        assert result.is_ok
        assert result.evidence.get("reason") == "unsupported_service"

    @patch("urllib.request.urlopen")
    def test_ok_when_every_enabled_list_has_enable_auto(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps([
            {"name": "TMDb Popular", "enabled": True, "enableAuto": True},
            {"name": "Trakt Trending", "enabled": True, "enableAuto": True},
            # Disabled lists are operator-intent — the probe ignores
            # whether enableAuto is set on those.
            {"name": "Old List", "enabled": False, "enableAuto": False},
        ]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("radarr")
        result = sl.probe_import_lists_auto(_ctx("radarr", port=7878))
        assert result.is_ok
        assert result.evidence.get("enabled_count") == 2

    @patch("urllib.request.urlopen")
    def test_failed_when_an_enabled_list_lacks_enable_auto(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps([
            {"name": "TMDb Popular", "enabled": True, "enableAuto": True},
            {"name": "Trakt Trending", "enabled": True, "enableAuto": False},
        ]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("radarr")
        result = sl.probe_import_lists_auto(_ctx("radarr", port=7878))
        assert result.status == "failed"
        assert "Trakt Trending" in result.evidence.get("missing_auto", [])

    @patch("urllib.request.urlopen")
    def test_failed_when_no_enabled_lists(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps([
            {"name": "Old List", "enabled": False, "enableAuto": False},
        ]).encode()
        mock_open.return_value = _http_response(body)

        sl = ServarrLifecycle("radarr")
        result = sl.probe_import_lists_auto(_ctx("radarr", port=7878))
        assert result.status == "failed"
        assert result.evidence.get("enabled_count") == 0

    @patch("urllib.request.urlopen")
    def test_failed_when_list_is_empty(
        self, mock_open: MagicMock,
    ) -> None:
        # Empty list ⇒ no seeded import lists yet — failed (the
        # promise asserts presence + auto-on; the upstream
        # seed-arr-import-lists job is responsible for populating
        # them).
        mock_open.return_value = _http_response(b"[]")
        sl = ServarrLifecycle("radarr")
        result = sl.probe_import_lists_auto(_ctx("radarr", port=7878))
        assert result.status == "failed"
        assert result.evidence.get("import_list_count") == 0

    @patch("urllib.request.urlopen")
    def test_unknown_when_arr_unreachable(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.side_effect = urllib.error.URLError("dns")
        sl = ServarrLifecycle("radarr")
        result = sl.probe_import_lists_auto(_ctx("radarr", port=7878))
        assert result.status == "unknown"


# --- Ensurer (shared) -------------------------------------------------


class TestEnsureRuntimeDefaults:
    """The shared ensurer delegates to the legacy
    ``apply_arr_runtime_defaults`` handler. Tests inject a fake
    handler via the wirer constructor so the tests don't pull in
    the heavyweight JobContext + cfg machinery."""

    def test_delegates_to_injected_handler(self) -> None:
        recorded: dict = {}

        def fake_handler(job_ctx) -> dict:
            recorded["called_with"] = job_ctx
            return {
                "action": "apply-arr-runtime-defaults",
                "updated": {"radarr": 2},
                "usenet_enabled": False,
            }

        wirer = RuntimeDefaultsWirer(
            configure_handler=fake_handler,
            job_context_factory=lambda: "FAKE_JOB_CTX",
        )
        outcome = wirer.ensure_runtime_defaults(
            "sonarr", _ARR_KEY, _ctx("sonarr"),
        )
        assert outcome.ok
        assert recorded["called_with"] == "FAKE_JOB_CTX"
        # Summary surfaces in evidence so run-history records the
        # per-arr counts.
        assert "summary" in outcome.evidence
        assert outcome.evidence["summary"]["updated"] == {"radarr": 2}
        assert outcome.evidence.get("service_id") == "sonarr"

    def test_transient_failure_when_handler_raises(self) -> None:
        def boom(job_ctx) -> dict:
            raise RuntimeError("legacy handler exploded")

        wirer = RuntimeDefaultsWirer(
            configure_handler=boom,
            job_context_factory=lambda: object(),
        )
        outcome = wirer.ensure_runtime_defaults(
            "sonarr", _ARR_KEY, _ctx("sonarr"),
        )
        assert not outcome.ok
        assert outcome.transient is True
        assert "exploded" in outcome.error

    def test_called_once_per_promise_dispatch_is_idempotent(self) -> None:
        # All three promises route through the same ensurer; the
        # second + third dispatch see ``updated={}`` from the legacy
        # handler (each invariant short-circuits when already
        # correct). Verify that an empty summary still produces a
        # success outcome — orchestrator's per-promise success bit
        # hinges on this.
        def fake_handler(job_ctx) -> dict:
            return {
                "action": "apply-arr-runtime-defaults",
                "updated": {},
                "usenet_enabled": True,
            }

        wirer = RuntimeDefaultsWirer(
            configure_handler=fake_handler,
            job_context_factory=lambda: object(),
        )
        outcome = wirer.ensure_runtime_defaults(
            "radarr", _ARR_KEY, _ctx("radarr", port=7878),
        )
        assert outcome.ok
        assert outcome.evidence["summary"]["updated"] == {}


# --- Lifecycle delegation --------------------------------------------


class TestLifecycleDelegators:
    """``ServarrLifecycle`` exposes the wirer methods via thin
    delegators. These tests pin the dispatch — the lifecycle
    methods MUST go through the module singleton wirer, not
    re-implement the probe / ensure logic inline."""

    @patch(
        "media_stack.adapters.servarr.lifecycle"
        "._RUNTIME_DEFAULTS_WIRER"
    )
    def test_probe_quality_profiles_delegates_to_wirer(
        self, mock_wirer: MagicMock,
    ) -> None:
        sl = ServarrLifecycle("sonarr")
        ctx = _ctx("sonarr")
        sl.probe_quality_profiles(ctx)
        mock_wirer.probe_quality_profiles.assert_called_once_with(
            "sonarr", _ARR_KEY, ctx,
        )

    @patch(
        "media_stack.adapters.servarr.lifecycle"
        "._RUNTIME_DEFAULTS_WIRER"
    )
    def test_probe_import_lists_auto_delegates_to_wirer(
        self, mock_wirer: MagicMock,
    ) -> None:
        sl = ServarrLifecycle("radarr")
        ctx = _ctx("radarr", port=7878)
        sl.probe_import_lists_auto(ctx)
        mock_wirer.probe_import_lists_auto.assert_called_once_with(
            "radarr", _ARR_KEY, ctx,
        )

    @patch(
        "media_stack.adapters.servarr.lifecycle"
        "._RUNTIME_DEFAULTS_WIRER"
    )
    def test_ensure_runtime_defaults_delegates_to_wirer(
        self, mock_wirer: MagicMock,
    ) -> None:
        sl = ServarrLifecycle("sonarr")
        ctx = _ctx("sonarr")
        sl.ensure_runtime_defaults(ctx)
        mock_wirer.ensure_runtime_defaults.assert_called_once_with(
            "sonarr", _ARR_KEY, ctx,
        )
