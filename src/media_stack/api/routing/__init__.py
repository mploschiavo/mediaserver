"""ADR-0007 OpenAPI-driven router.

Public surface for route modules + server integration:

  from media_stack.api.routing import RouteModule, get

  class HealthGetRoutes(RouteModule):
      @get("/healthz")
      def handle_healthz(self, handler):
          handler._json_response(200, {"status": "ok"})

  # In server.py:
  from media_stack.api.routing import DefaultDispatcher
  outcome = DefaultDispatcher.instance().try_dispatch(
      "GET", path, handler,
  )

The Router auto-discovers route modules at construction time, so
adding a new domain is "create api/routes/<domain>.py with a
RouteModule subclass". No central registration list to merge.
See ADR-0007 for the parallelism story.
"""

from __future__ import annotations

from media_stack.api.routing.dispatch import (
    DispatchOutcome,
    RouterDispatcher,
)
from media_stack.api.routing.exceptions import (
    RouteDispatchError,
    RouterError,
    RouterMisconfigured,
)
from media_stack.api.routing.registration import (
    RouteModule,
    RouteModuleRegistry,
    RouteSpec,
    delete,
    get,
    patch,
    post,
    put,
)
from media_stack.api.routing.router import (
    CompiledRoute,
    RouteMatch,
    Router,
)


class DefaultDispatcher:
    """Process-wide singleton accessor for the default
    ``RouterDispatcher``. Encapsulates the lazy-init + reset
    behaviour without exposing module-level mutable state."""

    _instance: RouterDispatcher | None = None

    @classmethod
    def instance(cls) -> RouterDispatcher:
        """Return the process-wide dispatcher. Lazily constructed
        on first call; subsequent calls return the same instance."""
        if cls._instance is None:
            cls._instance = RouterDispatcher(Router())
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        """Test-only — clears the singleton so a test can inject
        its own router/dispatcher pair."""
        cls._instance = None


__all__ = [
    "CompiledRoute",
    "DefaultDispatcher",
    "DispatchOutcome",
    "RouteDispatchError",
    "RouteMatch",
    "RouteModule",
    "RouteModuleRegistry",
    "RouteSpec",
    "Router",
    "RouterDispatcher",
    "RouterError",
    "RouterMisconfigured",
    "delete",
    "get",
    "patch",
    "post",
    "put",
]
