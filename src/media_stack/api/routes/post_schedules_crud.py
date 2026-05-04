"""Schedules CRUD POST routes (ADR-0007 Phase 2 wave 8 group 2).

Migrates the five state-changing schedule endpoints off the
``handlers_post.handle()`` elif chain onto the OpenAPI Router. The
existing ``routes/schedules.py`` already owns the GET; this module
owns the POSTs to keep the wave-6 file-naming pattern (one POST
module, one GET module per domain) consistent.

Routes:

* ``POST /api/schedules``                          — create.
* ``POST /api/schedules/{schedule_id}/delete``     — remove.
* ``POST /api/schedules/{schedule_id}/pause``      — set ``enabled=False``.
* ``POST /api/schedules/{schedule_id}/resume``     — set ``enabled=True``.
* ``POST /api/schedules/{schedule_id}/update``     — patch fields.

OO discipline (ADR-0007 + project-wide rule):

* ``SchedulesPostRoutes`` is a ``RouteModule`` subclass with
  instance methods only — no ``@staticmethod``, no loose top-level
  handler functions.
* Constructor-injects ``ScheduleRepository`` + ``ScheduleIdResolver``
  + ``ScheduleUpdateRequestParser`` + ``PostMutationGate`` with
  module-default fall-throughs that preserve the Router's zero-arg
  auto-discovery. Tests pass stubs to swap behaviour without
  monkey-patching.

Anti-pattern guard rails (mirrors wave 6/7):

* No lazy-cache resolver shape — every adapter caches ONLY a
  constructor-injected callable. The default path does a fresh
  module attribute lookup per call so ``mock.patch`` of the
  canonical symbol takes effect.

Security preservation:

* CSRF gate is invoked at the top of every handler method; tests
  can pass a permissive stub to exercise business logic in
  isolation. The legacy chain enforced this in
  ``_global_preflight``; routes migrated to the Router bypass that
  gate so ``PostMutationGate`` re-applies it here.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Callable

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routing import RouteModule, post


# ---------------------------------------------------------------------------
# Adapter / Strategy collaborators
# ---------------------------------------------------------------------------


class ScheduleRepository:
    """Repository onto ``scheduler.add_schedule`` /
    ``remove_schedule`` / ``set_schedule_enabled`` /
    ``update_schedule``.

    Each adapter caches ONLY the constructor-injected callable;
    the default path does a fresh module attribute lookup per
    call so ``mock.patch`` of the canonical symbol takes effect
    (avoids the lazy-cache resolver shape from earlier waves).
    """

    def __init__(
        self,
        add_fn: Callable[..., dict[str, Any]] | None = None,
        remove_fn: Callable[[int], dict[str, Any]] | None = None,
        set_enabled_fn: Callable[..., dict[str, Any]] | None = None,
        update_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._add = add_fn
        self._remove = remove_fn
        self._set_enabled = set_enabled_fn
        self._update = update_fn

    def add(
        self,
        action: str,
        interval_seconds: int,
        label: str,
        *,
        enabled: bool,
    ) -> dict[str, Any]:
        if self._add is not None:
            return self._add(action, interval_seconds, label, enabled)
        from media_stack.api.services import scheduler as sched_svc
        return sched_svc.add_schedule(
            action, interval_seconds, label, enabled,
        )

    def remove(self, schedule_id: int) -> dict[str, Any]:
        if self._remove is not None:
            return self._remove(schedule_id)
        from media_stack.api.services import scheduler as sched_svc
        return sched_svc.remove_schedule(schedule_id)

    def set_enabled(
        self, schedule_id: int, *, enabled: bool,
    ) -> dict[str, Any]:
        if self._set_enabled is not None:
            return self._set_enabled(schedule_id, enabled=enabled)
        from media_stack.api.services import scheduler as sched_svc
        return sched_svc.set_schedule_enabled(
            schedule_id, enabled=enabled,
        )

    def update(
        self, schedule_id: int, **kwargs: Any,
    ) -> dict[str, Any]:
        if self._update is not None:
            return self._update(schedule_id, **kwargs)
        from media_stack.api.services import scheduler as sched_svc
        return sched_svc.update_schedule(schedule_id, **kwargs)


class ScheduleIdResolver:
    """Strategy that parses + validates the integer ``schedule_id``
    path param.

    Returns ``(int_value, None)`` on success or
    ``(None, error_body)`` on a non-integer input. Owns the
    400-on-non-int branch so the route bodies stay one-liners.
    """

    def parse(
        self, raw: Any,
    ) -> tuple[int | None, dict[str, Any] | None]:
        try:
            return int(raw), None
        except (TypeError, ValueError):
            return None, {"error": "Invalid schedule ID"}


class ScheduleUpdateRequestParser:
    """Strategy that lifts ``action`` / ``interval_seconds`` /
    ``label`` / ``enabled`` off a JSON body into a kwargs dict
    suitable for ``ScheduleRepository.update``.

    Mirrors the legacy chain's "only forward keys the body
    actually supplied" semantic so a partial request doesn't
    accidentally null out the unmentioned field.
    """

    def build(self, body: dict[str, Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if "action" in body:
            kwargs["action"] = str(body["action"])
        if "interval_seconds" in body:
            try:
                kwargs["interval_seconds"] = int(body["interval_seconds"])
            except (TypeError, ValueError):
                # Forward verbatim — service layer validates +
                # returns the canonical error envelope.
                kwargs["interval_seconds"] = body["interval_seconds"]
        if "label" in body:
            kwargs["label"] = str(body["label"])
        if "enabled" in body:
            kwargs["enabled"] = bool(body["enabled"])
        return kwargs


# ---------------------------------------------------------------------------
# RouteModule
# ---------------------------------------------------------------------------


class SchedulesPostRoutes(RouteModule):
    """Schedules POST routes covering create / delete / pause /
    resume / update.

    The Router auto-discovers + instantiates this class + walks
    its tagged methods at startup. Constructor defaults keep
    auto-discovery zero-arg while letting tests swap any
    collaborator.
    """

    def __init__(
        self,
        *,
        mutation_gate: PostMutationGate | None = None,
        repository: ScheduleRepository | None = None,
        id_resolver: ScheduleIdResolver | None = None,
        update_parser: ScheduleUpdateRequestParser | None = None,
    ) -> None:
        self._gate = mutation_gate or PostMutationGate()
        self._repo = repository or ScheduleRepository()
        self._id_resolver = id_resolver or ScheduleIdResolver()
        self._update_parser = (
            update_parser or ScheduleUpdateRequestParser()
        )

    # --- gate helper ---------------------------------------------------

    def _gated(self, handler: Any) -> bool:
        """Run the CSRF gate; emit 403 + return False on rejection."""
        if not self._gate.verify(handler):
            self._gate.reject(handler)
            return False
        return True

    def _resolve_id(
        self, handler: Any, raw: str,
    ) -> int | None:
        parsed_id, error = self._id_resolver.parse(raw)
        if error is not None:
            handler._json_response(HTTPStatus.BAD_REQUEST, error)
            return None
        return parsed_id

    # --- routes --------------------------------------------------------

    @post("/api/schedules")
    def handle_create(self, handler: Any) -> None:
        """Create a new recurring schedule.

        Body: ``{action: str, interval_seconds: int, label?: str,
        enabled?: bool}``. Validation flows through the service
        layer's error envelope.
        """
        if not self._gated(handler):
            return
        body = handler._read_json_body() or {}
        result = self._repo.add(
            str(body.get("action", "")),
            int(body.get("interval_seconds", 0) or 0),
            str(body.get("label", "") or ""),
            enabled=bool(body.get("enabled", True)),
        )
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/schedules/{schedule_id}/delete")
    def handle_delete(
        self, handler: Any, *, schedule_id: str,
    ) -> None:
        """Remove a schedule by ID."""
        if not self._gated(handler):
            return
        parsed_id = self._resolve_id(handler, schedule_id)
        if parsed_id is None:
            return
        handler._json_response(
            HTTPStatus.OK, self._repo.remove(parsed_id),
        )

    @post("/api/schedules/{schedule_id}/pause")
    def handle_pause(
        self, handler: Any, *, schedule_id: str,
    ) -> None:
        """Pause (``enabled=False``) a schedule by ID."""
        if not self._gated(handler):
            return
        parsed_id = self._resolve_id(handler, schedule_id)
        if parsed_id is None:
            return
        handler._json_response(
            HTTPStatus.OK,
            self._repo.set_enabled(parsed_id, enabled=False),
        )

    @post("/api/schedules/{schedule_id}/resume")
    def handle_resume(
        self, handler: Any, *, schedule_id: str,
    ) -> None:
        """Resume (``enabled=True``) a schedule by ID."""
        if not self._gated(handler):
            return
        parsed_id = self._resolve_id(handler, schedule_id)
        if parsed_id is None:
            return
        handler._json_response(
            HTTPStatus.OK,
            self._repo.set_enabled(parsed_id, enabled=True),
        )

    @post("/api/schedules/{schedule_id}/update")
    def handle_update(
        self, handler: Any, *, schedule_id: str,
    ) -> None:
        """Patch one or more fields on a schedule.

        Body keys: ``action``, ``interval_seconds``, ``label``,
        ``enabled``. Only keys actually supplied are forwarded;
        omitted keys leave the persisted value alone.
        """
        if not self._gated(handler):
            return
        parsed_id = self._resolve_id(handler, schedule_id)
        if parsed_id is None:
            return
        body = handler._read_json_body() or {}
        kwargs = self._update_parser.build(body)
        handler._json_response(
            HTTPStatus.OK, self._repo.update(parsed_id, **kwargs),
        )


__all__ = [
    "ScheduleIdResolver",
    "ScheduleRepository",
    "ScheduleUpdateRequestParser",
    "SchedulesPostRoutes",
]
