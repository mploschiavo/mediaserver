"""State-domain GET routes (ADR-0007 Phase 2).

Covers the ``/status``, ``/apps``, ``/apps/{appName}``, ``/config``,
and ``/webhooks`` GET endpoints. These all read from
``handler.state`` (the in-memory ``ControllerState``) — the
"domain" name comes from the State tag in ``contracts/api/openapi.yaml``.

Each method body is lifted verbatim from the legacy
``handlers_get.GetRequestHandler.handle()`` chain. Phase 2 only
moves WHERE the dispatch decision is made (Router instead of an
``elif`` chain); the response shape is identical so downstream
consumers see no change.

Note on parameterized routes: ``/apps/{appName}`` declares
``appName`` as a path parameter in the OpenAPI spec, so the
Router passes it to the handler as the ``appName`` kwarg. The
method signature below uses that exact name to match the spec.

Note on ``/api/webhooks``: the legacy chain matched both
``/webhooks`` and ``/api/webhooks`` in a tuple. Only ``/webhooks``
is declared in ``contracts/api/openapi.yaml``; registering
``/api/webhooks`` on the Router would fail the startup spec-drift
check. The legacy chain remains the fallback path for
``/api/webhooks`` until the spec is updated.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get


class StateGetRoutes(RouteModule):
    """Controller-state GET routes. The Router auto-discovers and
    instantiates this class at startup, then walks tagged methods
    for registration."""

    @get("/status")
    def handle_status(self, handler: Any) -> None:
        """Full controller state — phase, action history, runtime
        config, app statuses, webhook URLs.

        Primary endpoint for the dashboard's lifecycle view.
        """
        handler._json_response(HTTPStatus.OK, handler.state.to_dict())

    @get("/apps")
    def handle_apps(self, handler: Any) -> None:
        """Bootstrap status for every managed application.

        Returns ``{"apps": {<name>: <status>, ...}}`` — an explicit
        snapshot of the ``ControllerState.app_status`` dict.
        """
        handler._json_response(
            HTTPStatus.OK, {"apps": dict(handler.state.app_status)},
        )

    @get("/apps/{appName}")
    def handle_app_by_name(self, handler: Any, appName: str) -> None:
        """Single-app bootstrap status.

        Returns the app's status entry on hit, ``404`` with an
        ``error`` field if the app isn't tracked. ``appName`` is
        bound by the Router from the path segment per the OpenAPI
        ``parameters: [{name: appName, in: path}]`` declaration.
        """
        info = handler.state.app_status.get(appName)
        if info is not None:
            handler._json_response(HTTPStatus.OK, {appName: info})
        else:
            handler._json_response(
                HTTPStatus.NOT_FOUND,
                {"error": f"app '{appName}' not found"},
            )

    @get("/config")
    def handle_config(self, handler: Any) -> None:
        """Runtime config overrides (e.g. ``skip_envoy``,
        ``dry_run``).

        Returns ``{"config": {<key>: <value>, ...}}`` — a snapshot
        of ``ControllerState.runtime_config``.
        """
        handler._json_response(
            HTTPStatus.OK, {"config": dict(handler.state.runtime_config)},
        )

    @get("/webhooks")
    def handle_webhooks(self, handler: Any) -> None:
        """Registered webhook URLs.

        Returns ``{"webhook_urls": [...]}`` — the URLs that receive
        action_complete / action_error notifications. The legacy
        chain also matched ``/api/webhooks`` here; that path isn't
        in the OpenAPI spec yet, so it falls through to the legacy
        handler during Phase 2.
        """
        handler._json_response(
            HTTPStatus.OK,
            {"webhook_urls": list(handler.state.webhook_urls)},
        )


__all__ = ["StateGetRoutes"]
