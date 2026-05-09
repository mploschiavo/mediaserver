"""Media-integrity POST -> JobRunner dispatch shim.

Lifted from ``media_stack.api.handlers_post`` during ADR-0007 Phase 2
Phase E (legacy-handler retirement).

Backwards-compatible shim: the SPA still calls
``POST /api/media-integrity/{reconcile,enforce-config,resolve-review}``
but every invocation flows through ``JobRunner.run`` so the unified
``/api/jobs.history[]`` reflects the run.

The ``MediaIntegrityHandlers`` singleton (in
``media_integrity_handlers.py``) still owns the admin gate, the
idempotency cache, the 409 mapping for ``MediaIntegrityInProgress``,
and the body validation for resolve-review. The shim plugs
``run_job`` between ``dispatch_post`` and the underlying service
method so the history line is written exactly once.

ADR-0012 refactor: helpers folded into ``MediaIntegrityDispatch``;
module-level callable aliases are wired through ``sys.modules`` so
``mock.patch("...media_integrity_dispatch._foo")`` keeps working.
"""

from __future__ import annotations

import contextlib
import sys
import threading
from collections.abc import Iterator
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qsl

from .media_integrity_handlers import (
    _instance as _media_integrity_handlers,
)


_ERR_LEN = 99


_MI_PATH_TO_JOB = {
    "/api/media-integrity/reconcile": "media-integrity:reconcile",
    "/api/media-integrity/enforce-config": "media-integrity:enforce-config",
    "/api/media-integrity/resolve-review": "media-integrity:resolve-review",
}


# Thread-local carrier for resolve-review parameters. The Job framework
# constructs its own ``JobContext`` inside ``run_job``; we can't pass
# kwargs through, so we stash them on a context-managed thread local
# and have ``media_integrity_resolve_review`` read them off the active
# JobContext at handler-call time.
_MI_REVIEW_TLS = threading.local()


class MediaIntegrityDispatch:
    """Dispatch helpers for media-integrity POST endpoints.

    All members are plain instance methods so unit tests can swap the
    module-level alias via ``mock.patch`` and the dispatcher will pick
    up the patched function (resolution goes through
    ``sys.modules[__name__]``).
    """

    def parse_query_string(self, raw: str) -> dict[str, str]:
        """Minimal parser mirroring ``media_integrity_handlers._parse_query``
        -- single-value pairs, last-write wins."""
        if not raw:
            return {}
        return {k: v for k, v in parse_qsl(raw, keep_blank_values=True)}

    @contextlib.contextmanager
    def mi_review_params(self, params: dict) -> Iterator[None]:
        """Stash resolve-review parameters for the duration of one
        run_job invocation. The job handler
        (``media_stack.services.media_integrity.job_handlers``) reads them
        via the module-level helper rather than from JobContext attrs, so
        no JobContext monkey-patching is needed. Reset on exit so a leak
        between requests can't poison a later run."""
        from media_stack.core.logging_utils import log_swallowed
        from media_stack.services.media_integrity import job_handlers as _jh

        prev = getattr(_MI_REVIEW_TLS, "params", None)
        _MI_REVIEW_TLS.params = dict(params)
        _jh.set_review_params(dict(params))
        try:
            yield
        finally:
            if prev is None:
                try:
                    del _MI_REVIEW_TLS.params
                except AttributeError as exc:
                    log_swallowed(exc)
                _jh.set_review_params(None)
            else:
                _MI_REVIEW_TLS.params = prev

    def run_mi_job_and_extract(
        self,
        run_job_fn: Any,
        job_name: str,
        actor_label: str,
        payload_key: str,
    ) -> Any:
        """Invoke a media-integrity job via run_job and return the raw
        service payload (legacy response shape).

        Translates ``status: skipped`` with an "already in progress"
        reason back into the ``MediaIntegrityInProgress`` exception so
        the caller can map it to HTTP 409 -- the legacy handler's
        contract.
        """
        from media_stack.services.media_integrity.service import (
            MediaIntegrityInProgress,
        )
        result = run_job_fn(job_name, source="manual", actor=actor_label)
        jobs = (result or {}).get("jobs") or {}
        entry = jobs.get(job_name) or {}
        if payload_key in entry:
            return entry[payload_key]
        status = entry.get("status")
        if status in ("skipped", "prereq_not_met"):
            skip_msg = entry.get("skipped") or entry.get("reason") or ""
            if "already in progress" in str(skip_msg):
                raise MediaIntegrityInProgress(job_name)
            return {"status": "skipped", "reason": skip_msg}
        if status == "error":
            return {"error": entry.get("error", "job failed")}
        # Fallback -- unexpected shape. Surface the JobRunner summary so
        # the caller has *something* to render.
        return result

    def resolve_review_via_job(
        self, run_job_fn: Any, body: dict, actor_label: str,
    ) -> Any:
        """Validate body params, stash them on TLS, then run the job.

        Mirrors ``MediaIntegrityHandlers._run_resolve_review``'s
        validation so 400s come out before the job dispatches.
        """
        _module = sys.modules[__name__]
        app = str(body.get("app", "") or "").strip()
        release_id = str(body.get("release_id", "") or "").strip()
        if not app:
            raise ValueError("app is required")
        if not release_id:
            raise ValueError("release_id is required")
        winner_file_id = body.get("winner_file_id")
        winner_sub_path = body.get("winner_sub_path")
        if winner_file_id is None and winner_sub_path is None:
            raise ValueError("winner_file_id or winner_sub_path required")
        params = {
            "_mi_review_app": app,
            "_mi_review_release_id": release_id,
            "_mi_review_winner_file_id": winner_file_id,
            "_mi_review_winner_sub_path": winner_sub_path,
            "_mi_review_release_kind": body.get("release_kind"),
            "_mi_review_language": body.get("language"),
            "_mi_review_forced": bool(body.get("forced", False)),
            "_mi_review_hi": bool(body.get("hi", False)),
        }
        with _module._mi_review_params(params):
            return _module._run_mi_job_and_extract(
                run_job_fn,
                "media-integrity:resolve-review",
                actor_label,
                "resolve_review",
            )

    def dispatch_media_integrity_via_job(
        self, handler: Any, path: str, body: dict, actor: Any,
    ) -> None:
        """Route a media-integrity POST through ``JobRunner`` while
        preserving the legacy response shape.

        Inlines the gating the legacy handler did (admin check,
        idempotency cache, 409 mapping, body validation for
        resolve-review), then dispatches the heavy work through
        ``run_job(...)`` so the unified ``/api/jobs.history[]`` reflects
        the call. The response body is the raw service payload (not the
        JobRunner summary) so UI v1.3.x's existing fetch handlers don't
        break.
        """
        from media_stack.services.jobs.framework import run_job
        from media_stack.services.media_integrity.service import (
            MediaIntegrityInProgress,
        )

        _module = sys.modules[__name__]
        bare_path = path.split("?", 1)[0]
        job_name = _MI_PATH_TO_JOB.get(bare_path)
        if job_name is None:
            # Defensive fall-through to the legacy handler (it returns 404).
            _media_integrity_handlers.dispatch_post(handler, path, body, actor)
            return

        service = getattr(_media_integrity_handlers, "_service", None)
        if service is None:
            handler._json_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "media-integrity service not configured"},
            )
            return

        # Auth gating mirrors MediaIntegrityHandlers._require_admin.
        if not getattr(actor, "is_admin", False):
            handler._json_response(
                HTTPStatus.FORBIDDEN, {"error": "admin required"},
            )
            return

        # Idempotency cache reuse -- the legacy handler's cache is the
        # source of truth so repeat POSTs within TTL still replay the
        # cached payload (with no JobRunner side effects).
        idem_key = ""
        headers_obj = getattr(handler, "headers", None)
        if headers_obj is not None:
            try:
                idem_key = str(
                    headers_obj.get("Idempotency-Key", "") or "",
                ).strip()
            except Exception:  # noqa: BLE001
                idem_key = ""
        actor_label = getattr(actor, "audit_label", None) or "user"
        cache = getattr(_media_integrity_handlers, "_cache", None)
        if cache is not None and idem_key:
            cached = cache.get(actor_label, idem_key)
            if cached is not None:
                handler._json_response(HTTPStatus.OK, cached)
                return

        # Branch on endpoint. ``reconcile`` honours ``?dry_run=1``: dry
        # runs stay read-only (no JobRunner / no history entry) since
        # the framework only owns committed runs.
        raw_qs = path.partition("?")[2]
        query = _module._parse_query_string(raw_qs)

        try:
            if bare_path == "/api/media-integrity/reconcile":
                dry_run = query.get("dry_run", "") in ("1", "true", "yes")
                if dry_run:
                    payload = service.reconcile(
                        actor=actor_label, dry_run=True,
                    )
                else:
                    payload = _module._run_mi_job_and_extract(
                        run_job, job_name, actor_label, "reconcile",
                    )
            elif bare_path == "/api/media-integrity/enforce-config":
                payload = _module._run_mi_job_and_extract(
                    run_job, job_name, actor_label, "enforce",
                )
            elif bare_path == "/api/media-integrity/resolve-review":
                payload = _module._resolve_review_via_job(
                    run_job, body or {}, actor_label,
                )
            else:  # pragma: no cover -- _MI_PATH_TO_JOB guards this
                handler._json_response(
                    HTTPStatus.NOT_FOUND, {"error": "not found"},
                )
                return
        except MediaIntegrityInProgress:
            handler._json_response(
                HTTPStatus.CONFLICT, {"error": "already in progress"},
            )
            return
        except ValueError as exc:
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": str(exc)[:_ERR_LEN]},
            )
            return

        if cache is not None and idem_key:
            cache.put(actor_label, idem_key, payload)
        handler._json_response(HTTPStatus.OK, payload)


_INSTANCE = MediaIntegrityDispatch()

# Module-level aliases preserve the legacy callable surface so existing
# imports (and ``mock.patch("..._foo")`` test seams) keep working.
_parse_query_string = _INSTANCE.parse_query_string
_mi_review_params = _INSTANCE.mi_review_params
_run_mi_job_and_extract = _INSTANCE.run_mi_job_and_extract
_resolve_review_via_job = _INSTANCE.resolve_review_via_job
_dispatch_media_integrity_via_job = _INSTANCE.dispatch_media_integrity_via_job


__all__ = [
    "_dispatch_media_integrity_via_job",
    "_run_mi_job_and_extract",
    "_resolve_review_via_job",
    "_parse_query_string",
    "_mi_review_params",
    "_MI_PATH_TO_JOB",
    "_MI_REVIEW_TLS",
    "MediaIntegrityDispatch",
]
