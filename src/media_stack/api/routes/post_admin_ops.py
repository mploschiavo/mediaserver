"""Admin/ops POST routes (ADR-0007 Phase 2 wave 5).

Migrates the thirteen state-changing administration + operations
endpoints off the ``handlers_post.handle()`` elif chain onto the
OpenAPI Router. These routes share a common shape: a small JSON
body, an admin-only mutation against a service collaborator, and
JSON status response.

Routes:

* ``POST /api/stack/upgrade``          — gated in-place compose upgrade.
* ``POST /api/restart/{service}``      — restart a single service container.
* ``POST /api/batch-restart``          — restart multiple services.
* ``POST /api/restore``                — restore configs from a backup blob.
* ``POST /api/snapshot``               — capture a config snapshot now.
* ``POST /api/log-level``              — change the runtime log level.
* ``POST /api/gpu/enable``             — auto-configure Jellyfin GPU.
* ``POST /api/auto-heal/run``          — trigger an auto-heal pass.
* ``POST /api/auto-heal/enabled``      — toggle the auto-heal cycle.
* ``POST /api/guardrails/config``      — set evaluation cadence.
* ``POST /api/guardrails``             — bulk-update guardrail settings.
* ``POST /api/guardrails/{id}``        — operator threshold override.
* ``POST /api/guardrails/{id}/test``   — dry-run a single rule.
* ``POST /api/guardrails/{id}/disable``— soft-disable / re-enable a rule.
* ``POST /api/media-server/reset``     — DB-level Jellyfin admin reset.
* ``POST /api/lifecycle-ensurers/{service}/{method}`` — ADR-0005
  Phase 5b: manually dispatch a single lifecycle ensurer (operator
  "Run now" + auto-heal converge on the same dispatch path).

The OpenAPI spec already declares each path; ``openapi.yaml``'s
guardrail bucket gained ``/api/guardrails/config`` in this wave so
the Router's startup spec-drift check passes.

OO discipline (ADR-0007 + project-wide rule):

* ``AdminOpsPostRoutes`` is a ``RouteModule`` subclass with
  instance methods only — no ``@staticmethod``, no loose
  top-level handler functions.
* Constructor-injects every collaborator with module-default
  fall-backs that preserve the Router's zero-arg auto-discovery.
  Tests pass stubs to swap behaviour without monkey-patching.
* Five named patterns isolate the concerns inlined into the
  legacy elif chain:

  * ``StackUpgrader`` — Adapter onto ``stack_update.start_upgrade``.
  * ``RestartService`` — Adapter onto ``admin_svc.restart_service``
    + ``admin_svc.batch_restart``. Owns the SERVICE_MAP guard so
    a typo'd service name returns 400 here, not deep inside the
    legacy chain.
  * ``SnapshotService`` — Adapter onto ``ops_svc.take_snapshot``.
  * ``LogLevelService`` — Strategy that validates the level enum +
    persists the new value to ``handler.state``.
  * ``GpuController`` — Adapter onto ``ops_svc.enable_gpu_transcoding``.
  * ``AutoHealController`` — Adapter onto ``auto_heal.run_cycle`` +
    ``auto_heal.set_enabled``.
  * ``GuardrailsService`` — Adapter onto the cross-domain guardrails
    registry covering threshold update, dry-run test, and disable.
  * ``MediaServerResetService`` — Adapter onto
    ``admin_svc.jellyfin_hard_reset`` with the env-default fallback
    pulled out of the route body.
  * ``RestoreService`` — Adapter onto ``config_svc.restore_backup``.
  * ``GuardrailsCadenceService`` — Strategy that validates +
    persists the evaluation-interval override.
  * ``BulkGuardrailsService`` — Adapter onto
    ``disk_svc.update_guardrails`` for the disk-style bulk update.
* ``except Exception`` is narrow per the project rule — only the
  one branch that lifted ``except Exception`` from the legacy
  chain (``MediaServerResetService.reset``'s service-registry
  lookup) keeps the swallow semantic, with ``log_swallowed``.

Anti-pattern guard rails (ADR-0007 wave-3+4 retros):

* No lazy-cache resolver shape — every adapter caches ONLY a
  constructor-injected callable. The default path does a fresh
  attribute lookup on the service module each call so
  ``mock.patch`` on the canonical symbol takes effect.
* No ratchet baseline bumps. Every collaborator default keeps the
  legacy class structure intact.

Security preservation (project memory bug-class:
``csrf_double_submit``):

* CSRF is enforced at server.py for every POST that flows through
  the legacy chain (``_global_preflight``). Routes that fall under
  the Router skip that gate, so this module installs its own
  ``PostMutationGate`` Strategy that wraps the same
  ``CsrfProtector.verify`` call. The gate is invoked at the top
  of every handler method; tests can pass a permissive stub to
  exercise business logic in isolation.
* Admin-only authz still flows through server.py's
  ``_controller_rbac.allows`` + ``_sudo_gate.allows`` checks,
  which run BEFORE the dispatcher. Audit-log writes stay on
  server.py's ``_audit_mutation`` post-dispatch hook (it fires
  on every HANDLED outcome).
"""

from __future__ import annotations

import os
from http import HTTPStatus
from typing import Any, Callable

from media_stack.api.routing import RouteModule, post
from media_stack.api.services.lifecycle_ensurer_invoker import (
    LifecycleEnsurerInvoker,
    SOURCE_OPERATOR,
)
from media_stack.core.auth.csrf import CsrfProtector
from media_stack.core.logging_utils import log_swallowed


# ---------------------------------------------------------------------------
# Constants — each value belongs to a named source-of-truth so
# string/numeric ratchets see one canonical site instead of inline
# magic strings scattered through the route bodies.
# ---------------------------------------------------------------------------

# Log levels the runtime accepts. Same enum the legacy elif body
# enforced; lifted here so the validation message can list the
# allowed values in one place.
_VALID_LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARN", "ERROR")

# Floors / ceilings on the operator-editable guardrail evaluation
# interval. 30s minimum so an operator can't ddos themselves;
# 86400s (24h) maximum so an alert-fatigued operator can quiet
# the loop down to once a day. Same bounds the legacy chain enforced.
_GUARDRAIL_INTERVAL_FLOOR = 30
_GUARDRAIL_INTERVAL_CEIL = 86400
_GUARDRAIL_INTERVAL_DEFAULT = 300
_GUARDRAIL_INTERVAL_ENV_VAR = "MEDIA_STACK_GUARDRAIL_INTERVAL_SECONDS"

# Persisted-state key the legacy LogLevel handler wrote so the
# new level survives controller restarts. Keep verbatim — tests
# pin the wire shape, and downstream readers key on this name.
_LOG_LEVEL_PERSIST_KEY = "_log_level"

# Default admin username/password fallbacks used by the legacy
# media-server reset handler when the request body omits them.
# Pulled out as constants so the env-var lookup has one named site.
_MS_RESET_USERNAME_ENV = "STACK_ADMIN_USERNAME"
_MS_RESET_PASSWORD_ENV = "STACK_ADMIN_PASSWORD"
_MS_RESET_DEFAULT_USERNAME = "admin"
_MS_RESET_DEFAULT_PASSWORD = "media-stack"
_MS_RESET_MIN_PASSWORD_LEN = 4

# Guardrail-subpath actions matched off the spec'd
# ``/api/guardrails/{id}/{action}`` family. ``""`` means "bare id"
# (threshold update) — kept in the same Strategy so all four
# branches share the rule-id lookup + 404-on-unknown semantics.
_GUARDRAIL_SUBPATH_TEST = "test"
_GUARDRAIL_SUBPATH_DISABLE = "disable"


# ---------------------------------------------------------------------------
# Mutation gate (CSRF + future hooks)
# ---------------------------------------------------------------------------


class PostMutationGate:
    """CSRF double-submit + user-mgmt rate-limit gate for
    router-dispatched POST routes.

    The legacy chain ran ``_global_preflight`` before any handler
    body -- that gate verifies the ``X-CSRF-Token`` header echoes
    the ``media_stack_csrf`` cookie AND enforces the per-IP
    user-mgmt rate-limit bucket. Routes migrated to the OpenAPI
    Router bypass that gate (the dispatcher returns HANDLED before
    the legacy chain runs), so the gate has to be re-applied here
    for every mutation.

    Constructor-injects ``CsrfProtector`` so a test can pass a
    permissive stub via ``PostMutationGate(csrf=_AlwaysAllow())``
    to exercise business logic without forging tokens. The default
    path constructs a fresh ``CsrfProtector()`` per gate instance,
    matching the singleton the legacy chain holds module-globally.

    The rate-limit bucket defaults to OFF (``rate_limit=False``)
    because most routes use the global ``_global_post_limiter`` in
    server.py. Routes that need the tighter user-mgmt bucket (the
    bans, sessions, users domains) construct
    ``PostMutationGate(rate_limit=True)``.
    """

    def __init__(
        self,
        csrf: CsrfProtector | None = None,
        *,
        enforce_env_var: str = "CSRF_ENFORCE",
        rate_limit: bool = False,
    ) -> None:
        self._csrf = csrf or CsrfProtector()
        self._enforce_env = enforce_env_var
        self._rate_limit = rate_limit
        self._rate_limit_failed = False

    def verify(self, handler: Any) -> bool:
        """Return True iff the request passes CSRF + rate-limit."""
        self._rate_limit_failed = False
        if not self._verify_csrf(handler):
            return False
        if self._rate_limit and not self._verify_rate_limit(handler):
            self._rate_limit_failed = True
            return False
        return True

    def _verify_csrf(self, handler: Any) -> bool:
        """Mirrors ``handlers_post._check_csrf``: requests without a
        Cookie header are API clients (basic auth from a script)
        and exempt unless ``CSRF_ENFORCE=1`` forces strict mode.
        Browser requests (Cookie present) must echo the token.
        """
        mode = (os.getenv(self._enforce_env, "") or "").strip()
        if mode == "0":
            return True
        headers = getattr(handler, "headers", None)
        if headers is None:
            return True
        try:
            cookie_header = headers.get("Cookie", "") or ""
            csrf_header = headers.get(self._csrf.header_name, "") or ""
        except AttributeError:
            return True
        has_cookie = bool(cookie_header.strip())
        if not (mode == "1" or has_cookie):
            return True
        return self._csrf.verify(
            cookie_header=cookie_header, header_value=csrf_header,
        )

    def _verify_rate_limit(self, handler: Any) -> bool:
        """Per-IP user-mgmt bucket check. Reset-password gets a
        tighter per-account bucket on top of the IP bucket."""
        from media_stack.api.services.rate_limiters import (
            _user_mgmt_limiter,
            _pw_reset_limiter,
        )
        from media_stack.api.session_singletons import (
            trusted_proxy_auth as _trusted_proxy_auth,
        )
        try:
            client_id = (
                _trusted_proxy_auth.client_ip(handler) or "-"
            )
        except Exception:  # noqa: BLE001
            client_id = "-"
        if not _user_mgmt_limiter.allow(
            client_id=client_id, bucket="user-mgmt",
        ):
            return False
        # Reset-password gets a separate, tighter per-ACCOUNT bucket so
        # an attacker rotating IPs still trips the throttle on the
        # target user_id.
        path = getattr(handler, "path", "") or ""
        parts = path.split("/")
        if len(parts) >= 5 and parts[4] == "reset-password":
            target_uid = parts[3]
            if not _pw_reset_limiter.allow(
                client_id=target_uid, bucket="pw-reset",
            ):
                return False
        return True

    def reject(self, handler: Any) -> None:
        """Emit the matching error body for whichever sub-gate
        rejected the request."""
        if self._rate_limit_failed:
            handler._json_response(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": "rate limit exceeded; slow down"},
            )
            return
        handler._json_response(
            HTTPStatus.FORBIDDEN,
            {"error": "CSRF token missing or invalid"},
        )


# ---------------------------------------------------------------------------
# Adapter / Strategy collaborators
# ---------------------------------------------------------------------------


class StackUpgrader:
    """Adapter onto ``stack_update.start_upgrade``.

    The legacy chain pulled the optional ``target`` field off the
    JSON body and forwarded it to the service. Same here.
    Constructor-injects the start function for testability; the
    default does a fresh module attribute lookup per call so
    ``mock.patch`` of the canonical symbol takes effect.
    """

    def __init__(self, start_fn: Callable[[Any], dict[str, Any]] | None = None) -> None:
        self._start = start_fn

    def start(self, target: Any) -> dict[str, Any]:
        if self._start is not None:
            return self._start(target)
        from media_stack.api.services import stack_update as su_svc
        return su_svc.start_upgrade(target)


class RestartService:
    """Adapter onto ``admin_svc.restart_service`` + ``batch_restart``.

    Owns the SERVICE_MAP guard so a typo'd service name returns
    400 here, not deep inside the legacy chain. ``controller`` is
    accepted as a valid target even though it's not in the
    SERVICE_MAP — same special case the legacy code carved out.
    """

    _CONTROLLER_TARGET = "controller"

    def __init__(
        self,
        restart_fn: Callable[[str], dict[str, Any]] | None = None,
        batch_fn: Callable[[list[str]], dict[str, Any]] | None = None,
        service_map_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._restart = restart_fn
        self._batch = batch_fn
        self._service_map_provider = service_map_provider

    def restart_one(self, service: str) -> tuple[int, dict[str, Any]]:
        service_map = self._resolve_service_map()
        if service not in service_map and service != self._CONTROLLER_TARGET:
            return (
                HTTPStatus.BAD_REQUEST,
                {
                    "error": f"Unknown service '{service}'",
                    "known": sorted(service_map.keys()),
                },
            )
        if self._restart is not None:
            return HTTPStatus.OK, self._restart(service)
        from media_stack.api.services import admin as admin_svc
        return HTTPStatus.OK, admin_svc.restart_service(service)

    def restart_many(self, services: list[str]) -> dict[str, Any]:
        if self._batch is not None:
            return self._batch(services)
        from media_stack.api.services import admin as admin_svc
        return admin_svc.batch_restart(services)

    def _resolve_service_map(self) -> dict[str, Any]:
        if self._service_map_provider is not None:
            return self._service_map_provider()
        from media_stack.api.services.registry import SERVICE_MAP
        return SERVICE_MAP


class SnapshotService:
    """Adapter onto ``ops_svc.take_snapshot``."""

    def __init__(
        self,
        take_fn: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._take = take_fn

    def capture(self) -> dict[str, Any]:
        if self._take is not None:
            return self._take()
        from media_stack.api.services import ops as ops_svc
        return ops_svc.take_snapshot()


class GpuController:
    """Adapter onto ``ops_svc.enable_gpu_transcoding``."""

    def __init__(
        self,
        enable_fn: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._enable = enable_fn

    def enable(self) -> dict[str, Any]:
        if self._enable is not None:
            return self._enable()
        from media_stack.api.services import ops as ops_svc
        return ops_svc.enable_gpu_transcoding()


class LogLevelService:
    """Strategy that validates the level enum + persists the new
    value to the controller state.

    Same shape the legacy chain enforced: upper-cased level lookup
    against the four-element enum, optional log emission, persist
    via ``state.update_config``. Constructor-injects the runtime
    setter / log writer / state mutator so tests don't have to
    monkey-patch the platform module.
    """

    def __init__(
        self,
        *,
        set_level_fn: Callable[[str], str] | None = None,
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        self._set_level = set_level_fn
        self._log = log_fn

    def set(
        self, raw_level: str, state: Any,
    ) -> tuple[int, dict[str, Any]]:
        level = (raw_level or "").upper()
        if level not in _VALID_LOG_LEVELS:
            return HTTPStatus.BAD_REQUEST, {
                "error": f"Invalid log level '{level}'",
                "valid": list(_VALID_LOG_LEVELS),
            }
        new_level = self._invoke_set_level(level)
        self._invoke_log(f"[INFO] Log level changed to {new_level}")
        state.update_config({_LOG_LEVEL_PERSIST_KEY: new_level})
        return HTTPStatus.OK, {"level": new_level}

    def _invoke_set_level(self, level: str) -> str:
        if self._set_level is not None:
            return self._set_level(level)
        from media_stack.services.runtime_platform import set_log_level
        return set_log_level(level)

    def _invoke_log(self, message: str) -> None:
        if self._log is not None:
            self._log(message)
            return
        from media_stack.services.runtime_platform import log
        log(message)


class AutoHealController:
    """Adapter onto ``auto_heal.run_cycle`` + ``auto_heal.set_enabled``."""

    def __init__(
        self,
        run_fn: Callable[[], dict[str, Any]] | None = None,
        set_enabled_fn: Callable[[bool], dict[str, Any]] | None = None,
    ) -> None:
        self._run = run_fn
        self._set_enabled = set_enabled_fn

    def run(self) -> dict[str, Any]:
        if self._run is not None:
            return self._run()
        from media_stack.api.services import auto_heal as autoheal_svc
        return autoheal_svc.run_cycle()

    def set_enabled(self, value: bool) -> dict[str, Any]:
        if self._set_enabled is not None:
            return self._set_enabled(value)
        from media_stack.api.services import auto_heal as autoheal_svc
        return autoheal_svc.set_enabled(value)


class GuardrailsCadenceService:
    """Strategy that validates + persists the evaluation-interval
    override for the cross-domain guardrails loop.

    Same floor / ceiling the legacy chain enforced. Persists to an
    env var that ``tick()`` reads each loop. Constructor-injects
    the env-mutation hook so tests don't have to touch
    ``os.environ``.
    """

    def __init__(
        self,
        env_setter: Callable[[str, str], None] | None = None,
    ) -> None:
        self._env_setter = env_setter

    def update(
        self, raw_value: Any,
    ) -> tuple[int, dict[str, Any]]:
        try:
            interval = int(raw_value if raw_value is not None else _GUARDRAIL_INTERVAL_DEFAULT)
        except (TypeError, ValueError):
            return HTTPStatus.BAD_REQUEST, {
                "error": "evaluation_interval_seconds must be an integer",
            }
        if interval < _GUARDRAIL_INTERVAL_FLOOR or interval > _GUARDRAIL_INTERVAL_CEIL:
            return HTTPStatus.BAD_REQUEST, {
                "error": (
                    "evaluation_interval_seconds must be in "
                    f"[{_GUARDRAIL_INTERVAL_FLOOR}, {_GUARDRAIL_INTERVAL_CEIL}]"
                ),
            }
        self._persist(interval)
        return HTTPStatus.OK, {"evaluation_interval_seconds": interval}

    def _persist(self, interval: int) -> None:
        if self._env_setter is not None:
            self._env_setter(_GUARDRAIL_INTERVAL_ENV_VAR, str(interval))
            return
        os.environ[_GUARDRAIL_INTERVAL_ENV_VAR] = str(interval)


class BulkGuardrailsService:
    """Adapter onto ``disk_svc.update_guardrails`` for the disk-style
    bulk update flowing through ``POST /api/guardrails``.

    Distinct from ``GuardrailsService`` (per-rule operator overrides
    on the cross-domain registry). Lifted as its own collaborator
    because the legacy elif body uses a different service module
    (``disk_svc``, not the guardrails registry).
    """

    def __init__(
        self,
        update_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self._update = update_fn

    def update(self, body: dict[str, Any]) -> dict[str, Any]:
        if self._update is not None:
            return self._update(body)
        from media_stack.api.services import disk as disk_svc
        return disk_svc.update_guardrails(body)


class GuardrailsService:
    """Adapter onto the cross-domain guardrails registry covering
    threshold update, dry-run test, and disable toggle.

    Constructor-injects the registry handle + state-collector so
    tests don't have to wire up the real domain modules. The
    default path does a fresh ``default()`` call per request so
    a test can ``reset_default()`` between cases without picking
    up a stale singleton.
    """

    def __init__(
        self,
        registry_provider: Callable[[], Any] | None = None,
        state_collector_fn: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._registry_provider = registry_provider
        self._state_collector = state_collector_fn

    def update_threshold(
        self, rule_id: str, body: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        registry = self._resolve_registry()
        if registry.get(rule_id) is None:
            return HTTPStatus.NOT_FOUND, {
                "error": f"unknown guardrail: {rule_id}",
            }
        threshold = body.get("threshold")
        if not isinstance(threshold, dict):
            return HTTPStatus.BAD_REQUEST, {
                "error": "body must include 'threshold' object",
            }
        return HTTPStatus.OK, registry.update_threshold(rule_id, threshold)

    def test(
        self, rule_id: str,
    ) -> tuple[int, dict[str, Any]]:
        registry = self._resolve_registry()
        if registry.get(rule_id) is None:
            return HTTPStatus.NOT_FOUND, {
                "error": f"unknown guardrail: {rule_id}",
            }
        snapshot = self._collect_state()
        snapshot[f"_threshold:{rule_id}"] = registry.threshold_for(rule_id)
        trigger = registry.evaluate_one(rule_id, snapshot)
        if trigger is None:
            return HTTPStatus.OK, {
                "would_trigger": False,
                "severity": None,
                "current_value": None,
                "threshold": registry.threshold_for(rule_id),
            }
        return HTTPStatus.OK, {
            "would_trigger": True,
            "severity": trigger.severity,
            "current_value": trigger.current_value,
            "threshold": trigger.threshold,
            "description": trigger.description,
        }

    def set_disabled(
        self, rule_id: str, body: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        registry = self._resolve_registry()
        if registry.get(rule_id) is None:
            return HTTPStatus.NOT_FOUND, {
                "error": f"unknown guardrail: {rule_id}",
            }
        disabled = bool(body.get("disabled", True))
        return HTTPStatus.OK, registry.set_disabled(rule_id, disabled)

    def _resolve_registry(self) -> Any:
        if self._registry_provider is not None:
            return self._registry_provider()
        from media_stack.services import guardrails as _guardrails_pkg
        return _guardrails_pkg.default()

    def _collect_state(self) -> dict[str, Any]:
        if self._state_collector is not None:
            return self._state_collector()
        from media_stack.services.guardrails.state_collector import (
            collect_state,
        )
        return collect_state()


class RestoreService:
    """Adapter onto ``config_svc.restore_backup``."""

    def __init__(
        self,
        restore_fn: Callable[[dict[str, Any], Any], dict[str, Any]] | None = None,
    ) -> None:
        self._restore = restore_fn

    def restore(
        self, body: dict[str, Any], state: Any,
    ) -> dict[str, Any]:
        if self._restore is not None:
            return self._restore(body, state)
        from media_stack.api.services import config as config_svc
        return config_svc.restore_backup(body, state)


class MediaServerResetService:
    """Adapter onto ``admin_svc.jellyfin_hard_reset``.

    Pulls the env-var fallbacks out of the route body so the
    handler stays a one-liner. The ``except Exception`` swallow on
    the registry lookup mirrors the legacy chain's behaviour
    (``admin_svc.is_media_server_reset_path`` only ever fires for
    legacy ``/api/<media-server-id>/reset`` aliases — but those
    aliases are NOT migrated by this wave; the canonical
    ``/api/media-server/reset`` is the only path the spec declares).
    """

    def __init__(
        self,
        reset_fn: Callable[[str, str], dict[str, Any]] | None = None,
        env_provider: Callable[[str], str] | None = None,
    ) -> None:
        self._reset = reset_fn
        self._env_provider = env_provider or os.environ.get

    def reset(
        self, body: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        username = body.get("username") or self._env_provider(
            _MS_RESET_USERNAME_ENV, _MS_RESET_DEFAULT_USERNAME,
        ) or _MS_RESET_DEFAULT_USERNAME
        password = body.get("password") or self._env_provider(
            _MS_RESET_PASSWORD_ENV, _MS_RESET_DEFAULT_PASSWORD,
        ) or _MS_RESET_DEFAULT_PASSWORD
        if not password or len(password) < _MS_RESET_MIN_PASSWORD_LEN:
            return HTTPStatus.BAD_REQUEST, {
                "error": (
                    f"password required (min {_MS_RESET_MIN_PASSWORD_LEN} chars)"
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
                "media-server reset hard-reset adapter not available",
                exc,
            )
            return HTTPStatus.INTERNAL_SERVER_ERROR, {
                "error": "media-server reset adapter not available",
            }


# ---------------------------------------------------------------------------
# RouteModule
# ---------------------------------------------------------------------------


class AdminOpsPostRoutes(RouteModule):
    """Admin/ops POST routes covering stack upgrade, restart,
    snapshot, log-level, GPU enablement, auto-heal, guardrails,
    restore, and media-server reset.

    The Router auto-discovers + instantiates this class + walks
    its tagged methods at startup. Constructor defaults keep
    auto-discovery zero-arg while letting tests swap any
    collaborator.
    """

    def __init__(
        self,
        *,
        mutation_gate: PostMutationGate | None = None,
        stack_upgrader: StackUpgrader | None = None,
        restart_service: RestartService | None = None,
        snapshot_service: SnapshotService | None = None,
        log_level_service: LogLevelService | None = None,
        gpu_controller: GpuController | None = None,
        auto_heal_controller: AutoHealController | None = None,
        guardrails_cadence: GuardrailsCadenceService | None = None,
        bulk_guardrails: BulkGuardrailsService | None = None,
        guardrails_service: GuardrailsService | None = None,
        restore_service: RestoreService | None = None,
        media_server_reset: MediaServerResetService | None = None,
        lifecycle_invoker: LifecycleEnsurerInvoker | None = None,
    ) -> None:
        self._gate = mutation_gate or PostMutationGate()
        self._stack_upgrader = stack_upgrader or StackUpgrader()
        self._restart = restart_service or RestartService()
        self._snapshot = snapshot_service or SnapshotService()
        self._log_level = log_level_service or LogLevelService()
        self._gpu = gpu_controller or GpuController()
        self._auto_heal = auto_heal_controller or AutoHealController()
        self._cadence = guardrails_cadence or GuardrailsCadenceService()
        self._bulk_guardrails = bulk_guardrails or BulkGuardrailsService()
        self._guardrails = guardrails_service or GuardrailsService()
        self._restore = restore_service or RestoreService()
        self._media_server_reset = (
            media_server_reset or MediaServerResetService()
        )
        self._lifecycle_invoker = (
            lifecycle_invoker or LifecycleEnsurerInvoker()
        )

    # --- gate helper ---------------------------------------------------

    def _gated(self, handler: Any) -> bool:
        """Run the CSRF gate; emit 403 + return False on rejection."""
        if not self._gate.verify(handler):
            self._gate.reject(handler)
            return False
        return True

    # --- routes --------------------------------------------------------

    @post("/api/stack/upgrade")
    def handle_stack_upgrade(self, handler: Any) -> None:
        """Trigger an in-place stack upgrade.

        Gated behind ``STACK_UPDATE_ALLOW_INPLACE`` on the
        controller container; without that, the service returns
        ``{accepted: false, error: ...}`` with instructions instead
        of starting work. Body: ``{target?: str}``.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        result = self._stack_upgrader.start(body.get("target"))
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/restart/{service}")
    def handle_restart_service(
        self, handler: Any, *, service: str,
    ) -> None:
        """Restart a single service container or pod.

        ``service`` is the registry id (``sonarr`` /
        ``jellyfin`` / etc.) or the magic string ``controller``.
        Unknown ids return 400 with the known set so an operator
        can spot a typo in one round-trip.
        """
        if not self._gated(handler):
            return
        status, body = self._restart.restart_one(service)
        handler._json_response(status, body)

    @post("/api/batch-restart")
    def handle_batch_restart(self, handler: Any) -> None:
        """Restart multiple services in one request.

        Body: ``{services: [...]}``. Empty list returns 400.
        Per-service failures don't block siblings; the response
        carries per-id results from ``admin_svc.batch_restart``.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        services = body.get("services", [])
        if not services:
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "services list required"},
            )
            return
        result = self._restart.restart_many(services)
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/restore")
    def handle_restore(self, handler: Any) -> None:
        """Restore service configs from a backup JSON payload.

        The backup blob is the same shape ``GET /api/backup``
        emits. Path traversal is blocked at the service layer;
        only known config paths under the config root accept
        writes.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body()
        if not body or "service_configs" not in body:
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "backup JSON with service_configs required"},
            )
            return
        result = self._restore.restore(body, handler.state)
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/snapshot")
    def handle_snapshot(self, handler: Any) -> None:
        """Take a config snapshot now."""
        if not self._gated(handler):
            return
        handler._json_response(HTTPStatus.OK, self._snapshot.capture())

    @post("/api/log-level")
    def handle_log_level(self, handler: Any) -> None:
        """Change the runtime log level + persist it.

        Body: ``{level: "DEBUG" | "INFO" | "WARN" | "ERROR"}``.
        Unknown levels return 400 with the allowed set inlined.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        status, response = self._log_level.set(
            body.get("level", ""), handler.state,
        )
        handler._json_response(status, response)

    @post("/api/gpu/enable")
    def handle_gpu_enable(self, handler: Any) -> None:
        """Auto-configure Jellyfin for hardware transcoding."""
        if not self._gated(handler):
            return
        handler._json_response(HTTPStatus.OK, self._gpu.enable())

    @post("/api/auto-heal/run")
    def handle_auto_heal_run(self, handler: Any) -> None:
        """Trigger a heal cycle right now."""
        if not self._gated(handler):
            return
        handler._json_response(HTTPStatus.OK, self._auto_heal.run())

    @post("/api/auto-heal/enabled")
    def handle_auto_heal_enabled(self, handler: Any) -> None:
        """Toggle the auto-heal cycle on/off.

        Body: ``{enabled: bool}``. Defaults to ``True`` for
        parity with the legacy chain's behaviour when the body is
        empty.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        result = self._auto_heal.set_enabled(bool(body.get("enabled", True)))
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/guardrails/config")
    def handle_guardrails_config(self, handler: Any) -> None:
        """Set the cross-domain guardrails evaluation cadence.

        Body: ``{evaluation_interval_seconds: int}`` clamped to
        ``[30, 86400]``. Persists to the env var the loop reads.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        status, response = self._cadence.update(
            body.get("evaluation_interval_seconds"),
        )
        handler._json_response(status, response)

    @post("/api/guardrails")
    def handle_guardrails_bulk_update(self, handler: Any) -> None:
        """Disk-style bulk update of guardrail thresholds.

        Distinct from the per-rule operator overrides on the
        cross-domain registry — this hits ``disk_svc`` for the
        legacy storage / qBittorrent threshold bucket.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body()
        if not body:
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "JSON body required"},
            )
            return
        result = self._bulk_guardrails.update(body)
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/guardrails/{id}")
    def handle_guardrail_threshold(
        self, handler: Any, *, id: str,  # noqa: A002 — matches spec
    ) -> None:
        """Operator threshold override for a single guardrail rule.

        Body: ``{threshold: {...}}``. Unknown rule ids return 404
        with the offending id echoed; missing/malformed body
        returns 400.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        status, response = self._guardrails.update_threshold(id, body)
        handler._json_response(status, response)

    @post("/api/guardrails/{id}/test")
    def handle_guardrail_test(
        self, handler: Any, *, id: str,  # noqa: A002 — matches spec
    ) -> None:
        """Dry-run a single guardrail evaluation.

        Returns the same envelope the legacy chain produced: a
        bool ``would_trigger`` plus the rule's current threshold
        + (when firing) severity / description.
        """
        if not self._gated(handler):
            return
        status, response = self._guardrails.test(id)
        handler._json_response(status, response)

    @post("/api/guardrails/{id}/disable")
    def handle_guardrail_disable(
        self, handler: Any, *, id: str,  # noqa: A002 — matches spec
    ) -> None:
        """Soft-disable / re-enable a single guardrail rule.

        Body: ``{disabled: bool}``. Defaults to ``True`` so a bare
        request disables the rule (operator's quickest "make it
        stop" lever).
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        status, response = self._guardrails.set_disabled(id, body)
        handler._json_response(status, response)

    @post("/api/lifecycle-ensurers/{service}/{method}")
    def handle_lifecycle_ensurer_invoke(
        self, handler: Any, *, service: str, method: str,
    ) -> None:
        """Manually dispatch a single lifecycle ensurer.

        Body: ``{overrides?: dict, source?: str}`` (source defaults
        to ``"operator"`` since the operator dashboard is the
        primary caller; auto-heal passes ``"auto-heal"``).

        ADR-0005 Phase 5b: the surface that lets operator + auto-heal
        migrate off ``action_trigger("ensure-X")`` to the same
        ``dispatch_ensurer`` path the orchestrator already uses.
        Unknown ``(service, method)`` pairs return 404 with the
        offending pair echoed; outcome envelopes (success / transient
        / permanent) flow through the 200 body — see
        ``LifecycleEnsurerInvoker`` for the mapping.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        status, response = self._lifecycle_invoker.invoke(
            service, method,
            source=body.get("source", SOURCE_OPERATOR),
            overrides=body.get("overrides"),
        )
        handler._json_response(status, response)

    @post("/api/media-server/reset")
    def handle_media_server_reset(self, handler: Any) -> None:
        """Hard-reset the media server admin credentials at the DB
        level.

        Body: ``{username?: str, password?: str}``. Falls back to
        ``STACK_ADMIN_USERNAME`` / ``STACK_ADMIN_PASSWORD`` env
        vars when fields are omitted; missing/short password
        returns 400.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        status, response = self._media_server_reset.reset(body)
        handler._json_response(status, response)


__all__ = [
    "AdminOpsPostRoutes",
    "AutoHealController",
    "BulkGuardrailsService",
    "GpuController",
    "GuardrailsCadenceService",
    "GuardrailsService",
    "LogLevelService",
    "MediaServerResetService",
    "PostMutationGate",
    "RestartService",
    "RestoreService",
    "SnapshotService",
    "StackUpgrader",
]
