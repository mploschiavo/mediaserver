"""Ratchet: ``body.get("…")`` and ``body["…"]`` accesses inside
``api/routes/*.py`` use snake_case keys.

Why a ratchet
-------------
The controller's wire-format convention is snake_case for JSON
body keys (`body.get("job_name")`, not `body.get("jobName")`).
The UI side uses camelCase JS variables but explicitly remaps to
snake_case at the wire boundary; mixed conventions cause silent
failures because Python's ``dict.get`` returns ``None`` on miss
and most route handlers fall through with a default value rather
than raising.

This ratchet scans every route handler for camelCase string
literal keys reaching ``body.get(…)`` or ``body[…]`` indexing,
and fails the build on any that are not in the upstream-passthrough
allowlist below.

Carve-out (UPSTREAM_PASSTHROUGH_KEYS)
-------------------------------------
Some keys legitimately stay camelCase because they match an
upstream service's JSON wire format — receiving an arr webhook,
forwarding an arr quality-profile object, etc. The dividing line:
**keys the operator's UI sends to our controller** are snake_case;
**keys embedded in objects we proxy to/from upstream services**
keep upstream casing.

If you find yourself adding a new entry, document the upstream
service in the comment so future contributors can verify the
exception is still valid.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_ROUTES_DIR = _ROOT / "src" / "media_stack" / "api" / "routes"

# camelCase keys that intentionally retain their upstream casing.
# Each entry is keyed by the literal string and value-commented
# with the upstream service whose wire format dictates the casing.
_UPSTREAM_PASSTHROUGH_KEYS: frozenset[str] = frozenset({
    # Sonarr/Radarr emit ``"eventType": "Download"`` etc. in their
    # webhook payloads (webhooks_and_deferred.handle_webhooks_arr).
    "eventType",
    # Sonarr/Radarr quality-profile object's field name; the
    # POST /api/quality-profiles/{service}/toggle body mirrors the
    # arr-API field so external scripted callers can reuse the
    # arr terminology
    # (post_content_config.QualityProfileToggleService).
    "upgradeAllowed",
})

# camelCase identifier (two-or-more-word). Single-word lowercase
# (``id``, ``key``) and snake_case (``user_id``) pass.
_CAMEL_KEY_RE = re.compile(r"^[a-z]+[A-Z][A-Za-z0-9]*$")


class RouteBodyKeysSnakeCaseRatchet(unittest.TestCase):

    def test_routes_dir_exists(self) -> None:
        self.assertTrue(_ROUTES_DIR.is_dir(), str(_ROUTES_DIR))

    def test_no_camel_case_body_keys_in_route_handlers(self) -> None:
        violations: list[str] = []
        for path in sorted(_ROUTES_DIR.glob("*.py")):
            if path.name.startswith("_"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                key = _camel_body_key(node)
                if key is None:
                    continue
                if key in _UPSTREAM_PASSTHROUGH_KEYS:
                    continue
                snake = _to_snake(key)
                violations.append(
                    f"{path.name}:{node.lineno}: body access uses "
                    f"camelCase key {key!r} — rename to {snake!r} "
                    f"to match the controller's snake_case wire-format "
                    f"convention. If the field MUST stay camelCase "
                    f"because it matches an upstream API, add it to "
                    f"_UPSTREAM_PASSTHROUGH_KEYS with a comment "
                    f"citing the upstream service."
                )

        self.assertFalse(
            violations,
            msg=(
                "\n\nCamelCase JSON body key(s) accessed from route "
                "handler(s):\n\n"
                + "\n".join(violations)
                + "\n\n"
                "See bug_class_url_value_case_normalization memory.\n"
            ),
        )


def _camel_body_key(node: ast.AST) -> str | None:
    """Return the literal key string from ``body.get("foo")`` or
    ``body["foo"]`` if it's a multi-word camelCase identifier;
    None otherwise."""
    if isinstance(node, ast.Call):
        # body.get("foo") or body.get("foo", default)
        func = node.func
        if not isinstance(func, ast.Attribute):
            return None
        if func.attr != "get":
            return None
        if not isinstance(func.value, ast.Name) or func.value.id != "body":
            return None
        if not node.args:
            return None
        return _camel_string_const(node.args[0])
    if isinstance(node, ast.Subscript):
        # body["foo"]
        if not isinstance(node.value, ast.Name) or node.value.id != "body":
            return None
        return _camel_string_const(node.slice)
    return None


def _camel_string_const(arg: ast.AST) -> str | None:
    if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
        return None
    if not _CAMEL_KEY_RE.match(arg.value):
        return None
    return arg.value


def _to_snake(camel: str) -> str:
    out = []
    for i, ch in enumerate(camel):
        if ch.isupper() and i > 0:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


if __name__ == "__main__":
    unittest.main()
