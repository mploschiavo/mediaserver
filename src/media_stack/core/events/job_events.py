"""Typed domain events for the Jobs framework.

Two events cover the full job lifecycle:

  * ``JobStarted`` — emitted by ``record_run_start`` the moment a run
    record is persisted with ``status=running``.
  * ``JobCompleted`` — emitted by ``record_run_complete`` once the run
    settles into any terminal status (ok / skipped / error / cancelled
    / timeout). The ``status`` field carries the terminal value rather
    than splitting into one event per outcome — downstream consumers
    that care about subset membership filter on the field.

Both events ride the shared ``EventBus`` defined in ``core/events/bus``
so SSE forwarders, audit-log archivers, and metrics counters can
subscribe without coupling to ``run_history`` directly. The SSE
forwarder at ``api/services/events_sse`` maps the ``job.*`` event-type
prefix to the operator-facing ``jobs`` topic exposed at
``GET /api/events?topics=jobs``.

Why two events instead of one ``JobStateChanged``? The ``started``
payload doesn't yet have ``elapsed`` / ``error`` / ``status``; carrying
optional fields on a single class would force every consumer to
defensively check ``if event.elapsed is not None`` for the started
case. Two classes keep each payload's required-field contract clean.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from media_stack.core.events.bus import Event


@dataclass(frozen=True, kw_only=True)
class JobStarted(Event):
    """A job run was just persisted with ``status=running``.

    All ID/name fields come from the ``RunRecord`` the caller already
    built; the event is a thin projection of that record's wire shape
    so the SSE forwarder doesn't have to re-import the domain class
    just to serialise it.
    """

    EVENT_TYPE: ClassVar[str] = "job.started"

    run_id: str
    job_name: str
    parent_run_id: str = ""
    batch_id: str = ""
    triggered_by: str = "unknown"
    actor: str = ""


@dataclass(frozen=True, kw_only=True)
class JobCompleted(Event):
    """A job run reached a terminal status.

    ``status`` is one of ``"ok"``, ``"skipped"``, ``"error"``,
    ``"cancelled"``, ``"timeout"``. Plain ``str`` rather than ``Enum``
    so the wire contract is the JSON-friendly value directly; the
    legal set is documented here and enforced at the
    ``record_run_complete`` boundary.

    ``elapsed`` is seconds (float). ``error`` is empty for non-error
    terminations; downstream consumers should treat ``""`` and absent
    as equivalent.
    """

    EVENT_TYPE: ClassVar[str] = "job.completed"

    run_id: str
    job_name: str
    status: str
    elapsed: float
    error: str = ""
