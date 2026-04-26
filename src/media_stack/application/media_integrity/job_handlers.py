"""Job-framework handlers for the media-integrity subsystem.

Each handler is a ``Callable[[JobContext], dict[str, Any]]`` so the
``Job`` framework in ``media_stack.services.jobs.framework`` can
invoke them through ``run_job(name, ...)``.

The handlers delegate to the singleton ``MediaIntegrityService`` held
on ``media_integrity_handlers._instance``. The service is constructed
once at controller boot by ``controller_serve`` (best-effort: a
missing API key, unreachable adapter, or unset env var is logged and
the subsystem stays disabled — handlers gracefully ``skipped`` in that
case rather than raising).

Why a separate handler module?
    Putting these here, instead of inline in ``job_framework.py``,
    keeps the framework agnostic of concrete subsystems. The framework
    discovers jobs via contract YAMLs (``contracts/services/*.yaml``);
    this module is the import target for those contract entries.

Four jobs are exposed:

- ``media-integrity:scan`` — cheap status scan that populates the
  dashboard card. Maps to ``service.status()``.
- ``media-integrity:reconcile`` — full duplicate reconcile.
- ``media-integrity:enforce-config`` — apply servarr/bazarr policy.
- ``media-integrity:resolve-review`` — operator-resolved review queue
  item. Trigger-only (no schedule); requires parameters provided via
  the JobContext (``mi_review_*`` overrides).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from media_stack.core.logging_utils import log_swallowed
from media_stack.services.jobs.framework import JobContext
from media_stack.application.media_integrity.service import (
    MediaIntegrityInProgress,
)


logger = logging.getLogger(__name__)


# Thread-local stash for resolve-review parameters set by the HTTP
# wrapper (``handlers_post._dispatch_media_integrity_via_job``) before
# it calls ``run_job``. The ``run_job`` framework constructs its own
# ``JobContext`` and we can't thread kwargs through it, so the wrapper
# stores the parameters here and the handler reads them at
# call-time. The context manager in ``handlers_post`` clears this on
# exit so a leak between requests can't poison a later run.
_REVIEW_TLS = threading.local()


def set_review_params(params: dict | None) -> None:
    """Set (or clear) the resolve-review parameters for the current
    thread. ``None`` clears."""
    if params is None:
        try:
            del _REVIEW_TLS.params
        except AttributeError as exc:
            log_swallowed(exc)
    else:
        _REVIEW_TLS.params = dict(params)


def _get_review_param(name: str, default: Any = None) -> Any:
    params = getattr(_REVIEW_TLS, "params", None) or {}
    return params.get(name, default)


def _get_service() -> Any | None:
    """Return the live MediaIntegrityService singleton, or None.

    The handler API singleton is the same instance ``controller_serve``
    populates via ``set_service`` at boot. Returning ``None`` lets the
    job framework record ``skipped`` rather than ``error`` when the
    subsystem isn't configured (e.g. missing API keys on a partial
    deployment).
    """
    # TODO(phase-16-F): application/ should not reach into api/. The
    # ``MediaIntegrityService`` singleton is currently parked on
    # ``api.services.media_integrity_handlers._instance`` and looked up
    # via a lazy import here — application → api is an inversion of the
    # hexagonal layering. Phase 16-F should move the singleton holder
    # into ``application/media_integrity`` (or wire it via DI) and have
    # the API handler read from there.
    try:
        from media_stack.api.services.media_integrity_handlers import (
            _instance as _api,
        )
    except Exception as exc:  # pragma: no cover — import-time defensive
        logger.warning("media_integrity: handler import failed: %s", exc)
        return None
    return getattr(_api, "_service", None)


def _actor_for(ctx: JobContext) -> str:
    """Pick an actor label for service-layer audit fields.

    The runner's ``source``/``actor`` flow through to job history; the
    service-layer audit fields are coarser (``"scheduler"`` vs
    ``"<username>"``). We map the JobContext default to ``"scheduler"``
    so cron-fired runs surface the same actor the legacy daemon
    emitted, keeping the on-the-wire audit shape stable.
    """
    actor = getattr(ctx, "_mi_actor", None)
    if actor:
        return str(actor)
    return "scheduler"


def media_integrity_scan(ctx: JobContext) -> dict[str, Any]:
    """Cheap status scan — populates the dashboard's last-pass card.

    No mutations: just a snapshot read. Used as the every-15-min
    heartbeat that lets the UI show "freshness" without paying for a
    full reconcile.
    """
    svc = _get_service()
    if svc is None:
        return {"skipped": "media-integrity service not configured"}
    try:
        snap = svc.status()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("media_integrity: scan failed: %s", exc)
        return {"skipped": f"scan failed: {str(exc)[:120]}"}
    # ``scan`` is the payload key — bare ``status`` would collide with
    # the framework's own status field on the return dict.
    return {"scan": snap}


def media_integrity_reconcile(ctx: JobContext) -> dict[str, Any]:
    """Full duplicate reconcile across every *arr + Bazarr."""
    svc = _get_service()
    if svc is None:
        return {"skipped": "media-integrity service not configured"}
    try:
        result = svc.reconcile(actor=_actor_for(ctx))
    except MediaIntegrityInProgress as exc:
        # A second reconcile arriving while one is in flight is
        # never an "error" — the framework's ``skipped`` bucket is
        # the right home so the history badge stays green.
        return {"skipped": f"already in progress: {exc.op}"}
    except Exception as exc:
        logger.warning("media_integrity: reconcile failed: %s", exc)
        raise
    return {"reconcile": result}


def media_integrity_enforce_config(ctx: JobContext) -> dict[str, Any]:
    """Apply canonical *arr + Bazarr policy. Idempotent."""
    svc = _get_service()
    if svc is None:
        return {"skipped": "media-integrity service not configured"}
    try:
        result = svc.enforce_config(actor=_actor_for(ctx))
    except MediaIntegrityInProgress as exc:
        return {"skipped": f"already in progress: {exc.op}"}
    except Exception as exc:
        logger.warning("media_integrity: enforce-config failed: %s", exc)
        raise
    return {"enforce": result}


def media_integrity_resolve_review(ctx: JobContext) -> dict[str, Any]:
    """Apply an operator-resolved review queue item.

    Parameters are supplied via ``set_review_params(...)`` from the
    HTTP wrapper (``handlers_post._dispatch_media_integrity_via_job``)
    immediately before ``run_job`` is invoked:

    - ``_mi_review_app``: ``"radarr"`` / ``"sonarr"`` / etc.
    - ``_mi_review_release_id``: external release id (string).
    - ``_mi_review_winner_file_id`` OR ``_mi_review_winner_sub_path``.
    - ``_mi_review_release_kind``, ``_mi_review_language``,
      ``_mi_review_forced``, ``_mi_review_hi`` (optional).

    Trigger-only — never cron-scheduled. If invoked from
    ``/actions/{name}`` without parameters (e.g. somebody POSTs
    ``/actions/media-integrity:resolve-review`` directly), returns
    ``skipped`` so the dispatcher doesn't crash.
    """
    svc = _get_service()
    if svc is None:
        return {"skipped": "media-integrity service not configured"}
    app = _get_review_param("_mi_review_app", "") or ""
    release_id = _get_review_param("_mi_review_release_id", "") or ""
    if not app or not release_id:
        return {"skipped": "resolve-review requires app + release_id parameters"}
    winner_file_id = _get_review_param("_mi_review_winner_file_id")
    winner_sub_path = _get_review_param("_mi_review_winner_sub_path")
    if winner_file_id is None and winner_sub_path is None:
        return {"skipped": "resolve-review requires a winner_file_id or winner_sub_path"}
    try:
        result = svc.resolve_review(
            str(app),
            str(release_id),
            winner_file_id=(
                str(winner_file_id) if winner_file_id is not None else None
            ),
            winner_sub_path=(
                str(winner_sub_path) if winner_sub_path is not None else None
            ),
            release_kind=_get_review_param("_mi_review_release_kind"),
            language=_get_review_param("_mi_review_language"),
            forced=bool(_get_review_param("_mi_review_forced", False)),
            hi=bool(_get_review_param("_mi_review_hi", False)),
            actor=_actor_for(ctx),
        )
    except MediaIntegrityInProgress as exc:
        return {"skipped": f"already in progress: {exc.op}"}
    except ValueError as exc:
        # Bad parameters land here (mutually exclusive winners, etc.).
        return {"skipped": f"invalid parameters: {str(exc)[:120]}"}
    except Exception as exc:
        logger.warning("media_integrity: resolve-review failed: %s", exc)
        raise
    return {"resolve_review": result}


__all__ = [
    "media_integrity_scan",
    "media_integrity_reconcile",
    "media_integrity_enforce_config",
    "media_integrity_resolve_review",
]
