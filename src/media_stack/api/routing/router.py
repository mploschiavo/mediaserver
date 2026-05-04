"""OpenAPI-driven Router (ADR-0007 Phase 1).

Reads ``contracts/api/openapi.yaml`` at construction time,
auto-discovers every module under ``api/routes/``
(``pkgutil.iter_modules``), instantiates each ``RouteModule``
subclass, walks the bound methods for ``@get``/``@post`` tags,
and compiles path patterns for O(1) exact-match lookup with
regex fallback for parameterized paths
(``/api/users/{user_id}``).

Designed for ADR-0007 Phase 2 parallelism: route modules are
classes that subclass ``RouteModule``; subclassing registers the
class with ``RouteModuleRegistry`` automatically. Adding a new
domain is a NEW file under ``api/routes/`` defining a new class —
no central registration list to merge.

The startup-time drift check (``RouterMisconfigured``) catches:
  * Registered (verb, path) where the path isn't declared in the
    spec, or the path is declared but the verb isn't.
  * Two registrations for the same (verb, path).
  * Method signatures that don't accept the spec's path
    parameters as kwargs.
  * Missing ``contracts/api/openapi.yaml``.

Permissive on the OTHER direction: spec paths with no registered
handler don't fail; they fall through to the legacy chain. After
Phase 2 completes, ``Router.assert_full_spec_coverage()`` flips
that to strict.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from media_stack.api.routing.exceptions import RouterMisconfigured
from media_stack.api.routing.registration import (
    RouteModule,
    RouteModuleRegistry,
    RouteSpec,
)


logger = logging.getLogger(__name__)


_PATH_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_DEFAULT_OPENAPI_PATH = (
    Path(__file__).resolve().parents[4]
    / "contracts" / "api" / "openapi.yaml"
)
_DEFAULT_ROUTES_PACKAGE = "media_stack.api.routes"
_SUPPORTED_VERBS = frozenset({"GET", "POST", "DELETE", "PUT", "PATCH"})


# ---------------------------------------------------------------------------
# Infrastructure allowlist for ``assert_full_spec_coverage()``.
#
# These (verb, path) pairs are documented in the OpenAPI spec
# because operators consume them, but they're served directly by
# ``server.py`` outside the Router and intentionally bypass the
# RouteModule pattern. ``assert_full_spec_coverage`` skips these
# entries so the strict-coverage check doesn't trip on
# infrastructure GETs that have no route module by design.
#
# Adding a new entry requires explicit intent — the test in
# ``tests/unit/api/routing/test_router_allowlist.py`` pins the
# exact set so an accidental expansion fails the build.
# ---------------------------------------------------------------------------

_INFRASTRUCTURE_ALLOWLIST: frozenset[tuple[str, str]] = frozenset({
    # Landing page redirect — server.py emits a 302 to /dashboard
    # without any route module involved.
    ("GET", "/"),
    # Dashboard landing page — server.py serves a static HTML
    # bundle directly off disk (no API handler in the Router).
    ("GET", "/dashboard"),
    # Swagger UI / Redoc page — server.py serves the rendered
    # docs HTML; the spec consumer (``openapi.yaml``) is loaded by
    # the Router itself but the page is not a RouteModule target.
    ("GET", "/api/docs"),
    # Static asset serving — assets live on disk and are streamed
    # through ``server.py``'s static-file handler. Wrapping that
    # in a RouteModule would re-implement file mime-detection +
    # range-request semantics for no benefit.
    ("GET", "/api/static/{asset}"),
    # Prometheus metrics scrape — the prometheus_client library
    # owns the response body shape; ``server.py`` glues it to the
    # request without going through the Router.
    ("GET", "/metrics"),
})


HandlerFn = Callable[..., Any]
PathParams = Mapping[str, str]


@dataclass(frozen=True)
class CompiledRoute:
    """A registered route ready for dispatch."""

    verb: str
    path: str  # spec-template form, e.g. ``/api/users/{user_id}``
    handler: HandlerFn  # bound method on a RouteModule instance
    pattern: re.Pattern[str] | None  # None for exact-match
    param_names: tuple[str, ...]
    display: str


@dataclass(frozen=True)
class RouteMatch:
    """Result of ``Router.match()``."""

    route: CompiledRoute
    params: PathParams


class _OpenApiSpecLoader:
    """Reads ``openapi.yaml`` and exposes the ``path → {verbs}``
    map. Constructor-injected file path so tests can point at
    fixtures."""

    def __init__(self, openapi_path: Path) -> None:
        self._openapi_path = openapi_path

    def load(self) -> dict[str, set[str]]:
        if not self._openapi_path.is_file():
            raise RouterMisconfigured(
                f"OpenAPI spec not found at {self._openapi_path}",
            )
        import yaml as _yaml
        try:
            doc = _yaml.safe_load(
                self._openapi_path.read_text(encoding="utf-8"),
            ) or {}
        except _yaml.YAMLError as exc:
            raise RouterMisconfigured(
                f"OpenAPI spec at {self._openapi_path} is not valid "
                f"YAML: {exc}",
            ) from exc
        paths = doc.get("paths") or {}
        if not isinstance(paths, dict):
            raise RouterMisconfigured(
                f"OpenAPI spec at {self._openapi_path}: ``paths`` "
                f"is not a mapping",
            )
        result: dict[str, set[str]] = {}
        for path, methods in paths.items():
            if not isinstance(path, str) or not path.startswith("/"):
                continue
            if not isinstance(methods, dict):
                continue
            result[path] = {
                m.upper() for m in methods
                if m.upper() in _SUPPORTED_VERBS
            }
        return result


class _RouteModuleDiscoverer:
    """Imports every module under the routes package so subclassing
    side-effects register their classes with ``RouteModuleRegistry``."""

    def __init__(self, routes_package: str) -> None:
        self._routes_package = routes_package

    def discover(self) -> None:
        try:
            package = importlib.import_module(self._routes_package)
        except ImportError as exc:
            raise RouterMisconfigured(
                f"Routes package {self._routes_package!r} not "
                f"importable: {exc}",
            ) from exc
        package_path = getattr(package, "__path__", None)
        if package_path is None:
            return
        for _, name, _is_pkg in pkgutil.iter_modules(package_path):
            if name.startswith("_"):
                continue
            importlib.import_module(f"{self._routes_package}.{name}")


class _RouteCompiler:
    """Turns a list of ``RouteSpec`` into the dispatch tables the
    Router uses at request time. Validates each spec against the
    OpenAPI ``path → {verbs}`` map; raises ``RouterMisconfigured``
    on drift."""

    def __init__(self, spec_paths: Mapping[str, set[str]]) -> None:
        self._spec_paths = spec_paths

    def compile_all(
        self, route_specs: list[RouteSpec],
    ) -> tuple[
        dict[tuple[str, str], CompiledRoute],
        list[CompiledRoute],
    ]:
        seen: set[tuple[str, str]] = set()
        exact: dict[tuple[str, str], CompiledRoute] = {}
        parameterized: list[CompiledRoute] = []

        for spec in route_specs:
            self._check_unique(seen, spec)
            self._check_in_spec(spec)
            param_names = tuple(_PATH_PARAM_RE.findall(spec.path))
            self._check_handler_signature(spec, param_names)

            display = spec.display
            if param_names:
                pattern = self._compile_pattern(spec.path)
                parameterized.append(CompiledRoute(
                    verb=spec.verb, path=spec.path, handler=spec.handler,
                    pattern=pattern, param_names=param_names,
                    display=display,
                ))
            else:
                exact[(spec.verb, spec.path)] = CompiledRoute(
                    verb=spec.verb, path=spec.path, handler=spec.handler,
                    pattern=None, param_names=(),
                    display=display,
                )
        return exact, parameterized

    def _check_unique(
        self, seen: set[tuple[str, str]], spec: RouteSpec,
    ) -> None:
        key = (spec.verb, spec.path)
        if key in seen:
            raise RouterMisconfigured(
                f"Duplicate route registration for {spec.verb} "
                f"{spec.path} (last registered by {spec.display}). "
                f"Each (verb, path) may be registered exactly once.",
            )
        seen.add(key)

    def _check_in_spec(self, spec: RouteSpec) -> None:
        verbs_for_path = self._spec_paths.get(spec.path)
        if verbs_for_path is None:
            raise RouterMisconfigured(
                f"{spec.verb} {spec.path}: registered by "
                f"{spec.display} but {spec.path!r} is not in the "
                f"OpenAPI spec. Either add the path to the spec or "
                f"remove the registration.",
            )
        if spec.verb not in verbs_for_path:
            raise RouterMisconfigured(
                f"{spec.verb} {spec.path}: registered by "
                f"{spec.display} but the spec declares only "
                f"{sorted(verbs_for_path)} for this path. Either "
                f"add ``{spec.verb.lower()}:`` to the spec entry "
                f"or change the decorator.",
            )

    def _check_handler_signature(
        self, spec: RouteSpec, param_names: tuple[str, ...],
    ) -> None:
        try:
            sig = inspect.signature(spec.handler)
        except (TypeError, ValueError) as exc:
            raise RouterMisconfigured(
                f"Cannot inspect signature of {spec.display} for "
                f"{spec.verb} {spec.path}: {exc}",
            ) from exc
        params = sig.parameters
        for name in param_names:
            if name not in params:
                raise RouterMisconfigured(
                    f"{spec.verb} {spec.path}: spec declares path "
                    f"parameter {name!r} but {spec.display} has no "
                    f"such kwarg. Add ``{name}: str`` to the method "
                    f"signature.",
                )

    def _compile_pattern(self, path: str) -> re.Pattern[str]:
        # Replace each {name} with a named regex group matching one
        # path segment (OpenAPI 3.0 default ``style: simple,
        # explode: false``).
        pattern_str = "^"
        idx = 0
        for match in _PATH_PARAM_RE.finditer(path):
            pattern_str += re.escape(path[idx:match.start()])
            pattern_str += f"(?P<{match.group(1)}>[^/]+)"
            idx = match.end()
        pattern_str += re.escape(path[idx:])
        pattern_str += "$"
        return re.compile(pattern_str)


class Router:
    """OpenAPI-driven request router.

    Constructor-injected ``openapi_path`` + ``routes_package`` for
    testability; defaults match the production layout. Discovery
    runs at construction time — configuration errors surface
    before the server binds.
    """

    def __init__(
        self,
        *,
        openapi_path: Path = _DEFAULT_OPENAPI_PATH,
        routes_package: str | None = _DEFAULT_ROUTES_PACKAGE,
    ) -> None:
        """Construct a Router.

        ``routes_package`` is the dotted module path to the routes
        directory whose ``RouteModule`` subclasses get auto-
        discovered. Pass ``None`` (typically only from tests that
        define ``RouteModule`` subclasses inline) to skip discovery
        — the constructor still walks the
        ``RouteModuleRegistry`` for already-registered classes.
        """
        self._openapi_path = openapi_path
        self._routes_package = routes_package

        spec_loader = _OpenApiSpecLoader(openapi_path)
        self._spec_paths = spec_loader.load()

        if routes_package is not None:
            _RouteModuleDiscoverer(routes_package).discover()

        route_specs = self._collect_route_specs()
        compiler = _RouteCompiler(self._spec_paths)
        self._exact, self._parameterized = compiler.compile_all(route_specs)

    def _collect_route_specs(self) -> list[RouteSpec]:
        specs: list[RouteSpec] = []
        for module_class in RouteModuleRegistry.instance().all_modules():
            try:
                instance = module_class()
            except Exception as exc:
                raise RouterMisconfigured(
                    f"Failed to instantiate {module_class.__module__}."
                    f"{module_class.__qualname__} "
                    f"for route registration: {exc}",
                ) from exc
            specs.extend(RouteModule.routes_on(instance))
        return specs

    # --- public API ---------------------------------------------------

    def has_route(self, verb: str, path: str) -> bool:
        return self.match(verb, path) is not None

    def match(self, verb: str, path: str) -> RouteMatch | None:
        verb = verb.upper()
        exact = self._exact.get((verb, path))
        if exact is not None:
            return RouteMatch(route=exact, params={})
        for route in self._parameterized:
            if route.verb != verb:
                continue
            assert route.pattern is not None
            m = route.pattern.match(path)
            if m is not None:
                return RouteMatch(route=route, params=m.groupdict())
        return None

    def spec_paths(self) -> Mapping[str, frozenset[str]]:
        return {p: frozenset(v) for p, v in self._spec_paths.items()}

    def registered_routes(self) -> tuple[CompiledRoute, ...]:
        return tuple(self._exact.values()) + tuple(self._parameterized)

    def assert_full_spec_coverage(self) -> None:
        """Strict mode: raise if any spec (path, verb) is missing
        a registered handler, EXCEPT entries on the
        ``_INFRASTRUCTURE_ALLOWLIST`` — those are served by
        ``server.py`` outside the Router (landing pages, static
        assets, Prometheus metrics, Swagger docs) and
        intentionally bypass the RouteModule pattern.

        Phase 2's cleanup commit calls this after every domain
        has migrated. The allowlist is exact-match — adding a new
        infrastructure GET requires editing both the constant and
        its pin test in
        ``tests/unit/api/routing/test_router_allowlist.py``.
        """
        missing: list[str] = []
        for path, verbs in self._spec_paths.items():
            for verb in verbs:
                if (verb, path) in _INFRASTRUCTURE_ALLOWLIST:
                    continue
                if not self.has_route(verb, path):
                    missing.append(f"{verb} {path}")
        if missing:
            preview = sorted(missing)[:20]
            extra = (
                f" (and {len(missing) - 20} more)"
                if len(missing) > 20 else ""
            )
            raise RouterMisconfigured(
                "Spec paths without registered handlers (strict "
                "mode): " + ", ".join(preview) + extra,
            )


__all__ = [
    "Router",
    "CompiledRoute",
    "RouteMatch",
]
