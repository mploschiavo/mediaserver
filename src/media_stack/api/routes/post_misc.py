"""Misc POST routes (ADR-0007 Phase 2 wave 8 group 3).

Migrates six heterogeneous POST endpoints off the legacy
``handlers_post.handle()`` elif chain onto the OpenAPI Router.
The endpoints are grouped here because each one is too small to
merit its own file — the first four are URL-root legacy aliases
preserved for compose-mode operator scripts, and the last two are
niche operator endpoints whose domains have no other migrated
siblings.

Why grouped, not split per-file
-------------------------------

* ``POST /actions/{name}``           — generic action dispatch (root).
* ``POST /cancel``                   — cancel running action (root).
* ``POST /config``                   — runtime config write (root).
* ``POST /run``                      — bootstrap legacy alias (root).
* ``POST /api/jellyfin/reset``       — Jellyfin admin credential hard-reset.
* ``POST /api/validate-migration``   — disk-migration target preflight.

The first four have no ``/api`` prefix — they predate the SPA's
nginx-only ``/api/*`` proxy contract and are kept as legacy
aliases for direct ``curl`` use by operator scripts. The last
two are ad-hoc operator endpoints with no near siblings (the
``/api/media-server/reset`` migration in wave 5 covers Jellyfin
under the canonical media-server alias; ``/api/jellyfin/reset``
is its named sibling).

OO discipline (ADR-0007 + project-wide rule)
--------------------------------------------

* ``MiscPostRoutes`` is a ``RouteModule`` subclass with instance
  methods only — no ``@staticmethod``, no loose top-level
  handler functions.
* Constructor-injects every collaborator with module-default
  fall-backs that preserve the Router's zero-arg auto-discovery.
  Tests pass stubs to swap behaviour without monkey-patching.
* Six named patterns isolate the concerns inlined into the
  legacy elif chain:

  * ``KnownActionsProvider`` — Strategy that exposes the
    ``KNOWN_ACTIONS`` frozenset off ``handlers_post``. Constructor-
    injects an override so tests can pin the accept-list without
    importing the whole legacy module.
  * ``ActionTrigger`` — Strategy that delegates the
    ``handler._handle_action(action_name)`` call. Lifted because
    the route's only job after the accept-list check is "call the
    handler's action method"; the Strategy keeps the route body
    a one-liner.
  * ``ActionCanceller`` — Adapter onto
    ``handler.state.cancel_action()``. Same one-liner-route
    rationale.
  * ``ConfigWriter`` — Adapter onto ``handler.state.update_config``
    + log. Owns the empty-body 400 envelope.
  * ``JellyfinResetService`` — Adapter onto
    ``admin_svc.jellyfin_hard_reset`` with the env-default
    fallback shape mirroring ``MediaServerResetService`` from
    wave 5 (same env vars, same min-length rule).
  * ``MigrationValidator`` — Adapter onto
    ``disk_svc.validate_migration_target``.

Anti-pattern guard rails (ADR-0007 wave-3+4 retros)
---------------------------------------------------

* No lazy-cache resolver shape — every adapter caches ONLY a
  constructor-injected callable. The default path does a fresh
  attribute lookup on the service module each call so
  ``mock.patch`` on the canonical symbol takes effect.
* No ratchet baseline bumps. Every collaborator default keeps
  the legacy class structure intact.

Security preservation (project memory bug-class:
``csrf_double_submit``)
---------------------------------------------------------------

* CSRF is enforced at server.py for every POST that flows
  through the legacy chain (``_global_preflight``). Routes
  migrated to the Router bypass that gate, so this module
  installs the same ``PostMutationGate`` Strategy used by
  ``post_admin_ops``. The gate is invoked at the top of every
  handler method; tests can pass a permissive stub to exercise
  business logic in isolation.
"""

from __future__ import annotations

import logging
import os
from http import HTTPStatus
from typing import Any, Callable

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routing import RouteModule, post
from media_stack.core.logging_utils import log_swallowed


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — each value belongs to a named source-of-truth so
# string ratchets see one canonical site instead of inline magic
# strings scattered through the route bodies.
# ---------------------------------------------------------------------------

# Default admin username/password fallbacks used by the legacy
# Jellyfin reset handler when the request body omits them. Mirror
# the wave-5 ``MediaServerResetService`` constants — same env
# vars, same min-length rule, same default literals — so a future
# refactor can fold the two paths into a single Strategy.
_JF_RESET_USERNAME_ENV = "STACK_ADMIN_USERNAME"
_JF_RESET_PASSWORD_ENV = "STACK_ADMIN_PASSWORD"
_JF_RESET_DEFAULT_USERNAME = "admin"
_JF_RESET_DEFAULT_PASSWORD = "media-stack"
_JF_RESET_MIN_PASSWORD_LEN = 4

# Legacy alias: ``/run`` is a no-arg trigger that always dispatches
# the ``bootstrap`` action. Pulled out as a constant so the alias
# enum has one named site (rather than a literal ``"bootstrap"``
# in the route body).
_RUN_LEGACY_ALIAS_ACTION = "bootstrap"


# ---------------------------------------------------------------------------
# Adapter / Strategy collaborators
# ---------------------------------------------------------------------------


class KnownActionsProvider:
    """Strategy that exposes the ``KNOWN_ACTIONS`` accept-list.

    The legacy chain reads the frozenset off ``handlers_post`` —
    that file owns the contract-discovery + alias-merge logic.
    The Strategy lets tests inject a small fixture set without
    importing the whole legacy module (saves test-startup
    overhead + breaks an import cycle the auto-discovery would
    otherwise trip).

    The default path does a fresh module attribute lookup on
    each ``contains()`` call so a future ``KNOWN_ACTIONS`` rebuild
    (e.g. when a new contract job lands) takes effect without a
    process restart.
    """

    def __init__(
        self,
        known_actions: frozenset[str] | None = None,
    ) -> None:
        self._known_actions = known_actions

    def contains(self, action_name: str) -> bool:
        return action_name in self._resolve()

    def all(self) -> frozenset[str]:
        return self._resolve()

    def _resolve(self) -> frozenset[str]:
        if self._known_actions is not None:
            return self._known_actions
        from media_stack.api.services.known_actions import KNOWN_ACTIONS
        return KNOWN_ACTIONS


class ActionTrigger:
    """Strategy that delegates the
    ``handler._handle_action(action_name)`` call.

    Lifted because the route's only job after the accept-list
    check is "call the handler's action method"; the Strategy
    keeps the route body a one-liner. Constructor-injects an
    override so tests can capture the call without touching the
    handler.
    """

    def __init__(
        self,
        trigger_fn: Callable[[Any, str], None] | None = None,
    ) -> None:
        self._trigger = trigger_fn

    def trigger(self, handler: Any, action_name: str) -> None:
        if self._trigger is not None:
            self._trigger(handler, action_name)
            return
        handler._handle_action(action_name)


class ActionCanceller:
    """Adapter onto ``handler.state.cancel_action()``.

    The legacy chain reads ``state.cancel_action()`` directly off
    the handler; the Adapter pattern keeps the route body a
    one-liner + lets tests pass a fake state without forging the
    full ``ControllerState`` surface.
    """

    def __init__(
        self,
        cancel_fn: Callable[[Any], bool] | None = None,
    ) -> None:
        self._cancel = cancel_fn

    def cancel(self, state: Any) -> tuple[bool, Any]:
        """Return ``(cancelled, current_action)``.

        ``cancelled`` is True iff a running action was signalled to
        stop; ``current_action`` is the (possibly-None) action
        record off ``state.current_action`` after the cancel call.
        """
        if self._cancel is not None:
            cancelled = self._cancel(state)
        else:
            cancelled = state.cancel_action()
        current = getattr(state, "current_action", None)
        return cancelled, current


class ConfigWriter:
    """Adapter onto ``handler.state.update_config``.

    Owns the empty-body 400 envelope so the route body is a
    one-liner. The legacy chain logged the body via the module
    logger at INFO; the Adapter preserves the log call but lets
    tests mute it via constructor injection.
    """

    def __init__(
        self,
        update_fn: Callable[[Any, dict[str, Any]], dict[str, Any]] | None = None,
        log_fn: Callable[[str, Any], None] | None = None,
    ) -> None:
        self._update = update_fn
        self._log = log_fn

    def write(
        self, state: Any, body: dict[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        if not body:
            return HTTPStatus.BAD_REQUEST, {"error": "JSON body required"}
        if self._update is not None:
            updated = self._update(state, body)
        else:
            updated = state.update_config(body)
        if self._log is not None:
            self._log("Config updated: %s", body)
        else:
            logger.info("Config updated: %s", body)
        return HTTPStatus.OK, {"status": "updated", "config": updated}


class JellyfinResetService:
    """Adapter onto ``admin_svc.jellyfin_hard_reset``.

    Pulls the env-var fallbacks out of the route body so the
    handler stays a one-liner. Mirrors the wave-5
    ``MediaServerResetService`` shape — same env vars, same
    min-length rule, same default literals. The two paths can
    be folded into a single Strategy in a later cleanup wave;
    for now they share the constant names but keep their own
    classes so each wave's tests stay self-contained.
    """

    def __init__(
        self,
        reset_fn: Callable[[str, str], dict[str, Any]] | None = None,
        env_provider: Callable[..., str] | None = None,
    ) -> None:
        self._reset = reset_fn
        self._env_provider = env_provider or os.environ.get

    def reset(
        self, body: dict[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        body = body or {}
        username = body.get("username") or self._env_provider(
            _JF_RESET_USERNAME_ENV, _JF_RESET_DEFAULT_USERNAME,
        ) or _JF_RESET_DEFAULT_USERNAME
        password = body.get("password") or self._env_provider(
            _JF_RESET_PASSWORD_ENV, _JF_RESET_DEFAULT_PASSWORD,
        ) or _JF_RESET_DEFAULT_PASSWORD
        if not password or len(password) < _JF_RESET_MIN_PASSWORD_LEN:
            return HTTPStatus.BAD_REQUEST, {
                "error": (
                    f"password required (min {_JF_RESET_MIN_PASSWORD_LEN} chars)"
                ),
            }
        if self._reset is not None:
            return HTTPStatus.OK, self._reset(username, password)
        try:
            from media_stack.api.services import admin as admin_svc
            return HTTPStatus.OK, admin_svc.jellyfin_hard_reset(
                username, password,
            )
        except (ImportError, AttributeError) as exc:
            log_swallowed(
                "jellyfin reset hard-reset adapter not available",
                exc,
            )
            return HTTPStatus.INTERNAL_SERVER_ERROR, {
                "error": "jellyfin reset adapter not available",
            }


class MigrationValidator:
    """Adapter onto ``disk_svc.validate_migration_target``.

    The legacy chain pulled ``target_path`` off the JSON body and
    forwarded it; the Adapter keeps the route body a one-liner
    and lets tests pass a fake validator without monkey-patching.
    """

    def __init__(
        self,
        validate_fn: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self._validate = validate_fn

    def validate(self, target_path: str) -> dict[str, Any]:
        if self._validate is not None:
            return self._validate(target_path)
        from media_stack.api.services import disk as disk_svc
        return disk_svc.validate_migration_target(target_path)


# ---------------------------------------------------------------------------
# RouteModule
# ---------------------------------------------------------------------------


class MiscPostRoutes(RouteModule):
    """Misc POST routes covering the URL-root legacy aliases plus
    Jellyfin reset and migration validator.

    The Router auto-discovers + instantiates this class + walks
    its tagged methods at startup. Constructor defaults keep
    auto-discovery zero-arg while letting tests swap any
    collaborator.
    """

    def __init__(
        self,
        *,
        mutation_gate: PostMutationGate | None = None,
        known_actions: KnownActionsProvider | None = None,
        action_trigger: ActionTrigger | None = None,
        action_canceller: ActionCanceller | None = None,
        config_writer: ConfigWriter | None = None,
        jellyfin_reset: JellyfinResetService | None = None,
        migration_validator: MigrationValidator | None = None,
    ) -> None:
        self._gate = mutation_gate or PostMutationGate()
        self._known_actions = known_actions or KnownActionsProvider()
        self._action_trigger = action_trigger or ActionTrigger()
        self._action_canceller = action_canceller or ActionCanceller()
        self._config_writer = config_writer or ConfigWriter()
        self._jellyfin_reset = jellyfin_reset or JellyfinResetService()
        self._migration_validator = (
            migration_validator or MigrationValidator()
        )

    # --- gate helper ---------------------------------------------------

    def _gated(self, handler: Any) -> bool:
        """Run the CSRF gate; emit 403 + return False on rejection."""
        if not self._gate.verify(handler):
            self._gate.reject(handler)
            return False
        return True

    # --- routes --------------------------------------------------------

    @post("/actions/{name}")
    def handle_action(self, handler: Any, *, name: str) -> None:
        """Generic action dispatch — root-path legacy alias.

        ``name`` must be in the controller's ``KNOWN_ACTIONS``
        accept-list (built from core actions + contract-discovered
        jobs + their declared aliases). Unknown names return 404
        with the known set so an operator can spot a typo in one
        round-trip.

        The body (optional JSON) flows through to the handler's
        ``_handle_action`` which forwards it as ``overrides``.

        SPA consumers should hit ``/api/actions/{name}`` instead
        (``handle_action_api`` below) — the SPA's nginx config
        only proxies ``/api/*`` to the controller. Same dispatch
        body; just routed under the proxied prefix.
        """
        if not self._gated(handler):
            return
        # ``/actions/cancel`` is a legacy alias for ``/cancel`` —
        # the operator's curl scripts predate the dedicated cancel
        # path, so we keep it routed here. Delegate to the canonical
        # cancel handler so the response shape matches.
        if name == "cancel":
            self.handle_cancel(handler)
            return
        if not self._known_actions.contains(name):
            handler._json_response(
                HTTPStatus.NOT_FOUND,
                {
                    "error": f"unknown action '{name}'",
                    "known": sorted(self._known_actions.all()),
                },
            )
            return
        self._action_trigger.trigger(handler, name)

    @post("/api/actions/{name}")
    def handle_action_api(self, handler: Any, *, name: str) -> None:
        """Dashboard-facing alias of ``handle_action``.

        Same dispatch body and response shape as
        ``POST /actions/{name}``. The alias exists so the SPA's
        ``location /api/`` nginx block reaches the controller
        without needing a separate ``location = /actions`` rule
        per nginx config — without it, the SPA's "Run now" /
        "Cancel" buttons hit the SPA-fallback ``try_files`` block
        and the operator sees "unknown path
        '/api/actions/<name>'". This was the same bug class
        ADR-0005 Phase 5a fixed for ``/status`` -> ``/api/status``.
        """
        if not self._gated(handler):
            return
        if name == "cancel":
            self.handle_cancel(handler)
            return
        if not self._known_actions.contains(name):
            handler._json_response(
                HTTPStatus.NOT_FOUND,
                {
                    "error": f"unknown action '{name}'",
                    "known": sorted(self._known_actions.all()),
                },
            )
            return
        self._action_trigger.trigger(handler, name)

    @post("/cancel")
    def handle_cancel(self, handler: Any) -> None:
        """Cancel the currently-running action — root-path legacy
        alias.

        Returns ``{status: "cancel_requested" | "no_action_running",
        current_action: ...}``. The current-action record is taken
        AFTER the cancel call so a successful cancel still returns
        the running record (caller polls ``/status`` to confirm
        the action has stopped).
        """
        if not self._gated(handler):
            return
        cancelled, current = self._action_canceller.cancel(handler.state)
        record = current.to_dict() if current is not None else None
        handler._json_response(
            HTTPStatus.OK,
            {
                "status": (
                    "cancel_requested" if cancelled else "no_action_running"
                ),
                "current_action": record,
            },
        )

    @post("/config")
    def handle_config(self, handler: Any) -> None:
        """Write a partial config update to the controller's
        runtime state — root-path legacy alias.

        Body is a JSON object whose keys overlay onto the existing
        ``runtime_config`` blob; the merged result is returned in
        the response. An empty body returns 400.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body()
        status, response = self._config_writer.write(handler.state, body)
        handler._json_response(status, response)

    @post("/run")
    def handle_run(self, handler: Any) -> None:
        """Trigger ``bootstrap`` — root-path legacy alias.

        Backward-compatible alias for ``POST /actions/bootstrap``.
        Body (optional JSON) flows through to ``_handle_action``
        as overrides.
        """
        if not self._gated(handler):
            return
        self._action_trigger.trigger(handler, _RUN_LEGACY_ALIAS_ACTION)

    @post("/api/jellyfin/reset")
    def handle_jellyfin_reset(self, handler: Any) -> None:
        """Hard-reset Jellyfin admin credentials.

        Body: ``{username?: str, password?: str}``. Both fields
        fall back to the ``STACK_ADMIN_USERNAME`` /
        ``STACK_ADMIN_PASSWORD`` env vars and then to the literal
        ``"admin"`` / ``"media-stack"`` defaults if the env vars
        aren't set. Passwords shorter than 4 characters return 400.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body()
        status, response = self._jellyfin_reset.reset(body)
        handler._json_response(status, response)

    @post("/api/validate-migration")
    def handle_validate_migration(self, handler: Any) -> None:
        """Pre-flight a disk-migration target path.

        Body: ``{target_path: str}``. Returns capacity info,
        writability check, and the rsync command to execute.
        Missing body fields default to empty string — the service
        layer's validator emits the canonical ``{valid: false,
        error: ...}`` envelope on a bad input.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        result = self._migration_validator.validate(
            str(body.get("target_path", "")),
        )
        handler._json_response(HTTPStatus.OK, result)


__all__ = [
    "ActionCanceller",
    "ActionTrigger",
    "ConfigWriter",
    "JellyfinResetService",
    "KnownActionsProvider",
    "MigrationValidator",
    "MiscPostRoutes",
]
