"""Tests for ``MaintainerrLifecycle.{probe|ensure}_rules_linked_to_arr``
— the lifecycle-method port of the Maintainerr ``radarrSettingsId`` /
``sonarrSettingsId`` rule-link wiring (ADR-0005 Phase 3 cutover, wide-
handler delegation).

Two surfaces:

  * Probe — hits the LIVE
    ``/app/maintainerr/api/collections`` endpoint and asserts at least
    one movie collection links to ``radarrSettingsId`` or one show
    collection links to ``sonarrSettingsId``. Tri-state semantics:
    ``unknown`` on unreachable / unparseable, ``failed`` on structural
    no-link signal, ``ok`` when at least one link is populated.
  * Ensurer — wide-handler delegation. Probe-skip when already linked;
    otherwise dispatch the legacy ``ensure_maintainerr_integrations``
    handler via injected ``configure_handler`` + ``job_context_factory``
    callables.

No real HTTP and no real Maintainerr — urllib + the configure handler
+ JobContext factory are all mocked / injected.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

from media_stack.adapters.maintainerr.lifecycle import MaintainerrLifecycle
from media_stack.adapters.maintainerr.rules_wiring import (
    MaintainerrCollectionsWirer,
)
from media_stack.domain.services import OrchestrationContext


_HOST = "maintainerr"
_PORT = 6246


def _ctx(extra: dict | None = None) -> OrchestrationContext:
    return OrchestrationContext(
        service_id="maintainerr",
        config={
            "host": _HOST,
            "port": _PORT,
            "scheme": "http",
        },
        secrets={},
        now=lambda: 1700000000.0,
        extra=extra or {},
    )


def _http_response(body: bytes, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda *_: None
    return resp


# --- Probe ----------------------------------------------------------


class TestProbeRulesLinkedToArr:

    @patch("urllib.request.urlopen")
    def test_ok_when_movie_has_radarr_link(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps([
            {"type": "movie", "radarrSettingsId": 1},
            {"type": "show", "sonarrSettingsId": None},
        ]).encode()
        mock_open.return_value = _http_response(body)
        sl = MaintainerrLifecycle()
        result = sl.probe_rules_linked_to_arr(_ctx())
        assert result.is_ok
        assert result.evidence.get("linked_count") == 1
        assert result.evidence.get("collection_count") == 2

    @patch("urllib.request.urlopen")
    def test_ok_when_show_has_sonarr_link(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps([
            {"type": "movie", "radarrSettingsId": None},
            {"type": "show", "sonarrSettingsId": 7},
        ]).encode()
        mock_open.return_value = _http_response(body)
        sl = MaintainerrLifecycle()
        result = sl.probe_rules_linked_to_arr(_ctx())
        assert result.is_ok
        assert result.evidence.get("linked_count") == 1

    @patch("urllib.request.urlopen")
    def test_failed_when_all_unlinked(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps([
            {"type": "movie", "radarrSettingsId": None},
            {"type": "show", "sonarrSettingsId": None},
        ]).encode()
        mock_open.return_value = _http_response(body)
        sl = MaintainerrLifecycle()
        result = sl.probe_rules_linked_to_arr(_ctx())
        assert result.status == "failed"
        assert result.evidence.get("linked_count") == 0
        assert result.evidence.get("collection_count") == 2

    @patch("urllib.request.urlopen")
    def test_failed_when_collections_empty(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.return_value = _http_response(b"[]")
        sl = MaintainerrLifecycle()
        result = sl.probe_rules_linked_to_arr(_ctx())
        assert result.status == "failed"
        assert result.evidence.get("collection_count") == 0

    @patch("urllib.request.urlopen")
    def test_unknown_when_unreachable(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.side_effect = urllib.error.URLError("connection refused")
        sl = MaintainerrLifecycle()
        result = sl.probe_rules_linked_to_arr(_ctx())
        assert result.status == "unknown"

    @patch("urllib.request.urlopen")
    def test_unknown_when_unparseable(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.return_value = _http_response(b"not-json{{{")
        sl = MaintainerrLifecycle()
        result = sl.probe_rules_linked_to_arr(_ctx())
        assert result.status == "unknown"

    @patch("urllib.request.urlopen")
    def test_failed_when_non_list_payload(
        self, mock_open: MagicMock,
    ) -> None:
        mock_open.return_value = _http_response(b'{"oops": "object"}')
        sl = MaintainerrLifecycle()
        result = sl.probe_rules_linked_to_arr(_ctx())
        assert result.status == "failed"

    def test_unknown_when_host_or_port_missing(self) -> None:
        sl = MaintainerrLifecycle()
        ctx = OrchestrationContext(
            service_id="maintainerr",
            config={"host": "", "port": None, "scheme": "http"},
            secrets={},
            now=lambda: 1700000000.0,
            extra={},
        )
        result = sl.probe_rules_linked_to_arr(ctx)
        assert result.status == "unknown"


# --- Ensurer (wide-handler delegation) ------------------------------


class _StubJobCtx:
    """Minimal JobContext stub — the wirer reads .cfg / .config_root /
    .arr_apps / .wait_timeout via getattr."""

    def __init__(
        self,
        cfg: dict | None = None,
        config_root: str | None = "/srv-stack/controller-config",
        arr_apps: list | None = None,
        wait_timeout: int | None = 60,
    ) -> None:
        self.cfg = cfg if cfg is not None else {"maintainerr": {}}
        self.config_root = config_root
        self.arr_apps = arr_apps if arr_apps is not None else []
        self.wait_timeout = wait_timeout


class TestEnsureRulesLinkedToArr:

    @patch("urllib.request.urlopen")
    def test_short_circuits_when_already_linked(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps([
            {"type": "movie", "radarrSettingsId": 1},
        ]).encode()
        mock_open.return_value = _http_response(body)
        # Wirer takes injected callables; bypass the lifecycle's lazy
        # imports by exercising the wirer directly here (the lifecycle
        # method's lazy-import path is exercised by the per-test in
        # ``TestLifecycleMethodWiring`` below).
        wirer = MaintainerrCollectionsWirer()
        configure_handler = MagicMock()
        job_context_factory = MagicMock(return_value=_StubJobCtx())
        outcome = wirer.ensure(
            _ctx(),
            configure_handler=configure_handler,
            job_context_factory=job_context_factory,
        )
        assert outcome.ok
        configure_handler.assert_not_called()
        job_context_factory.assert_not_called()
        assert outcome.evidence.get("reason") == "already_linked"

    @patch("urllib.request.urlopen")
    def test_delegates_to_handler_when_unlinked(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps([
            {"type": "movie", "radarrSettingsId": None},
        ]).encode()
        mock_open.return_value = _http_response(body)
        wirer = MaintainerrCollectionsWirer()
        configure_handler = MagicMock(return_value=None)
        stub = _StubJobCtx(
            cfg={"maintainerr": {"integrations": {"enabled": True}}},
            config_root="/srv-stack/controller-config",
            arr_apps=[{"id": "radarr"}, {"id": "sonarr"}],
            wait_timeout=120,
        )
        job_context_factory = MagicMock(return_value=stub)
        outcome = wirer.ensure(
            _ctx(),
            configure_handler=configure_handler,
            job_context_factory=job_context_factory,
        )
        assert outcome.ok
        configure_handler.assert_called_once_with(
            stub.cfg, stub.config_root, stub.arr_apps, stub.wait_timeout,
        )
        assert outcome.evidence.get("delegated_to") == (
            "ensure_maintainerr_integrations"
        )

    @patch("urllib.request.urlopen")
    def test_transient_failure_when_handler_raises(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps([
            {"type": "show", "sonarrSettingsId": None},
        ]).encode()
        mock_open.return_value = _http_response(body)
        wirer = MaintainerrCollectionsWirer()
        configure_handler = MagicMock(
            side_effect=RuntimeError("maintainerr unreachable"),
        )
        job_context_factory = MagicMock(return_value=_StubJobCtx())
        outcome = wirer.ensure(
            _ctx(),
            configure_handler=configure_handler,
            job_context_factory=job_context_factory,
        )
        assert not outcome.ok
        assert outcome.transient
        assert "maintainerr unreachable" in outcome.error

    @patch("urllib.request.urlopen")
    def test_transient_failure_when_factory_raises(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps([
            {"type": "movie", "radarrSettingsId": None},
        ]).encode()
        mock_open.return_value = _http_response(body)
        wirer = MaintainerrCollectionsWirer()
        configure_handler = MagicMock()
        job_context_factory = MagicMock(
            side_effect=RuntimeError("no JobContext available"),
        )
        outcome = wirer.ensure(
            _ctx(),
            configure_handler=configure_handler,
            job_context_factory=job_context_factory,
        )
        assert not outcome.ok
        assert outcome.transient
        configure_handler.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_failure_when_job_ctx_missing_cfg(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps([
            {"type": "movie", "radarrSettingsId": None},
        ]).encode()
        mock_open.return_value = _http_response(body)
        wirer = MaintainerrCollectionsWirer()
        configure_handler = MagicMock()
        # JobContext-shape stub with cfg explicitly None reproduces the
        # "JobContext exists but the cfg attribute hasn't been
        # resolved" path; the wirer should refuse to dispatch.
        empty_ctx = MagicMock(spec=[
            "cfg", "config_root", "arr_apps", "wait_timeout",
        ])
        empty_ctx.cfg = None
        empty_ctx.config_root = "/srv-stack/controller-config"
        empty_ctx.arr_apps = []
        empty_ctx.wait_timeout = 60
        job_context_factory = MagicMock(return_value=empty_ctx)
        outcome = wirer.ensure(
            _ctx(),
            configure_handler=configure_handler,
            job_context_factory=job_context_factory,
        )
        assert not outcome.ok
        configure_handler.assert_not_called()


# --- Lifecycle method wiring ----------------------------------------


class TestLifecycleMethodWiring:
    """The lifecycle method delegates to the module-level singleton
    ``_RULES_WIRER`` via lazy-imported callables. Pin the wiring by
    patching the singleton's ``ensure`` and confirming it's called
    with the expected ``configure_handler`` (the legacy
    ``ensure_maintainerr_integrations``)."""

    def test_ensure_method_routes_through_singleton(self) -> None:
        from media_stack.adapters.maintainerr import lifecycle as lc_mod
        captured: dict = {}

        def fake_ensure(ctx, *, configure_handler, job_context_factory):
            captured["configure_handler"] = configure_handler
            captured["job_context_factory"] = job_context_factory
            from media_stack.domain.services import Outcome
            return Outcome.success(None, evidence={"stub": True})

        with patch.object(lc_mod._RULES_WIRER, "ensure", side_effect=fake_ensure):
            sl = MaintainerrLifecycle()
            outcome = sl.ensure_rules_linked_to_arr(_ctx())
        assert outcome.ok
        # The legacy handler the lifecycle ensurer wide-handler-
        # delegates to MUST be ``ensure_maintainerr_integrations`` —
        # not the misnomer Jellyfin auto-collections handler.
        from media_stack.services.apps.maintainerr.runtime_ops import (
            ensure_maintainerr_integrations,
        )
        assert captured["configure_handler"] is ensure_maintainerr_integrations
        # And the factory MUST be the framework's JobContext class —
        # so the four-arg adapter-handler call shape is built from
        # whatever the runner provides.
        from media_stack.services.jobs.framework import JobContext
        assert captured["job_context_factory"] is JobContext

    @patch("urllib.request.urlopen")
    def test_probe_method_routes_through_singleton(
        self, mock_open: MagicMock,
    ) -> None:
        body = json.dumps([
            {"type": "movie", "radarrSettingsId": 5},
        ]).encode()
        mock_open.return_value = _http_response(body)
        sl = MaintainerrLifecycle()
        result = sl.probe_rules_linked_to_arr(_ctx())
        assert result.is_ok
