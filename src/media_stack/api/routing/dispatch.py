"""Request dispatch entry-point (ADR-0007 Phase 1).

``RouterDispatcher.try_dispatch`` is invoked by ``server.py``'s
``do_GET`` / ``do_POST`` BEFORE falling through to the legacy
``handlers_get.handle()`` / ``handlers_post.handle()`` chains.

Returns:
  * ``True`` — router matched a registered route, dispatched, and
    wrote a response. Caller should NOT fall through.
  * ``False`` — no registered route matched. Caller falls through
    to the legacy chain during migration. After ADR-0007 Phase 2
    completes, the caller can switch to strict-404 mode (uses
    ``DispatchOutcome.NO_MATCH`` to distinguish this from a 405).

Path parameters declared in the OpenAPI spec are passed to handlers
as kwargs with names matching the spec's
``parameters: [{in: path, name: ...}]`` declarations.
"""

from __future__ import annotations

import logging
from enum import Enum
from http import HTTPStatus
from typing import Any

from media_stack.api.routing.router import Router


logger = logging.getLogger(__name__)


class DispatchOutcome(Enum):
    """What happened on a dispatch attempt.

    ``HANDLED`` — router matched + invoked the handler.
    ``NO_MATCH`` — path is not registered with the router. During
        migration, caller falls through to legacy. After Phase 2
        cleanup, caller emits 404.
    ``METHOD_NOT_ALLOWED`` — path exists in the spec but the verb
        doesn't match any registered route OR any spec verb. Caller
        emits 405 directly; legacy chain wouldn't help.
    """

    HANDLED = "handled"
    NO_MATCH = "no_match"
    METHOD_NOT_ALLOWED = "method_not_allowed"


class RouterDispatcher:
    """Per-process dispatcher. Single instance constructed at
    server start; ``try_dispatch`` invoked per-request.

    Constructor-injected ``Router`` for testability — tests can
    construct a dispatcher with a custom router (different spec
    path, isolated route registrations, etc.)."""

    def __init__(self, router: Router) -> None:
        self._router = router

    def try_dispatch(
        self, verb: str, path: str, handler: Any,
    ) -> DispatchOutcome:
        """Attempt to route the request. Returns the outcome.

        ``verb`` is the HTTP method (``GET`` / ``POST`` / etc.).
        ``path`` is the path component WITHOUT query string —
        callers are responsible for stripping ``?...``.
        ``handler`` is the ``ControllerAPIHandler`` instance — passed
        to the registered route function as the first positional arg.
        """
        verb = verb.upper()
        match = self._router.match(verb, path)
        if match is not None:
            try:
                match.route.handler(handler, **match.params)
            except Exception:
                logger.exception(
                    "Router-dispatched handler raised: %s %s -> "
                    "%s.%s",
                    verb, path, match.route.module, match.route.qualname,
                )
                raise
            return DispatchOutcome.HANDLED

        # No registered route. Distinguish 405 from "not found yet"
        # — a 405 is "spec declares this path but with different
        # verbs", which we can answer authoritatively even before
        # Phase 2 completes. NO_MATCH means the path isn't in the
        # spec, OR the spec doesn't have this verb AND we have no
        # registered route — let the legacy chain handle it during
        # migration.
        spec_verbs = self._router.spec_paths().get(path)
        if spec_verbs is not None and verb not in spec_verbs:
            return DispatchOutcome.METHOD_NOT_ALLOWED
        return DispatchOutcome.NO_MATCH

    def write_method_not_allowed(self, handler: Any, path: str) -> None:
        """Emit a 405 with the spec-declared verbs. Caller invokes
        this after ``try_dispatch`` returns ``METHOD_NOT_ALLOWED``."""
        spec_verbs = self._router.spec_paths().get(path) or frozenset()
        allow = ", ".join(sorted(spec_verbs))
        # The HTTP/1.1 spec requires ``Allow`` on a 405 response.
        body = {
            "error": "method_not_allowed",
            "path": path,
            "allowed": sorted(spec_verbs),
        }
        if hasattr(handler, "_raw_response"):
            import json as _json
            payload = _json.dumps(body).encode()
            handler._raw_response(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "application/json",
                payload,
                {"Allow": allow} if allow else None,
            )
        else:  # pragma: no cover — handler protocol mismatch
            handler._json_response(HTTPStatus.METHOD_NOT_ALLOWED, body)


__all__ = ["RouterDispatcher", "DispatchOutcome"]
