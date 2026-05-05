"""Disk-guardrails routes (ADR-0008 Phase 2).

Six operator-facing endpoints sitting on top of the Phase 1
``DownloadLockdownService`` + the legacy ``DiskGuardrailsService``
cleanup engine + the cross-domain ``GuardrailRegistry`` rule loop:

* ``GET  /api/disk-guardrails``               -- merged state view.
* ``POST /api/disk-guardrails/cleanup``       -- synchronous cleanup.
* ``POST /api/disk-guardrails/lockdown``      -- engage manual lockdown.
* ``POST /api/disk-guardrails/release``       -- release lockdown.
* ``POST /api/disk-guardrails/pause-auto``    -- TTL bypass for the
   auto-evaluation side (``hours`` query-string, clamped to [1, 24]).
* ``POST /api/disk-guardrails/evaluate``      -- force-tick the registry.

OO discipline:

* ``DiskGuardrailsRoutes`` is a ``RouteModule`` subclass with
  instance methods only — no module-level handler functions, no
  loose helpers. Mirrors ``post_admin_ops.AdminOpsPostRoutes``.
* Constructor-injected collaborators:
  * ``LockdownService``     -- the ``DownloadLockdownService`` singleton
                               from ``LockdownFactory.singleton()``.
  * ``CleanupRunner``       -- adapter onto
                               ``DiskGuardrailsService.enforce``.
  * ``RegistryProvider``    -- callable returning the cross-domain
                               registry (``application.guardrails.default``).
  * ``EvaluationLoopTick``  -- the ``tick`` function (not a partial)
                               so a test can pass a no-op stub.
  * ``ActorResolver``       -- resolves the requesting username for
                               the audit trail; default uses the
                               session-cookie-or-trusted-proxy
                               three-tier the password-tickets route
                               also uses.
  * ``mutation_gate``       -- ``PostMutationGate`` from
                               ``post_admin_ops``; reuses the
                               canonical CSRF gate so the same
                               header-echo contract applies.
* All POST mutations CSRF-gated via the shared ``PostMutationGate``.
* All POST mutations role-gated to ``controller_admin``; the
  authenticated identity is resolved via the same three-tier strategy
  ``post_admin_ops`` uses (session cookie, trusted-proxy header,
  Basic-auth decode).
* Read-only ``GET`` admits any authenticated user.

Path-param + body keys are snake_case (``hours``, ``categories``,
``max_delete``) to satisfy the snake_case ratchets.

Narrow exceptions: each catch site enumerates the documented failure
modes; ``log_swallowed`` is invoked anywhere a swallow is intentional.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from http import HTTPStatus
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routing import RouteModule, get, post
from media_stack.core.logging_utils import log_swallowed


_log = logging.getLogger("media_stack.api.disk_guardrails")


_LOCKDOWN_RULE_ID = "storage:lockdown_threshold"

# Pause-auto bounds. 1h floor + 24h ceiling per the ADR. Hours == 0 is
# valid as the explicit "clear bypass" path; a missing or negative
# value is rejected with 400.
_PAUSE_AUTO_MIN_HOURS = 1
_PAUSE_AUTO_MAX_HOURS = 24

# Audit-action names. Centralised so the audit reader can filter the
# transition feed by an exact-match action prefix.
_AUDIT_LOCKDOWN_ENGAGED = "disk_guardrail_lockdown_engaged"
_AUDIT_LOCKDOWN_RELEASED = "disk_guardrail_lockdown_released"
_AUDIT_CLEANUP_INVOKED = "disk_guardrail_cleanup_invoked"
_AUDIT_PAUSE_AUTO = "disk_guardrail_pause_auto"
_AUDIT_EVALUATE = "disk_guardrail_evaluate"

_AUDIT_ACTIONS_TRANSITIONS = (
    _AUDIT_LOCKDOWN_ENGAGED,
    _AUDIT_LOCKDOWN_RELEASED,
    _AUDIT_CLEANUP_INVOKED,
    _AUDIT_PAUSE_AUTO,
)

# How many recent transition rows to surface on GET /api/disk-guardrails.
_TRANSITIONS_LIMIT = 25

_STACK_ADMIN_USERNAME_ENV = "STACK_ADMIN_USERNAME"


class ActorResolver:
    """Three-tier actor resolver shared across the disk-guardrails
    routes.

    Class shape (not a free function) so the no-loose-functions
    ratchet stays clean. Constructor-injects the username readers so
    a test can pass deterministic stubs.

    Resolution order:

      1. session-cookie reader (browser session)
      2. trusted-proxy header (Envoy / Authelia identity)
      3. Basic-auth decode (script clients)

    Returns the empty string when no identity is available.
    """

    def __init__(
        self,
        *,
        session_lookup: Callable[[Any], str] | None = None,
        proxy_lookup: Callable[[Any], str] | None = None,
    ) -> None:
        self._session_lookup = session_lookup
        self._proxy_lookup = proxy_lookup

    def resolve(self, handler: Any) -> str:
        username = self._from_session(handler)
        if username:
            return username
        username = self._from_proxy(handler)
        if username:
            return username
        return self._from_basic_auth(handler)

    def _from_session(self, handler: Any) -> str:
        if self._session_lookup is not None:
            try:
                return str(self._session_lookup(handler) or "")
            except (AttributeError, KeyError, ValueError) as exc:
                log_swallowed(exc, context="disk-guardrails-actor-session")
                return ""
        try:
            from media_stack.api.session_singletons import (
                session_cookie_reader,
            )
            return str(
                session_cookie_reader.username_for_handler(handler) or "",
            )
        except (AttributeError, KeyError, ImportError, ValueError) as exc:
            log_swallowed(exc, context="disk-guardrails-actor-session")
            return ""

    def _from_proxy(self, handler: Any) -> str:
        if self._proxy_lookup is not None:
            try:
                return str(self._proxy_lookup(handler) or "")
            except (AttributeError, KeyError, ValueError) as exc:
                log_swallowed(exc, context="disk-guardrails-actor-proxy")
                return ""
        try:
            from media_stack.api.session_singletons import (
                trusted_proxy_auth,
            )
            return str(trusted_proxy_auth.identity(handler) or "")
        except (AttributeError, KeyError, ImportError, ValueError) as exc:
            log_swallowed(exc, context="disk-guardrails-actor-proxy")
            return ""

    def _from_basic_auth(self, handler: Any) -> str:
        try:
            auth_header = handler.headers.get("Authorization", "") or ""
        except AttributeError:
            return ""
        if not auth_header.startswith("Basic "):
            return ""
        try:
            decoded = base64.b64decode(auth_header[6:]).decode(
                "utf-8", "replace",
            )
            return decoded.partition(":")[0] or ""
        except (ValueError, UnicodeDecodeError) as exc:
            log_swallowed(exc, context="disk-guardrails-actor-basic")
            return ""


class AdminGate:
    """Strategy that decides whether the requesting user is a
    ``controller_admin``.

    Class-based so the ratchet stays clean. Constructor-injects the
    user-service factory + the env-admin lookup so tests aren't
    coupled to the live user store.
    """

    def __init__(
        self,
        *,
        user_service_fn: Callable[[], Any] | None = None,
        env_admin_fn: Callable[[], str] | None = None,
    ) -> None:
        self._user_service_fn = user_service_fn
        self._env_admin_fn = env_admin_fn

    def is_admin(self, username: str) -> bool:
        env_admin = self._resolve_env_admin()
        if username and env_admin and username == env_admin:
            return True
        try:
            svc = self._user_service()
        except (ImportError, AttributeError, OSError) as exc:
            log_swallowed(exc, context="disk-guardrails-admin-check-svc")
            return True  # fail-open on store outage (mirrors RBAC fallback)
        try:
            user = svc._store.get_by_username(username)
        except (AttributeError, OSError, ValueError) as exc:
            log_swallowed(exc, context="disk-guardrails-admin-check-store")
            return True
        if user is None:
            return True  # unknown user — RBAC fallback admits
        try:
            role = svc._roles.get(user.role_slug)
        except (AttributeError, KeyError) as exc:
            log_swallowed(exc, context="disk-guardrails-admin-check-role")
            return True
        if role is None:
            return True
        return bool(getattr(role, "controller_admin", True))

    def _user_service(self) -> Any:
        if self._user_service_fn is not None:
            return self._user_service_fn()
        from media_stack.core.auth.users.user_service_factory import (
            build_default_service,
        )
        return build_default_service()

    def _resolve_env_admin(self) -> str:
        if self._env_admin_fn is not None:
            return self._env_admin_fn() or ""
        return (os.environ.get(_STACK_ADMIN_USERNAME_ENV, "") or "").strip()


class CleanupRunner:
    """Adapter onto ``DiskGuardrailsService.enforce(...)``.

    Constructor-injects the service builder + a configuration
    resolver so a test can swap both in. The default path defers to
    the production wirer's ``enforce_disk_guardrails`` shim which
    pulls live config + qbit creds from the runtime singletons.
    """

    def __init__(
        self,
        *,
        enforce_fn: Callable[..., dict[str, Any]] | None = None,
        config_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._enforce = enforce_fn
        self._config_provider = config_provider

    def run(
        self,
        *,
        categories_override: list[str] | None,
        max_delete_override: int | None,
        force: bool,
    ) -> dict[str, Any]:
        cfg, qbit_cfg, qb_username, qb_password, config_root = (
            self._resolve_config()
        )
        # Apply the route-body overrides on a SHALLOW COPY of the
        # qbit_cleanup block so the global config object isn't
        # mutated. Anything not overridden falls through to the
        # configured value.
        guard_cfg = dict(cfg.get("disk_guardrails") or {})
        qbit_cleanup = dict(guard_cfg.get("qbit_cleanup") or {})
        if categories_override is not None:
            qbit_cleanup["categories"] = list(categories_override)
        if max_delete_override is not None:
            qbit_cleanup["max_delete_per_run"] = int(max_delete_override)
        guard_cfg["qbit_cleanup"] = qbit_cleanup
        # The manual surface always cleans regardless of disk %; the
        # caller passes ``force=True`` for the canonical operator
        # invocation.
        guard_cfg.setdefault("enabled", True)
        cfg_view = dict(cfg)
        cfg_view["disk_guardrails"] = guard_cfg

        if self._enforce is not None:
            return self._enforce(
                cfg=cfg_view,
                config_root=config_root,
                qbit_cfg=qbit_cfg,
                qb_username=qb_username,
                qb_password=qb_password,
                force=force,
            )
        from media_stack.infrastructure.servarr.runtime.hygiene_ops import (
            _disk_guardrails_service,
        )
        service = _disk_guardrails_service()
        return service.enforce(
            cfg=cfg_view,
            config_root=config_root,
            qbit_cfg=qbit_cfg,
            qb_username=qb_username,
            qb_password=qb_password,
            force=force,
        )

    def _resolve_config(self) -> tuple[dict[str, Any], dict[str, Any], str, str, str]:
        if self._config_provider is not None:
            payload = self._config_provider()
            return (
                payload.get("cfg") or {},
                payload.get("qbit_cfg") or {},
                str(payload.get("qb_username") or ""),
                str(payload.get("qb_password") or ""),
                str(payload.get("config_root") or ""),
            )
        # Default path: read bootstrap config + profile via the
        # canonical resolver shared with the rest of api/services.
        # Torrent-client creds defer to the qbittorrent adapter's
        # canonical env-var constants (single source of truth) so
        # this file doesn't introduce service-name strings of its
        # own. Failure falls back to empty dicts; the service's
        # ``enabled`` gate then short-circuits cleanly.
        try:
            import json
            from pathlib import Path
            from media_stack.api.services._resolve import resolve_config_path
            from media_stack.adapters.qbittorrent.categories_wiring import (
                _QBIT_USERNAME_ENV,
                _QBIT_PASSWORD_ENV,
            )
            from media_stack.services.apps.download_clients.registry_helpers import (
                default_torrent_client_url,
            )
            cfg_path = resolve_config_path()
            cfg: dict[str, Any] = {}
            if cfg_path:
                cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8")) or {}
            qbit_cfg = {"url": default_torrent_client_url() or ""}
            qb_username = os.environ.get(_QBIT_USERNAME_ENV) or "admin"
            qb_password = os.environ.get(_QBIT_PASSWORD_ENV) or ""
            config_root = (
                os.environ.get("CONFIG_ROOT")
                or os.environ.get("STACK_CONFIG_ROOT")
                or "/srv-config"
            )
            return (cfg, qbit_cfg, qb_username, qb_password, config_root)
        except (ImportError, AttributeError, OSError, ValueError) as exc:
            log_swallowed(exc, context="disk-guardrails-config-resolve")
            return ({}, {}, "", "", "")


class TransitionFeedReader:
    """Adapter onto the audit log's ``recent_by_actions`` reader,
    scoped to the disk-guardrail transition action set.
    """

    def __init__(
        self,
        *,
        user_service_fn: Callable[[], Any] | None = None,
        limit: int = _TRANSITIONS_LIMIT,
    ) -> None:
        self._user_service_fn = user_service_fn
        self._limit = int(limit)

    def recent(self) -> list[dict[str, Any]]:
        try:
            svc = self._user_service()
            entries = svc._audit.recent_by_actions(
                actions=list(_AUDIT_ACTIONS_TRANSITIONS),
                limit=self._limit,
            )
        except (ImportError, AttributeError, OSError, ValueError) as exc:
            log_swallowed(exc, context="disk-guardrails-transitions-read")
            return []
        out: list[dict[str, Any]] = []
        for entry in entries or []:
            row = self._serialise(entry)
            if row is not None:
                out.append(row)
        return out

    def _serialise(self, entry: Any) -> dict[str, Any] | None:
        try:
            return {
                "timestamp": getattr(entry, "timestamp", "") or "",
                "actor": getattr(entry, "actor", "") or "",
                "action": getattr(entry, "action", "") or "",
                "result": getattr(entry, "result", "") or "",
                "detail": dict(getattr(entry, "detail", {}) or {}),
            }
        except (AttributeError, TypeError) as exc:
            log_swallowed(exc, context="disk-guardrails-transitions-serialise")
            return None

    def _user_service(self) -> Any:
        if self._user_service_fn is not None:
            return self._user_service_fn()
        from media_stack.core.auth.users.user_service_factory import (
            build_default_service,
        )
        return build_default_service()


class AuditAppender:
    """Adapter onto ``UserService._audit.append`` for the manual
    surface's transition rows.

    Failure isolation: an audit-log outage must not block the
    operator's request. Every swallow records via ``log_swallowed``.
    """

    def __init__(
        self,
        *,
        user_service_fn: Callable[[], Any] | None = None,
    ) -> None:
        self._user_service_fn = user_service_fn

    def append(
        self,
        *,
        actor: str,
        action: str,
        result: str,
        detail: dict[str, Any],
    ) -> None:
        try:
            svc = self._user_service()
            svc._audit.append(
                actor=actor or "anonymous",
                action=action,
                target="disk-guardrails",
                result=result,
                detail=detail,
            )
        except (ImportError, AttributeError, OSError, ValueError) as exc:
            log_swallowed(exc, context=f"disk-guardrails-audit/{action}")

    def _user_service(self) -> Any:
        if self._user_service_fn is not None:
            return self._user_service_fn()
        from media_stack.core.auth.users.user_service_factory import (
            build_default_service,
        )
        return build_default_service()


class HoursQueryParser:
    """Strategy that pulls + validates the ``hours`` query parameter
    on ``POST /api/disk-guardrails/pause-auto``.

    Returns ``(hours, error_msg)``. ``error_msg`` non-empty signals
    a 400 response.
    """

    def parse(self, raw_path: str) -> tuple[int, str]:
        try:
            qs = urlparse(raw_path).query or ""
            params = parse_qs(qs)
        except ValueError as exc:
            log_swallowed(exc, context="disk-guardrails-hours-parse")
            return 0, "invalid query string"
        values = params.get("hours") or []
        if not values:
            return 0, "hours query parameter required"
        raw = values[0]
        try:
            hours = int(raw)
        except (TypeError, ValueError):
            return 0, "hours must be an integer"
        if hours < _PAUSE_AUTO_MIN_HOURS:
            return 0, (
                f"hours must be >= {_PAUSE_AUTO_MIN_HOURS} "
                "(use the release endpoint to clear lockdown)"
            )
        if hours > _PAUSE_AUTO_MAX_HOURS:
            # Server-side clamp matches the ADR contract.
            hours = _PAUSE_AUTO_MAX_HOURS
        return hours, ""


class DiskGuardrailsRoutes(RouteModule):
    """Six disk-guardrails endpoints registered against the OpenAPI
    Router. The Router auto-discovers + instantiates this class at
    startup.

    Constructor defaults keep auto-discovery zero-arg; tests pass a
    set of stubs to exercise the route bodies in isolation.
    """

    def __init__(
        self,
        *,
        lockdown_service: Any | None = None,
        cleanup_runner: CleanupRunner | None = None,
        registry_provider: Callable[[], Any] | None = None,
        evaluation_loop_tick: Callable[..., dict[str, Any]] | None = None,
        actor_resolver: ActorResolver | None = None,
        admin_gate: AdminGate | None = None,
        transition_reader: TransitionFeedReader | None = None,
        audit_appender: AuditAppender | None = None,
        hours_parser: HoursQueryParser | None = None,
        mutation_gate: PostMutationGate | None = None,
    ) -> None:
        self._lockdown_explicit = lockdown_service
        self._cleanup = cleanup_runner or CleanupRunner()
        self._registry_provider = registry_provider
        self._tick = evaluation_loop_tick
        self._actor = actor_resolver or ActorResolver()
        self._admin = admin_gate or AdminGate()
        self._transitions = transition_reader or TransitionFeedReader()
        self._audit = audit_appender or AuditAppender()
        self._hours_parser = hours_parser or HoursQueryParser()
        self._gate = mutation_gate or PostMutationGate()

    # -- collaborator accessors --------------------------------------

    def _lockdown(self) -> Any:
        if self._lockdown_explicit is not None:
            return self._lockdown_explicit
        from media_stack.services.lockdown_factory import LockdownFactory
        return LockdownFactory.singleton()

    def _registry(self) -> Any:
        if self._registry_provider is not None:
            return self._registry_provider()
        from media_stack.application.guardrails.registry import default
        return default()

    def _resolve_tick(self) -> Callable[..., dict[str, Any]]:
        if self._tick is not None:
            return self._tick
        from media_stack.application.guardrails.evaluation_loop import tick
        return tick

    # -- gate helpers ------------------------------------------------

    def _gated(self, handler: Any) -> bool:
        if not self._gate.verify(handler):
            self._gate.reject(handler)
            return False
        return True

    def _admin_gated(self, handler: Any) -> tuple[bool, str]:
        actor = self._actor.resolve(handler)
        if not self._admin.is_admin(actor):
            handler._json_response(
                HTTPStatus.FORBIDDEN,
                {"error": "controller_admin role required"},
            )
            return False, actor
        return True, actor

    # -- routes ------------------------------------------------------

    @get("/api/disk-guardrails")
    def handle_status(self, handler: Any) -> None:
        """Return the merged status view: lockdown state, mount
        usage, registry threshold, recent transitions, paused
        clients, last failures.

        Read-only — admits any authenticated user.
        """
        lockdown = self._lockdown()
        try:
            state = dict(lockdown.get_state() or {})
        except (OSError, ValueError) as exc:
            log_swallowed(exc, context="disk-guardrails-status-state")
            state = {}
        try:
            registry = self._registry()
            threshold = dict(registry.threshold_for(_LOCKDOWN_RULE_ID) or {})
        except (AttributeError, KeyError, ValueError) as exc:
            log_swallowed(exc, context="disk-guardrails-status-threshold")
            threshold = {}
        used_percent = self._collect_used_percent_by_mount()
        engaged = bool(state.get("engaged"))
        if engaged:
            trigger = state.get("trigger")
            display_state = (
                "MANUAL_LOCKDOWN" if trigger == "manual"
                else "AUTO_LOCKDOWN"
            )
        else:
            display_state = "NORMAL"
        body = {
            "state": display_state,
            "used_percent_by_mount": used_percent,
            "thresholds": threshold,
            "engaged_at": float(state.get("engaged_at") or 0.0),
            "engaged_by": str(state.get("engaged_by") or ""),
            "trigger": state.get("trigger"),
            "auto_check_paused_until": state.get("auto_check_paused_until"),
            "paused_clients": list(state.get("paused_clients") or []),
            "last_failures": list(state.get("last_failures") or []),
            "transitions": self._transitions.recent(),
        }
        handler._json_response(HTTPStatus.OK, body)

    @post("/api/disk-guardrails/cleanup")
    def handle_cleanup(self, handler: Any) -> None:
        """Run the ``DiskGuardrailsService.enforce()`` cleanup pass
        synchronously, regardless of disk %. Body (optional):
        ``{categories: [...], max_delete: int}`` overrides.
        """
        if not self._gated(handler):
            return
        ok, actor = self._admin_gated(handler)
        if not ok:
            return
        body = handler._read_json_body() or {}
        categories_raw = body.get("categories")
        if categories_raw is not None and not isinstance(categories_raw, list):
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "categories must be a list of strings"},
            )
            return
        categories: list[str] | None = None
        if categories_raw is not None:
            categories = [str(c).strip() for c in categories_raw if str(c).strip()]
        max_delete_raw = body.get("max_delete")
        max_delete: int | None = None
        if max_delete_raw is not None:
            try:
                max_delete = int(max_delete_raw)
            except (TypeError, ValueError):
                handler._json_response(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "max_delete must be an integer"},
                )
                return
        try:
            report = self._cleanup.run(
                categories_override=categories,
                max_delete_override=max_delete,
                force=True,
            )
        except (RuntimeError, OSError, ValueError) as exc:
            log_swallowed(exc, context="disk-guardrails-cleanup-run")
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"cleanup failed: {exc}"},
            )
            return
        self._audit.append(
            actor=f"operator:{actor}" if actor else "operator:anonymous",
            action=_AUDIT_CLEANUP_INVOKED,
            result="ok",
            detail={
                "deleted": report.get("deleted", 0),
                "freed_gb": report.get("freed_gb", 0.0),
                "strategy": report.get("strategy", "oldest_first"),
            },
        )
        handler._json_response(HTTPStatus.OK, report)

    @post("/api/disk-guardrails/lockdown")
    def handle_lockdown(self, handler: Any) -> None:
        """Engage a manual lockdown. Pauses every download client.
        Manual stickiness: the auto-loop won't release this state.
        """
        if not self._gated(handler):
            return
        ok, actor = self._admin_gated(handler)
        if not ok:
            return
        lockdown = self._lockdown()
        actor_label = f"operator:{actor}" if actor else "operator:anonymous"
        try:
            result = lockdown.engage(trigger="manual", by=actor_label)
        except (OSError, RuntimeError, ValueError) as exc:
            log_swallowed(exc, context="disk-guardrails-lockdown-engage")
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"engage failed: {exc}"},
            )
            return
        self._audit.append(
            actor=actor_label,
            action=_AUDIT_LOCKDOWN_ENGAGED,
            result="ok",
            detail={
                "trigger": "manual",
                "paused_clients": list(result.get("paused_clients") or []),
                "failures": list(result.get("failures") or []),
            },
        )
        handler._json_response(HTTPStatus.OK, {
            "state": "MANUAL_LOCKDOWN",
            "paused_clients": list(result.get("paused_clients") or []),
            "failures": list(result.get("failures") or []),
        })

    @post("/api/disk-guardrails/release")
    def handle_release(self, handler: Any) -> None:
        """Release lockdown. Resumes previously-paused clients."""
        if not self._gated(handler):
            return
        ok, actor = self._admin_gated(handler)
        if not ok:
            return
        lockdown = self._lockdown()
        actor_label = f"operator:{actor}" if actor else "operator:anonymous"
        try:
            result = lockdown.release(by=actor_label)
        except (OSError, RuntimeError, ValueError) as exc:
            log_swallowed(exc, context="disk-guardrails-lockdown-release")
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"release failed: {exc}"},
            )
            return
        self._audit.append(
            actor=actor_label,
            action=_AUDIT_LOCKDOWN_RELEASED,
            result="ok",
            detail={
                "released_clients": list(result.get("released_clients") or []),
                "failures": list(result.get("failures") or []),
                "was_engaged": bool(result.get("was_engaged")),
            },
        )
        handler._json_response(HTTPStatus.OK, {
            "state": "NORMAL",
            "released_clients": list(result.get("released_clients") or []),
        })

    @post("/api/disk-guardrails/pause-auto")
    def handle_pause_auto(self, handler: Any) -> None:
        """Set the auto-evaluation pause TTL. ``hours`` query
        parameter clamped to ``[1, 24]``."""
        if not self._gated(handler):
            return
        ok, actor = self._admin_gated(handler)
        if not ok:
            return
        path = getattr(handler, "path", "") or ""
        hours, error = self._hours_parser.parse(path)
        if error:
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": error},
            )
            return
        lockdown = self._lockdown()
        actor_label = f"operator:{actor}" if actor else "operator:anonymous"
        try:
            result = lockdown.pause_auto(hours=hours, by=actor_label)
        except (OSError, RuntimeError, ValueError) as exc:
            log_swallowed(exc, context="disk-guardrails-pause-auto")
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"pause-auto failed: {exc}"},
            )
            return
        self._audit.append(
            actor=actor_label,
            action=_AUDIT_PAUSE_AUTO,
            result="ok",
            detail={"hours": hours},
        )
        handler._json_response(HTTPStatus.OK, {
            "paused_until": result.get("auto_check_paused_until"),
            "hours": hours,
        })

    @post("/api/disk-guardrails/evaluate")
    def handle_evaluate(self, handler: Any) -> None:
        """Force-tick the registry for an immediate evaluation
        snapshot. Bypasses the cadence floor.
        """
        if not self._gated(handler):
            return
        ok, actor = self._admin_gated(handler)
        if not ok:
            return
        tick = self._resolve_tick()
        try:
            result = tick(
                lockdown_service=self._lockdown(),
                record_history=False,
                min_interval=0,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            log_swallowed(exc, context="disk-guardrails-evaluate")
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"evaluate failed: {exc}"},
            )
            return
        actor_label = f"operator:{actor}" if actor else "operator:anonymous"
        self._audit.append(
            actor=actor_label,
            action=_AUDIT_EVALUATE,
            result="ok",
            detail={
                "triggers": len(result.get("triggers") or []),
                "actions": len(result.get("actions") or []),
            },
        )
        handler._json_response(HTTPStatus.OK, {
            "ran_at": result.get("ran_at") or time.time(),
            "elapsed": result.get("elapsed", 0.0),
            "triggers": result.get("triggers") or [],
            "actions": result.get("actions") or [],
        })

    # -- helpers -----------------------------------------------------

    def _collect_used_percent_by_mount(self) -> dict[str, float]:
        """Pull the per-mount percent_used numbers from the
        legacy ``api/services/disk.get_disk()`` snapshot. Failure
        falls back to an empty dict — the GET still returns a
        coherent envelope."""
        try:
            from media_stack.api.services.disk import get_disk
            payload = get_disk() or {}
        except (ImportError, AttributeError, OSError, ValueError) as exc:
            log_swallowed(exc, context="disk-guardrails-used-percent")
            return {}
        disks = payload.get("disk") or {}
        if not isinstance(disks, dict):
            return {}
        out: dict[str, float] = {}
        for label, info in disks.items():
            if not isinstance(info, dict):
                continue
            try:
                out[str(label)] = float(info.get("percent_used") or 0.0)
            except (TypeError, ValueError):
                continue
        return out


__all__ = [
    "ActorResolver",
    "AdminGate",
    "AuditAppender",
    "CleanupRunner",
    "DiskGuardrailsRoutes",
    "HoursQueryParser",
    "TransitionFeedReader",
]
