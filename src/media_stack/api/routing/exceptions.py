"""Router-layer exceptions (ADR-0007).

``RouterMisconfigured`` is the startup-time guard. Anything wrong
about the router's view of the world (a registered path that's not
in the spec, a duplicate registration, a handler signature missing
a path parameter the spec declares) raises this BEFORE
``start_api_server()`` binds, so drift surfaces fast — at process
boot, not at dashboard-render time.
"""

from __future__ import annotations


class RouterError(Exception):
    """Base class for routing failures."""


class RouterMisconfigured(RouterError):
    """Startup-time configuration error.

    Examples:

      * ``@get("/api/users")`` registered for a path the OpenAPI
        spec doesn't declare.
      * Two route modules register the same (verb, path).
      * A handler's signature kwargs don't match the spec's
        ``parameters: [{in: path, name: ...}]`` declarations.
      * ``contracts/api/openapi.yaml`` is missing or unparseable.

    Operator action: fix the route module / spec entry. The error
    message names the offending file + line + path.
    """


class RouteDispatchError(RouterError):
    """Runtime dispatch failure that wasn't caught by the contract
    validator. Rare — most runtime issues become 4xx responses; this
    is for genuinely unexpected internal state."""


__all__ = [
    "RouterError",
    "RouterMisconfigured",
    "RouteDispatchError",
]
