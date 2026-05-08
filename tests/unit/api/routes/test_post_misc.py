"""Tests for ``api/routes/post_misc.py`` (ADR-0007 Phase 2 wave 8 group 3).

Covers the six heterogeneous POST routes lifted off the legacy
``handlers_post`` elif chain:

* ``POST /actions/{name}`` — generic action dispatch (root path).
* ``POST /cancel``         — generic cancel (root path).
* ``POST /config``         — config write (root path).
* ``POST /run``            — bootstrap legacy alias (root path).
* ``POST /api/jellyfin/reset`` — Jellyfin admin credential reset.
* ``POST /api/validate-migration`` — migration target preflight.

The legacy chain's behaviour is the contract — these tests pin
the wire shape (validation, error envelopes, service-call wiring)
at the route boundary so a later refactor doesn't accidentally
regress operator-script callers of the URL-root aliases.

Test layout mirrors ``test_post_jobs_queue`` (same wave's template):

* Per-route ``Test<Domain>Route`` classes.
* ``_RouteHarness`` rebinds the auto-discovered ``MiscPostRoutes``
  with a test-wired instance so stubs flow through the production
  Router.
* End-of-file ``TestCsrfGate`` pins CSRF short-circuits + 403
  emission, plus ``TestRoutingIntegration`` pins auto-discovery
  + spec-parity.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routes.post_misc import (
    ActionCanceller,
    ActionTrigger,
    ConfigWriter,
    JellyfinResetService,
    KnownActionsProvider,
    MigrationValidator,
    MiscPostRoutes,
)
from media_stack.api.routing import (
    DefaultDispatcher,
    DispatchOutcome,
    Router,
    RouterDispatcher,
)
from tests.unit.api.routes._helpers import (
    CapturedResponse,
    MockControllerHandler,
    RouteDispatchHarness,
)


# ---------------------------------------------------------------------------
# POST-aware mock handler — lifted from test_post_jobs_queue.
# ---------------------------------------------------------------------------


class PostMockHandler(MockControllerHandler):
    """``MockControllerHandler`` extended with ``_read_json_body``
    that mirrors the production server's behaviour and an optional
    ``_handle_action`` capture so the action-dispatch routes can
    assert their forwarding."""

    def __init__(
        self,
        *,
        path: str = "/",
        body: bytes | dict[str, Any] = b"",
        headers: dict[str, str] | None = None,
        state: Any = None,
    ) -> None:
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        merged_headers = dict(headers or {})
        if body and "Content-Length" not in merged_headers:
            merged_headers["Content-Length"] = str(len(body))
        super().__init__(
            path=path, body=body, headers=merged_headers, state=state,
        )
        self.action_calls: list[str] = []

    def _read_json_body(self) -> dict[str, Any]:
        length_str = self.headers.get("Content-Length", "0")
        try:
            length = int(length_str)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            return {}

    def _handle_action(self, action_name: str) -> None:
        """Capture the dispatched action name + emit a 200 like
        the production handler does."""
        self.action_calls.append(action_name)
        self._json_response(200, {"status": "accepted", "action": action_name})


class _AlwaysAllowGate:
    """Permissive ``PostMutationGate`` stand-in for tests focused
    on business logic."""

    def verify(self, handler: Any) -> bool:
        return True

    def reject(self, handler: Any) -> None:  # pragma: no cover
        raise AssertionError(
            "_AlwaysAllowGate.reject called — verify should have "
            "returned True",
        )


# ---------------------------------------------------------------------------
# Fake ControllerState surface for the cancel + config routes.
# ---------------------------------------------------------------------------


class _FakeAction:
    """Stand-in for ``ControllerState.current_action`` — only
    needs ``to_dict()``."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class _FakeCancelState:
    def __init__(
        self,
        *,
        cancelled: bool,
        current_action: _FakeAction | None,
    ) -> None:
        self._cancelled = cancelled
        self.current_action = current_action

    def cancel_action(self) -> bool:
        return self._cancelled


class _FakeConfigState:
    def __init__(self) -> None:
        self.last_body: dict[str, Any] | None = None

    def update_config(self, body: dict[str, Any]) -> dict[str, Any]:
        self.last_body = dict(body)
        return {"merged": True, **body}


# ---------------------------------------------------------------------------
# Harness — rebinds the auto-discovered MiscPostRoutes with a
# test-wired instance so stubs flow through the production Router.
# Pattern mirrors test_post_jobs_queue::_RouteHarness.
# ---------------------------------------------------------------------------


class _RouteHarness:
    @classmethod
    def with_routes(cls, routes: MiscPostRoutes) -> RouteDispatchHarness:
        DefaultDispatcher.reset_for_tests()
        router = Router()
        cls._rebind(router, routes)
        return RouteDispatchHarness(RouterDispatcher(router))

    @classmethod
    def _rebind(cls, router: Router, routes: MiscPostRoutes) -> None:
        for key, route in list(router._exact.items()):
            replacement = cls._maybe_replacement(route, routes)
            if replacement is not None:
                router._exact[key] = type(route)(
                    verb=route.verb, path=route.path,
                    handler=replacement, pattern=route.pattern,
                    param_names=route.param_names, display=route.display,
                )
        for idx, route in enumerate(list(router._parameterized)):
            replacement = cls._maybe_replacement(route, routes)
            if replacement is not None:
                router._parameterized[idx] = type(route)(
                    verb=route.verb, path=route.path,
                    handler=replacement, pattern=route.pattern,
                    param_names=route.param_names, display=route.display,
                )

    @staticmethod
    def _maybe_replacement(route: Any, routes: MiscPostRoutes) -> Any:
        if "MiscPostRoutes" not in route.display:
            return None
        method_name = route.display.rsplit(".", 1)[-1]
        return getattr(routes, method_name)


def _dispatch_post(
    harness: RouteDispatchHarness,
    path: str,
    *,
    body: bytes | dict[str, Any] = b"",
    headers: dict[str, str] | None = None,
    state: Any = None,
) -> tuple[CapturedResponse, PostMockHandler]:
    handler = PostMockHandler(
        path=path, body=body, headers=headers, state=state,
    )
    outcome = harness._dispatcher.try_dispatch("POST", path, handler)
    if outcome == DispatchOutcome.METHOD_NOT_ALLOWED:
        harness._dispatcher.write_method_not_allowed(handler, path)
    return handler.captured, handler


def _routes_with(**kwargs: Any) -> MiscPostRoutes:
    kwargs.setdefault("mutation_gate", _AlwaysAllowGate())
    return MiscPostRoutes(**kwargs)


# ---------------------------------------------------------------------------
# /actions/{name} — generic action dispatch
# ---------------------------------------------------------------------------


class TestActionsRoute:
    def test_known_action_forwards_to_handler(self) -> None:
        captured: list[tuple[Any, str]] = []

        def fake_trigger(handler: Any, action_name: str) -> None:
            captured.append((handler, action_name))
            handler._json_response(200, {"status": "accepted"})

        routes = _routes_with(
            known_actions=KnownActionsProvider(
                known_actions=frozenset({"bootstrap", "reconcile"}),
            ),
            action_trigger=ActionTrigger(trigger_fn=fake_trigger),
        )
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(harness, "/actions/bootstrap")

        assert response.status == 200
        assert json.loads(response.body) == {"status": "accepted"}
        assert len(captured) == 1
        assert captured[0][1] == "bootstrap"

    def test_api_alias_forwards_to_same_handler(self) -> None:
        # ``/api/actions/{name}`` is the dashboard alias of
        # ``/actions/{name}``. SPA's nginx only proxies ``/api/*``
        # to the controller; without this alias the UI's "Run now"
        # button hits the SPA-fallback ``try_files`` block and 404s.
        # Pin: alias dispatches the same trigger.
        captured: list[tuple[Any, str]] = []

        def fake_trigger(handler: Any, action_name: str) -> None:
            captured.append((handler, action_name))
            handler._json_response(200, {"status": "accepted"})

        routes = _routes_with(
            known_actions=KnownActionsProvider(
                known_actions=frozenset({"run-media-hygiene"}),
            ),
            action_trigger=ActionTrigger(trigger_fn=fake_trigger),
        )
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(
            harness, "/api/actions/run-media-hygiene",
        )

        assert response.status == 200
        assert json.loads(response.body) == {"status": "accepted"}
        assert len(captured) == 1
        assert captured[0][1] == "run-media-hygiene"

    def test_api_alias_unknown_action_returns_404(self) -> None:
        # Same 404 envelope as the root-path route — symmetry
        # check so the SPA's error-handling path doesn't have to
        # branch on which prefix the request used.
        routes = _routes_with(
            known_actions=KnownActionsProvider(
                known_actions=frozenset({"bootstrap"}),
            ),
            action_trigger=ActionTrigger(
                trigger_fn=lambda h, n: None,
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(
            harness, "/api/actions/not-a-real-action",
        )

        assert response.status == 404
        body = json.loads(response.body)
        assert body["error"] == "unknown action 'not-a-real-action'"
        assert "bootstrap" in body["known"]

    def test_unknown_action_returns_404(self) -> None:
        triggered: list[Any] = []
        routes = _routes_with(
            known_actions=KnownActionsProvider(
                known_actions=frozenset({"bootstrap", "reconcile"}),
            ),
            action_trigger=ActionTrigger(
                trigger_fn=lambda h, n: triggered.append(n),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(harness, "/actions/not-a-real-action")

        assert response.status == 404
        body = json.loads(response.body)
        assert body["error"] == "unknown action 'not-a-real-action'"
        assert "bootstrap" in body["known"]
        assert "reconcile" in body["known"]
        # Sorted preserves the legacy chain's wire shape.
        assert body["known"] == sorted(body["known"])
        # Trigger must NOT fire on an unknown action.
        assert triggered == []

    def test_path_param_with_special_chars_returns_404(self) -> None:
        """Path-param injection guard: an action name that's not in
        the accept-list (even if it looks like a path traversal
        string) falls through to the unknown-action 404 envelope.
        The Router's regex compiler matches a single segment so
        ``/actions/../config`` doesn't even reach this handler;
        the test pins the in-segment shape.
        """
        triggered: list[Any] = []
        routes = _routes_with(
            known_actions=KnownActionsProvider(
                known_actions=frozenset({"bootstrap"}),
            ),
            action_trigger=ActionTrigger(
                trigger_fn=lambda h, n: triggered.append(n),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        # Single-segment "weird" name — should land on this handler
        # and 404 because it's not in the accept-list.
        response, _ = _dispatch_post(harness, "/actions/..%2Fconfig")
        assert response.status == 404
        body = json.loads(response.body)
        assert body["error"].startswith("unknown action ")
        assert triggered == []

    def test_multi_segment_path_does_not_match(self) -> None:
        """``/actions/foo/bar`` shouldn't even reach the handler —
        the parameterized pattern only matches a single segment.
        Pin the Router's verb-discrimination so a future regex
        change doesn't accidentally allow path traversal."""
        triggered: list[Any] = []
        routes = _routes_with(
            known_actions=KnownActionsProvider(
                known_actions=frozenset({"bootstrap"}),
            ),
            action_trigger=ActionTrigger(
                trigger_fn=lambda h, n: triggered.append(n),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        outcome, _ = harness.try_dispatch(
            "POST", "/actions/foo/bar",
        )
        assert outcome == DispatchOutcome.NO_MATCH
        assert triggered == []


# ---------------------------------------------------------------------------
# /cancel — generic cancel
# ---------------------------------------------------------------------------


class TestCancelRoute:
    def test_running_action_returns_cancel_requested(self) -> None:
        state = _FakeCancelState(
            cancelled=True,
            current_action=_FakeAction({
                "id": "bootstrap-3", "name": "bootstrap", "status": "running",
            }),
        )
        routes = _routes_with(action_canceller=ActionCanceller())
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(harness, "/cancel", state=state)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["status"] == "cancel_requested"
        assert body["current_action"]["name"] == "bootstrap"

    def test_no_running_action_returns_no_action_running(self) -> None:
        state = _FakeCancelState(cancelled=False, current_action=None)
        routes = _routes_with(action_canceller=ActionCanceller())
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(harness, "/cancel", state=state)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["status"] == "no_action_running"
        assert body["current_action"] is None

    def test_cancel_called_with_state(self) -> None:
        captured_states: list[Any] = []

        def fake_cancel(state: Any) -> bool:
            captured_states.append(state)
            return True

        state = _FakeCancelState(cancelled=True, current_action=None)
        routes = _routes_with(
            action_canceller=ActionCanceller(cancel_fn=fake_cancel),
        )
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(harness, "/cancel", state=state)

        assert captured_states == [state]


# ---------------------------------------------------------------------------
# /config — config write
# ---------------------------------------------------------------------------


class TestConfigRoute:
    def test_writes_body_to_state(self) -> None:
        state = _FakeConfigState()
        routes = _routes_with(config_writer=ConfigWriter())
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(
            harness, "/config",
            body={"_log_level": "DEBUG"}, state=state,
        )

        assert response.status == 200
        body = json.loads(response.body)
        assert body["status"] == "updated"
        assert body["config"]["_log_level"] == "DEBUG"
        assert state.last_body == {"_log_level": "DEBUG"}

    def test_empty_body_returns_400(self) -> None:
        state = _FakeConfigState()
        routes = _routes_with(config_writer=ConfigWriter())
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(harness, "/config", state=state)

        assert response.status == 400
        assert json.loads(response.body) == {"error": "JSON body required"}
        # Empty body must not call update_config at all.
        assert state.last_body is None

    def test_log_callable_receives_body(self) -> None:
        state = _FakeConfigState()
        log_calls: list[tuple[str, Any]] = []
        routes = _routes_with(
            config_writer=ConfigWriter(
                log_fn=lambda fmt, body: log_calls.append((fmt, body)),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(
            harness, "/config",
            body={"key": "value"}, state=state,
        )

        assert len(log_calls) == 1
        assert log_calls[0][1] == {"key": "value"}


# ---------------------------------------------------------------------------
# /run — bootstrap legacy alias
# ---------------------------------------------------------------------------


class TestRunRoute:
    def test_dispatches_bootstrap(self) -> None:
        captured: list[str] = []

        def fake_trigger(handler: Any, action_name: str) -> None:
            captured.append(action_name)
            handler._json_response(200, {"status": "accepted"})

        routes = _routes_with(
            action_trigger=ActionTrigger(trigger_fn=fake_trigger),
        )
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(harness, "/run")

        assert response.status == 200
        assert captured == ["bootstrap"]


# ---------------------------------------------------------------------------
# /api/jellyfin/reset — Jellyfin admin reset
# ---------------------------------------------------------------------------


class TestJellyfinResetRoute:
    def test_explicit_credentials_forwarded(self) -> None:
        captured: list[tuple[str, str]] = []

        def fake_reset(username: str, password: str) -> dict[str, Any]:
            captured.append((username, password))
            return {"status": "reset", "user": username}

        routes = _routes_with(
            jellyfin_reset=JellyfinResetService(reset_fn=fake_reset),
        )
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(
            harness, "/api/jellyfin/reset",
            body={"username": "operator", "password": "verysecure"},
        )

        assert response.status == 200
        assert json.loads(response.body) == {
            "status": "reset", "user": "operator",
        }
        assert captured == [("operator", "verysecure")]

    def test_falls_back_to_env_when_body_omits_fields(self) -> None:
        captured: list[tuple[str, str]] = []

        def fake_reset(username: str, password: str) -> dict[str, Any]:
            captured.append((username, password))
            return {"status": "reset"}

        env_calls: list[tuple[str, str]] = []

        def fake_env(key: str, default: str = "") -> str:
            env_calls.append((key, default))
            return {
                "STACK_ADMIN_USERNAME": "admin-from-env",
                "STACK_ADMIN_PASSWORD": "passw0rd",
            }.get(key, default)

        routes = _routes_with(
            jellyfin_reset=JellyfinResetService(
                reset_fn=fake_reset, env_provider=fake_env,
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(
            harness, "/api/jellyfin/reset", body={},
        )

        assert response.status == 200
        assert captured == [("admin-from-env", "passw0rd")]

    def test_short_password_returns_400(self) -> None:
        called: list[Any] = []
        routes = _routes_with(
            jellyfin_reset=JellyfinResetService(
                reset_fn=lambda u, p: called.append((u, p)) or {},
                env_provider=lambda key, default="": "",
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(
            harness, "/api/jellyfin/reset",
            body={"username": "admin", "password": "abc"},
        )

        assert response.status == 400
        body = json.loads(response.body)
        assert "min 4" in body["error"]
        assert called == []

    def test_default_literals_when_env_empty(self) -> None:
        captured: list[tuple[str, str]] = []
        routes = _routes_with(
            jellyfin_reset=JellyfinResetService(
                reset_fn=lambda u, p: (
                    captured.append((u, p)) or {"status": "reset"}
                ),
                env_provider=lambda key, default="": default,
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(harness, "/api/jellyfin/reset", body={})

        # Defaults come from the literal fallbacks in the module.
        assert captured == [("admin", "media-stack")]


# ---------------------------------------------------------------------------
# /api/validate-migration — disk migration target preflight
# ---------------------------------------------------------------------------


class TestValidateMigrationRoute:
    def test_forwards_target_path(self) -> None:
        captured: list[str] = []

        def fake_validate(target_path: str) -> dict[str, Any]:
            captured.append(target_path)
            return {"valid": True, "target": target_path}

        routes = _routes_with(
            migration_validator=MigrationValidator(validate_fn=fake_validate),
        )
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(
            harness, "/api/validate-migration",
            body={"target_path": "/mnt/new-storage"},
        )

        assert response.status == 200
        assert json.loads(response.body) == {
            "valid": True, "target": "/mnt/new-storage",
        }
        assert captured == ["/mnt/new-storage"]

    def test_missing_target_path_forwards_empty_string(self) -> None:
        captured: list[str] = []
        routes = _routes_with(
            migration_validator=MigrationValidator(
                validate_fn=lambda tp: (
                    captured.append(tp) or {"valid": False, "error": "..."}
                ),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(
            harness, "/api/validate-migration", body={},
        )

        assert response.status == 200
        assert captured == [""]

    def test_empty_body_forwards_empty_string(self) -> None:
        """An entirely missing body still calls the validator with
        empty string — the service layer's validator emits the
        canonical error envelope."""
        captured: list[str] = []
        routes = _routes_with(
            migration_validator=MigrationValidator(
                validate_fn=lambda tp: (
                    captured.append(tp) or {"valid": False, "error": "..."}
                ),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        _dispatch_post(harness, "/api/validate-migration", body=b"")

        assert captured == [""]


# ---------------------------------------------------------------------------
# Strategy unit coverage — defaults + fresh-lookup
# ---------------------------------------------------------------------------


class TestKnownActionsProvider:
    def test_explicit_set_overrides_default(self) -> None:
        provider = KnownActionsProvider(
            known_actions=frozenset({"a", "b"}),
        )
        assert provider.contains("a")
        assert not provider.contains("c")
        assert provider.all() == frozenset({"a", "b"})

    def test_default_resolves_to_handlers_post(self) -> None:
        provider = KnownActionsProvider()
        # The legacy chain's KNOWN_ACTIONS includes the core
        # ``bootstrap`` action — pin that the default lookup
        # actually reaches it.
        assert provider.contains("bootstrap")


class TestActionTrigger:
    def test_explicit_trigger_called(self) -> None:
        seen: list[tuple[Any, str]] = []
        ActionTrigger(
            trigger_fn=lambda h, n: seen.append((h, n)),
        ).trigger("HANDLER", "bootstrap")
        assert seen == [("HANDLER", "bootstrap")]

    def test_default_calls_handler_method(self) -> None:
        handler = MagicMock()
        ActionTrigger().trigger(handler, "reconcile")
        handler._handle_action.assert_called_once_with("reconcile")


class TestActionCanceller:
    def test_returns_cancelled_and_current(self) -> None:
        action = _FakeAction({"id": "x"})
        state = _FakeCancelState(cancelled=True, current_action=action)
        cancelled, current = ActionCanceller().cancel(state)
        assert cancelled is True
        assert current is action

    def test_returns_none_current_when_state_lacks_attr(self) -> None:
        class _NoAttr:
            def cancel_action(self) -> bool:
                return False
        cancelled, current = ActionCanceller().cancel(_NoAttr())
        assert cancelled is False
        assert current is None


class TestConfigWriter:
    def test_empty_body_returns_400(self) -> None:
        status, response = ConfigWriter().write(_FakeConfigState(), None)
        assert status == 400
        assert response == {"error": "JSON body required"}

    def test_empty_dict_returns_400(self) -> None:
        status, response = ConfigWriter().write(_FakeConfigState(), {})
        assert status == 400

    def test_default_calls_state_update_config(self) -> None:
        state = _FakeConfigState()
        status, response = ConfigWriter().write(state, {"a": 1})
        assert status == 200
        assert response["status"] == "updated"
        assert state.last_body == {"a": 1}


class TestJellyfinResetService:
    def test_explicit_reset_fn_called(self) -> None:
        captured: list[tuple[str, str]] = []
        svc = JellyfinResetService(
            reset_fn=lambda u, p: (
                captured.append((u, p)) or {"status": "reset"}
            ),
        )
        status, response = svc.reset(
            {"username": "alice", "password": "averylongpw"},
        )
        assert status == 200
        assert response == {"status": "reset"}
        assert captured == [("alice", "averylongpw")]

    def test_short_password_blocked(self) -> None:
        called: list[Any] = []
        svc = JellyfinResetService(
            reset_fn=lambda u, p: called.append((u, p)) or {},
            env_provider=lambda key, default="": "",
        )
        status, response = svc.reset({"password": "x"})
        assert status == 400
        assert called == []

    def test_default_path_imports_admin_module(
        self, monkeypatch,
    ) -> None:
        from media_stack.api.services import admin as admin_svc
        seen: list[tuple[str, str]] = []

        def stub(username: str, password: str) -> dict[str, Any]:
            seen.append((username, password))
            return {"status": "reset", "via": "stub"}

        monkeypatch.setattr(admin_svc, "jellyfin_hard_reset", stub)
        svc = JellyfinResetService(
            env_provider=lambda key, default="": "",
        )
        status, response = svc.reset(
            {"username": "alice", "password": "verysecure"},
        )
        assert status == 200
        assert response == {"status": "reset", "via": "stub"}
        assert seen == [("alice", "verysecure")]


class TestMigrationValidator:
    def test_explicit_fn_called(self) -> None:
        seen: list[str] = []
        result = MigrationValidator(
            validate_fn=lambda tp: (
                seen.append(tp) or {"valid": True}
            ),
        ).validate("/mnt/x")
        assert seen == ["/mnt/x"]
        assert result == {"valid": True}

    def test_default_path_imports_disk_module(
        self, monkeypatch,
    ) -> None:
        from media_stack.api.services import disk as disk_svc
        seen: list[str] = []

        def stub(target_path: str) -> dict[str, Any]:
            seen.append(target_path)
            return {"valid": False, "error": "stub"}

        monkeypatch.setattr(disk_svc, "validate_migration_target", stub)
        result = MigrationValidator().validate("/some/path")
        assert seen == ["/some/path"]
        assert result == {"valid": False, "error": "stub"}


# ---------------------------------------------------------------------------
# CSRF gate — security regression coverage
# ---------------------------------------------------------------------------


class TestCsrfGate:
    """Pin the CSRF double-submit semantics for the migrated misc
    POST routes. Regressions here would re-open the CSRF surface
    for legacy URL-root paths that operator scripts still use.
    """

    def _gate_blocking(self) -> PostMutationGate:
        csrf_stub = MagicMock()
        csrf_stub.header_name = "X-CSRF-Token"
        csrf_stub.verify.return_value = False
        return PostMutationGate(csrf=csrf_stub)

    def test_actions_blocked_when_gate_rejects(self) -> None:
        triggered: list[Any] = []
        routes = MiscPostRoutes(
            mutation_gate=self._gate_blocking(),
            known_actions=KnownActionsProvider(
                known_actions=frozenset({"bootstrap"}),
            ),
            action_trigger=ActionTrigger(
                trigger_fn=lambda h, n: triggered.append(n),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(
            harness, "/actions/bootstrap",
            headers={"Cookie": "media_stack_csrf=zzz"},
        )

        assert response.status == 403
        assert triggered == []

    def test_cancel_blocked_when_gate_rejects(self) -> None:
        cancel_calls: list[Any] = []
        routes = MiscPostRoutes(
            mutation_gate=self._gate_blocking(),
            action_canceller=ActionCanceller(
                cancel_fn=lambda s: cancel_calls.append(s) or True,
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(
            harness, "/cancel",
            state=_FakeCancelState(
                cancelled=True, current_action=None,
            ),
            headers={"Cookie": "media_stack_csrf=zzz"},
        )
        assert response.status == 403
        assert cancel_calls == []

    def test_config_blocked_when_gate_rejects(self) -> None:
        state = _FakeConfigState()
        routes = MiscPostRoutes(
            mutation_gate=self._gate_blocking(),
            config_writer=ConfigWriter(),
        )
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(
            harness, "/config",
            body={"a": 1}, state=state,
            headers={"Cookie": "media_stack_csrf=zzz"},
        )
        assert response.status == 403
        assert state.last_body is None

    def test_run_blocked_when_gate_rejects(self) -> None:
        triggered: list[Any] = []
        routes = MiscPostRoutes(
            mutation_gate=self._gate_blocking(),
            action_trigger=ActionTrigger(
                trigger_fn=lambda h, n: triggered.append(n),
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(
            harness, "/run",
            headers={"Cookie": "media_stack_csrf=zzz"},
        )
        assert response.status == 403
        assert triggered == []

    def test_jellyfin_reset_blocked_when_gate_rejects(self) -> None:
        called: list[Any] = []
        routes = MiscPostRoutes(
            mutation_gate=self._gate_blocking(),
            jellyfin_reset=JellyfinResetService(
                reset_fn=lambda u, p: called.append((u, p)) or {},
                env_provider=lambda key, default="": "verysecure",
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(
            harness, "/api/jellyfin/reset",
            body={"username": "x", "password": "verysecure"},
            headers={"Cookie": "media_stack_csrf=zzz"},
        )
        assert response.status == 403
        assert called == []

    def test_validate_migration_blocked_when_gate_rejects(self) -> None:
        called: list[Any] = []
        routes = MiscPostRoutes(
            mutation_gate=self._gate_blocking(),
            migration_validator=MigrationValidator(
                validate_fn=lambda tp: called.append(tp) or {},
            ),
        )
        harness = _RouteHarness.with_routes(routes)
        response, _ = _dispatch_post(
            harness, "/api/validate-migration",
            body={"target_path": "/mnt/x"},
            headers={"Cookie": "media_stack_csrf=zzz"},
        )
        assert response.status == 403
        assert called == []


# ---------------------------------------------------------------------------
# Routing integration — auto-discovery + spec-parity
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    """Pin auto-discovery + spec-parity for the misc POST domain.
    If a future change accidentally drops a handler, this fires
    before any per-route test does.
    """

    _EXPECTED = frozenset({
        "/actions/{name}",
        "/api/actions/{name}",
        "/cancel",
        "/config",
        "/run",
        "/api/jellyfin/reset",
        "/api/validate-migration",
    })

    def test_all_misc_routes_registered(self) -> None:
        harness = RouteDispatchHarness.with_default_router()
        registered = {
            r.path
            for r in harness._dispatcher._router.registered_routes()
            if r.verb == "POST"
            and "MiscPostRoutes" in r.display
        }
        assert registered == self._EXPECTED, (
            f"Mismatch — missing: {self._EXPECTED - registered}, "
            f"unexpected: {registered - self._EXPECTED}"
        )

    def test_default_constructor_wires_real_collaborators(self) -> None:
        instance = MiscPostRoutes()
        assert isinstance(instance._gate, PostMutationGate)
        assert isinstance(instance._known_actions, KnownActionsProvider)
        assert isinstance(instance._action_trigger, ActionTrigger)
        assert isinstance(instance._action_canceller, ActionCanceller)
        assert isinstance(instance._config_writer, ConfigWriter)
        assert isinstance(instance._jellyfin_reset, JellyfinResetService)
        assert isinstance(instance._migration_validator, MigrationValidator)

    def test_get_to_post_only_path_returns_method_not_allowed(self) -> None:
        """``/cancel`` is POST-only in the spec; a GET should land
        on METHOD_NOT_ALLOWED, not on the legacy chain."""
        harness = RouteDispatchHarness.with_default_router()
        outcome, _ = harness.try_dispatch("GET", "/cancel")
        assert outcome == DispatchOutcome.METHOD_NOT_ALLOWED
