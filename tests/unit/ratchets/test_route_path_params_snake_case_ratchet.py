"""Ratchet: ``@get``/``@post`` decorator path templates use
snake_case for path parameters.

Why a ratchet
-------------
The controller's wire-format convention is snake_case across both
URL path parameters and JSON body keys; the UI maps to camelCase
JS variables explicitly at the boundary (see
``ui/src/lib/events/EventStreamProvider.tsx`` for the canonical
remap pattern).

Mixed conventions silently break callers — a request to
``/api/services/sonarr/api-key`` with a kwarg name expectation of
``service_id`` vs ``serviceId`` fails differently in the two
naming styles, and the Router's ``_check_handler_signature``
catches mismatches at startup but only AFTER both decorator and
method signature already disagree with the spec.

Wave-6 of ADR-0007 (commit 77f60652) introduced four POST-domain
route modules; the case-normalization sweep that followed renamed
``{serviceId}`` → ``{service_id}`` and ``{appName}`` →
``{app_name}`` to match the snake_case convention.

This ratchet locks the convention in place: any new
``@(get|post)("…/{camelCaseName}")`` decorator on a route module
fails the build. Allowlist exists for path *segments* containing
upstream-API conventions if such routes ever land (none today).
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_ROUTES_DIR = _ROOT / "src" / "media_stack" / "api" / "routes"

# Path-param names that intentionally retain a non-snake_case
# spelling because they exactly match an upstream service's API
# field name and renaming would break the contract. None today;
# entries should cite the upstream API and the route file.
_PATH_PARAM_ALLOWLIST: frozenset[str] = frozenset()

# A camelCase identifier inside ``{...}`` in a decorator path.
# Snake_case identifiers with an underscore (``service_id``) and
# all-lowercase single-word names (``service``, ``id``) pass.
# Detects two-or-more-word camelCase: ``serviceId``, ``appName``,
# ``runId``, ``customerNumber``, etc.
_CAMEL_PARAM_RE = re.compile(r"\{([a-z]+[A-Z][A-Za-z0-9]*)\}")

_DECORATOR_NAMES = frozenset({"get", "post", "delete", "put", "patch"})


class RoutePathParamsSnakeCaseRatchet(unittest.TestCase):

    def test_routes_dir_exists(self) -> None:
        self.assertTrue(_ROUTES_DIR.is_dir(), str(_ROUTES_DIR))

    def test_no_camel_case_path_params_in_route_decorators(self) -> None:
        violations: list[str] = []
        for path in sorted(_ROUTES_DIR.glob("*.py")):
            if path.name.startswith("_"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                for dec in node.decorator_list:
                    decorator_path = _decorator_path(dec)
                    if decorator_path is None:
                        continue
                    for match in _CAMEL_PARAM_RE.finditer(decorator_path):
                        param = match.group(1)
                        if param in _PATH_PARAM_ALLOWLIST:
                            continue
                        snake = _to_snake(param)
                        violations.append(
                            f"{path.name}:{dec.lineno}: "
                            f"@{_decorator_name(dec)}"
                            f"({decorator_path!r}) uses camelCase "
                            f"path param {{{param}}} — rename to "
                            f"{{{snake}}} to match the controller's "
                            f"snake_case wire-format convention. "
                            f"Update the spec, decorator, method "
                            f"kwarg, and any tests in lockstep."
                        )

        self.assertFalse(
            violations,
            msg=(
                "\n\nCamelCase path parameter(s) in route decorator(s):\n\n"
                + "\n".join(violations)
                + "\n\n"
                "See bug_class_url_value_case_normalization memory for "
                "context. If a parameter MUST stay camelCase because it "
                "matches an upstream API contract, add it to "
                "_PATH_PARAM_ALLOWLIST with a comment citing the "
                "upstream service.\n"
            ),
        )


def _decorator_path(dec: ast.AST) -> str | None:
    """Return the literal path string from a ``@get("...")`` /
    ``@post("...")`` decorator, or None if the decorator isn't one
    of those forms."""
    if not isinstance(dec, ast.Call):
        return None
    name = _decorator_name(dec)
    if name not in _DECORATOR_NAMES:
        return None
    if not dec.args:
        return None
    arg = dec.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _decorator_name(dec: ast.AST) -> str:
    if isinstance(dec, ast.Call):
        func = dec.func
    else:
        func = dec
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _to_snake(camel: str) -> str:
    out = []
    for i, ch in enumerate(camel):
        if ch.isupper() and i > 0:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


if __name__ == "__main__":
    unittest.main()
