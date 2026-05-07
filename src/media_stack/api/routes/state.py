"""State-domain GET routes (ADR-0007 Phase 2).

Covers the ``/status``, ``/apps``, ``/apps/{app_name}``, ``/config``,
and ``/webhooks`` GET endpoints. These all read from
``handler.state`` (the in-memory ``ControllerState``) — the
"domain" name comes from the State tag in ``contracts/api/openapi.yaml``.

Each method body is lifted verbatim from the legacy
``handlers_get.GetRequestHandler.handle()`` chain. Phase 2 only
moves WHERE the dispatch decision is made (Router instead of an
``elif`` chain); the response shape is identical so downstream
consumers see no change.

Note on parameterized routes: ``/apps/{app_name}`` declares
``app_name`` as a path parameter in the OpenAPI spec, so the
Router passes it to the handler as the ``app_name`` kwarg. The
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
        """Full controller state — action history, runtime config,
        app statuses, webhook URLs, ``initial_bootstrap_done``.

        Bare-path endpoint historically used by ``kubectl exec``
        health probes and the CLI wait service (``ControllerJobWait
        Service`` / ``BootstrapPodHttpClient``).

        SPA consumers should hit ``/api/status`` instead — the SPA's
        nginx config (``deploy/compose/ui-nginx.conf``,
        ``deploy/k8s/.../ui-nginx.conf``) only proxies ``/api/*`` to
        the controller; a bare ``/status`` falls into the SPA
        ``try_files`` fallback and returns ``index.html``.
        ``handle_status_api`` below is the dashboard-facing alias.
        """
        handler._json_response(HTTPStatus.OK, handler.state.to_dict())

    @get("/api/status")
    def handle_status_api(self, handler: Any) -> None:
        """Dashboard-facing alias of ``handle_status``.

        Same response body as ``GET /status``. The alias exists so
        the SPA's ``location /api/ { proxy_pass ... }`` block reaches
        the controller without needing a separate ``location =
        /status`` rule per nginx config. See ``handle_status`` above
        for context.
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

    @get("/apps/{app_name}")
    def handle_app_by_name(self, handler: Any, app_name: str) -> None:
        """Single-app bootstrap status.

        Returns the app's status entry on hit, ``404`` with an
        ``error`` field if the app isn't tracked. ``app_name`` is
        bound by the Router from the path segment per the OpenAPI
        ``parameters: [{name: app_name, in: path}]`` declaration.
        """
        info = handler.state.app_status.get(app_name)
        if info is not None:
            handler._json_response(HTTPStatus.OK, {app_name: info})
        else:
            handler._json_response(
                HTTPStatus.NOT_FOUND,
                {"error": f"app '{app_name}' not found"},
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
