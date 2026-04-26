"""Ratchet — controller state paths MUST be dot-prefixed (``.controller``).

Why this test exists
--------------------
The ``.controller`` directory is the dot-prefixed PVC mount on k8s.
State written to the non-dot path ``controller/`` ends up on the pod's
ephemeral overlay — it survives until the next pod restart, then
vanishes. That failure mode has bitten the stack twice:

  1. v1.0.162 migrated state.py, schedulers, snapshots, routing-
     overrides, auth-overrides, telemetry, EPG cache, and the
     password-policy config to ``.controller``. Everything SEEMED
     fixed — dashboard Save Routing survived restart, snapshots
     persisted, EPG caches warmed correctly.

  2. v1.0.169 caught that ``user_service_factory.py`` and
     ``server.py`` (plugin loader) were missed. Users created via
     the UI disappeared on restart, plugins never reloaded.

Without this ratchet, the ONLY way to find the next missed call site
is to notice some piece of state has quietly stopped persisting. That
is a debugging journey measured in hours. The scan here is fast:
grep the source tree for ``"controller"`` used as a path component
where a dotted sibling would be appropriate.

What this catches
-----------------
Any literal ``"controller"`` string used in a pathlib.Path expression
where the LEFT side of the ``/`` operator is a ``CONFIG_ROOT``-ish
variable. Pre-existing well-known writes to ``<app_id>/`` paths
(``/srv-config/prowlarr/``, ``/srv-config/jellyfin/``) aren't affected
because they're separate per-service PVCs.

Allowlist
---------
The ``_ALLOWED`` set captures call sites that legitimately want the
NON-dotted path — typically migration/fallback code that reads the
legacy location so operators on the compose bind-mount see their
state carry over. Add a line here with a short justification when
adding one of those; the whole point of the test is to force the
conversation.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "media_stack"

# ``<anything> / "controller"`` — the non-dotted path component in a
# Path expression. ``PATH_RE`` targets the exact bug class: a pathlib
# division that names ``controller`` without the dot.
_PATH_RE = re.compile(
    r"""
    (?:                       # left operand: CONFIG_ROOT-ish
        config_root
      | CONFIG_ROOT
      | Path\([^)]*CONFIG_ROOT[^)]*\)
      | Path\([^)]+\)         # or any Path(...) expression
    )
    \s*/\s*
    "controller"
    """,
    re.VERBOSE,
)

# Every line here is an explicit call site that legitimately uses the
# legacy non-dotted path — usually a migration branch reading the
# old location to copy state over. Format: ``"relative/path.py:<hint>"``.
# Add entries deliberately with a rationale, not to silence the ratchet.
_ALLOWED = {
    # user_service_factory._state_path reads this path as the LEGACY
    # source, migrates to .controller, returns the new location.
    "core/auth/users/user_service_factory.py:_state_path-legacy-read",
    # server._load_plugins falls back to the legacy path so compose
    # operators mid-upgrade don't lose their plugins on the day the
    # image bumps to v1.0.169.
    "api/server.py:_load_plugins-legacy-fallback",
}


class ControllerStatePathPrefixRatchet(unittest.TestCase):
    """State writes must go through the ``.controller`` PVC path.

    The ratchet accepts a small allowlist for migration/fallback reads
    of the legacy non-dotted path. Everything else fails the test.
    """

    def test_no_non_dotted_controller_path(self):
        violations: list[str] = []
        for py in sorted(SRC.rglob("*.py")):
            if "__pycache__" in str(py):
                continue
            try:
                text = py.read_text(encoding="utf-8")
            except Exception:
                continue
            for match in _PATH_RE.finditer(text):
                # Line where the match started.
                line_no = text.count("\n", 0, match.start()) + 1
                rel = str(py.relative_to(SRC))
                if self._is_allowed(rel, py, line_no):
                    continue
                violations.append(f"  {rel}:{line_no}  {match.group(0)}")
        self.assertFalse(
            violations,
            "Controller state path must be dot-prefixed "
            "(``.controller``) so writes land on the k8s PVC instead "
            "of the pod's ephemeral overlay. Non-dotted offenders:\n"
            + "\n".join(violations)
            + "\n\nIf this call site legitimately needs the legacy "
            "non-dotted path (typically a migration read), add its "
            "``<rel-path>:<hint>`` to ``_ALLOWED`` in the test file "
            "with a one-line rationale.",
        )

    def _is_allowed(self, rel: str, py: Path, line_no: int) -> bool:
        for entry in _ALLOWED:
            path_part, _, _hint = entry.partition(":")
            if rel == path_part:
                return True
        return False


if __name__ == "__main__":
    unittest.main()
