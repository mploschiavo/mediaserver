"""Class-based route registration (ADR-0007).

Each route module defines a class that subclasses ``RouteModule``;
its methods are decorated with ``@get(path)`` / ``@post(path)`` /
etc., which tag the method with route metadata (no module-level
side effects). At Router-build time, the auto-discovery imports
every ``api/routes/*.py`` module — that triggers
``RouteModule.__init_subclass__``, which registers the class with
the ``RouteModuleRegistry`` singleton. The Router then instantiates
each registered class and walks its methods to find the tagged
ones.

This shape satisfies the project's OO-discipline rule (no loose
top-level handler functions; everything is a class method) while
keeping ``@get(path)`` ergonomics next to the method definition.

Example route module:

    class HealthGetRoutes(RouteModule):
        @get("/healthz")
        def handle_healthz(self, handler):
            handler._json_response(200, {"status": "ok"})
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


HandlerFn = Callable[..., Any]
_ROUTE_TAG_ATTR = "_route_metadata"


@dataclass(frozen=True)
class RouteSpec:
    """One method-tagged route. The ``Router`` consumes a list of
    these at startup after instantiating each ``RouteModule``."""

    verb: str
    path: str
    handler: HandlerFn  # bound method on a RouteModule instance
    module_class: type
    method_name: str

    @property
    def display(self) -> str:
        return (
            f"{self.module_class.__module__}."
            f"{self.module_class.__qualname__}.{self.method_name}"
        )


class _RouteDecorator:
    """Decorator factory that tags an unbound method with verb +
    path metadata. Stateless — the tag goes on the method object
    itself; nothing module-global accumulates."""

    def __init__(self, verb: str) -> None:
        self._verb = verb.upper()

    def __call__(self, path: str) -> Callable[[HandlerFn], HandlerFn]:
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError(
                f"@{self._verb.lower()}(path) requires an absolute "
                f"path string starting with '/'; got {path!r}",
            )
        verb = self._verb

        def _decorate(fn: HandlerFn) -> HandlerFn:
            existing = getattr(fn, _ROUTE_TAG_ATTR, None)
            if existing is not None:
                raise ValueError(
                    f"Method {fn.__qualname__} already tagged with "
                    f"{existing!r}; cannot also tag {verb} {path!r}. "
                    f"Use one decorator per method.",
                )
            setattr(fn, _ROUTE_TAG_ATTR, (verb, path))
            return fn

        return _decorate


# Public decorator instances. Route module CLASSES use them on
# their METHODS — tag-only, no module-global side effects.
get = _RouteDecorator("GET")
post = _RouteDecorator("POST")
delete = _RouteDecorator("DELETE")
put = _RouteDecorator("PUT")
patch = _RouteDecorator("PATCH")


class RouteModuleRegistry:
    """Process-wide registry of ``RouteModule`` subclasses.

    Singleton. Subclasses register via
    ``RouteModule.__init_subclass__`` at class-definition time
    (which fires when ``api/routes/*.py`` modules are imported by
    the Router's auto-discovery). The Router consumes the registry
    at startup.
    """

    _instance: "RouteModuleRegistry | None" = None

    @classmethod
    def instance(cls) -> "RouteModuleRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._instance = None

    def __init__(self) -> None:
        self._modules: list[type] = []

    def register(self, module_class: type) -> None:
        if module_class in self._modules:
            return
        self._modules.append(module_class)

    def all_modules(self) -> tuple[type, ...]:
        return tuple(self._modules)


class RouteModule:
    """Base class for route modules.

    Each ``api/routes/<domain>.py`` defines one subclass with
    ``@get`` / ``@post``-tagged methods. Subclassing automatically
    registers the class with ``RouteModuleRegistry``.

    Per-call ``handler`` is the ``ControllerAPIHandler`` instance;
    the framework passes it as the first arg after ``self``.

    Subclasses are stateless by default. If a route module needs
    state (e.g. a cached service client), accept it via
    constructor kwargs and the Router will pass them through —
    not yet wired in Phase 1; deferred to Phase 3 DI work.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        RouteModuleRegistry.instance().register(cls)

    @classmethod
    def routes_on(cls, instance: "RouteModule") -> Iterable[RouteSpec]:
        """Yield one ``RouteSpec`` per ``@get``/``@post``-tagged
        method on ``instance``. The Router calls this after
        instantiating the class — bound methods get registered so
        ``self`` flows through normally on dispatch."""
        instance_class = type(instance)
        for name in dir(instance):
            if name.startswith("__"):
                continue
            method = getattr(instance, name, None)
            if not callable(method):
                continue
            tag = getattr(method, _ROUTE_TAG_ATTR, None)
            if tag is None:
                continue
            verb, path = tag
            yield RouteSpec(
                verb=verb, path=path, handler=method,
                module_class=instance_class, method_name=name,
            )


__all__ = [
    "RouteModule",
    "RouteModuleRegistry",
    "RouteSpec",
    "get",
    "post",
    "delete",
    "put",
    "patch",
]
