"""Ratchets pinning two bug classes that hit production in v1.0.199.

Bug class B — Authenticated-caller synth fallback under SSO empty
=================================================================
Under Authelia SSO the controller doesn't mint native ``SessionStore``
sessions — the cookie lives in Authelia, and the file-backed
``SessionAdminProvider`` impls degrade to ``[]`` in the default
deployment. When the session aggregator returns empty, handlers that
list "the current caller's own sessions / tokens / etc." must
synthesise a fallback row representing the authenticated caller so the
UI doesn't render a misleading "no live sessions" empty state for
someone who is clearly logged in (they're staring at the page right
now).

The reference implementation is
``security_get_handlers.py::_synth_caller_session(actor)``, appended in
``_active_sessions`` and ``_my_sessions`` when the aggregator returns
empty AND the actor is authenticated.

This ratchet AST-walks the handler modules and asserts that every
list-of-caller-scoped-resource handler either:
  * calls ``_synth_caller_session(...)``, or
  * is on ``KNOWN_NO_SYNTH`` with a documented reason.

Bug class D — UI fetch ↔ backend identity-field consistency
============================================================
The SPA fetched ``/api/users/{X}/login-history`` with ``X = user.id``
(the UUID), but the backend's ``login_history_for_user`` filters audit
events on ``target == username`` — and login events store
``target=username``. The UUID never matched, so the admin
login-history drawer was always empty. Fixed in v1.0.199 by routing
``/api/me/login-history`` through a username-aware path; the admin-
keyed route is still wrong unless the UI passes ``user.username``.

This ratchet pulls every ``api/users/${X}/<resource>`` template from
the UI hooks files and asserts the interpolated variable matches the
identity field the backend keys that resource on (per a hand-curated
``EXPECTED_ID_FIELD`` table sourced from manual reading of the Python
handler).

Allowlist policy: both allowlists ship near-empty. Each entry must
carry a one-line ``TODO`` explaining why the contract is violated and
what the domain fix would be — the ratchet pins the contract; the
fix is domain work that lands separately.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "media_stack"
UI_FEATURES = ROOT / "ui" / "src" / "features"


# ---------------------------------------------------------------------------
# Ratchet 1 — authenticated-caller synth fallback (Bug class B)
# ---------------------------------------------------------------------------


# Module paths (relative to ROOT) AST-walked for handler functions.
# Add new files here when a new handler module lands.
_HANDLER_MODULES: tuple[Path, ...] = (
    SRC / "api" / "services" / "security_get_handlers.py",
    SRC / "api" / "services" / "security_post_handlers.py",
    SRC / "api" / "handlers_get.py",
    SRC / "api" / "handlers_post.py",
)

# Handler-name pattern from the spec: caller-scoped list-shaped GETs.
# Anchored on a leading underscore (private method) followed by one of
# the scope words and one of the resource words.
_CALLER_SCOPED_HANDLER_RE = re.compile(
    r"^_(my|active|user)_(sessions|tokens|recent|login_history)$",
)

# Handlers exempt from the synth-fallback contract. Each entry MUST
# carry a justification — empty-list is intentional here (e.g. an
# admin cluster-wide view where the caller might legitimately not be
# in the list, or a per-user admin view of *another* user where the
# caller's own session is irrelevant).
KNOWN_NO_SYNTH: dict[str, str] = {
    # Tokens are user-created artefacts. A caller can legitimately have
    # zero API tokens (the common case until they generate one). Synth
    # fallback would manufacture a fake token row, which makes no
    # sense — there's no underlying credential to revoke.
    "_my_tokens": (
        "tokens are explicit user artefacts; empty is the correct "
        "initial state, not an SSO-degradation symptom"
    ),
    # Login history is an audit-log query; an empty result genuinely
    # means "no auth events found in the lookback window for this
    # user". The caller's *current* login is the row we'd synthesise,
    # but it's already in the audit log — synthesising would either
    # double-count it or paper over an audit-pipeline outage. Better
    # to surface the real empty state.
    "_my_login_history": (
        "audit-log query; empty == genuine no-events-in-window. "
        "Synthesising would mask an audit-pipeline outage."
    ),
    # Admin-scoped view of another user's history. The caller's own
    # session has no business being injected into a different user's
    # row. Empty here means "this user has no recorded auth events"
    # — surface that truthfully.
    "_user_login_history": (
        "admin view of another user's audit trail; caller is not the "
        "target — synthesising the caller would be wrong."
    ),
}


def _walk_function_defs(module_path: Path) -> list[tuple[str, ast.FunctionDef]]:
    """Return ``(qualified_name, FunctionDef)`` pairs for every
    function/method defined at the top level of ``module_path``,
    including methods defined directly inside top-level classes.
    Methods are qualified ``ClassName.method_name``. Nested
    functions (closures, decorators returning functions) are out
    of scope — handler dispatch always lands on a top-level method
    or a top-level helper."""
    text = module_path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(module_path))
    out: list[tuple[str, ast.FunctionDef]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append((f"{node.name}.{child.name}", child))  # type: ignore[arg-type]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node))  # type: ignore[arg-type]
    return out


def _function_calls_synth(fn: ast.FunctionDef) -> bool:
    """True iff ``fn``'s body calls ``_synth_caller_session(...)``
    anywhere (directly or via attribute access on ``self``)."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "_synth_caller_session":
                return True
            if isinstance(f, ast.Attribute) and f.attr == "_synth_caller_session":
                return True
    return False


class CallerSynthFallbackRatchet(unittest.TestCase):
    """Every caller-scoped list-of-resources handler must call
    ``_synth_caller_session`` on the empty path, or be allowlisted
    in :data:`KNOWN_NO_SYNTH` with a documented reason.

    Why a ratchet
    -------------
    The v1.0.199 fix established the synth-fallback pattern across
    ``_active_sessions`` and ``_my_sessions``. Without this ratchet,
    a future handler ("``/api/me/devices``", "``/api/me/api-keys``",
    "``/api/me/active-rooms``" — pick your noun) will silently miss
    the contract on day one and re-bug the SSO path.

    Scan strategy
    -------------
    AST-walk every module in :data:`_HANDLER_MODULES`. For each method
    whose name matches :data:`_CALLER_SCOPED_HANDLER_RE`, assert the
    body either calls ``_synth_caller_session`` or carries an entry in
    the allowlist. We deliberately don't fail on missing decorators or
    docstrings — those are style ratchets owned elsewhere. This one
    pins ONLY the synth-fallback contract.
    """

    def test_caller_scoped_handlers_synth_or_allowlisted(self) -> None:
        violations: list[str] = []
        seen: set[str] = set()
        for module_path in _HANDLER_MODULES:
            if not module_path.is_file():
                continue
            for qual_name, fn in _walk_function_defs(module_path):
                short = fn.name
                if not _CALLER_SCOPED_HANDLER_RE.match(short):
                    continue
                seen.add(short)
                if _function_calls_synth(fn):
                    continue
                if short in KNOWN_NO_SYNTH:
                    continue
                rel = module_path.relative_to(ROOT)
                violations.append(
                    f"{rel}::{qual_name} — caller-scoped list handler does "
                    f"not call _synth_caller_session and is not in "
                    f"KNOWN_NO_SYNTH. Add a synth-empty fallback (see "
                    f"_active_sessions for the pattern) or allowlist "
                    f"with a one-line justification."
                )
        # Sanity check: the reference implementations must be present
        # — if they ever vanish, the ratchet would silently pass with
        # zero matches.
        for required in ("_active_sessions", "_my_sessions"):
            self.assertIn(
                required, seen,
                f"Reference handler {required!r} not found in scanned "
                f"modules. The ratchet's _CALLER_SCOPED_HANDLER_RE or "
                f"_HANDLER_MODULES list is out of date.",
            )
        self.assertFalse(
            violations,
            "Caller-scoped synth-fallback contract violated:\n  - "
            + "\n  - ".join(violations),
        )

    def test_known_no_synth_entries_have_justifications(self) -> None:
        for name, reason in KNOWN_NO_SYNTH.items():
            self.assertTrue(
                reason and len(reason) >= 20,
                f"KNOWN_NO_SYNTH[{name!r}] must carry a substantive "
                f"justification (>=20 chars); got {reason!r}.",
            )

    def test_synth_helper_is_defined(self) -> None:
        """The reference helper must exist; its absence would break
        every implementer of the contract."""
        path = SRC / "api" / "services" / "security_get_handlers.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            "def _synth_caller_session(", text,
            "_synth_caller_session helper missing — the synth-empty "
            "fallback contract has nothing to dispatch to.",
        )


# ---------------------------------------------------------------------------
# Ratchet 2 — UI fetch ↔ backend identity-field consistency (Bug class D)
# ---------------------------------------------------------------------------


# Per-resource expected identity field. Sourced from MANUAL READING of:
#   * src/media_stack/api/services/security_get_handlers.py
#   * src/media_stack/api/handlers_get.py (_UserMgmtGetHelper)
#   * src/media_stack/core/auth/users/user_service.py
#   * src/media_stack/services/security/security_report_service.py
#
# Identity-field values:
#   "user_id"   — the UUID from UserStore (matches ``user.id`` in TS).
#   "username"  — the human-readable login name (matches ``user.username``).
#
# The backend keys each resource as follows:
#   /api/users/{X}/sessions       -> user_service.list_sessions(X)
#                                    -> UserStore.get(X) [keyed by id (UUID)]
#                                    => user_id
#   /api/users/{X}/login-history  -> SecurityReportService.login_history_for_user(
#                                       username=X)
#                                    -> filters audit-log target==X (a USERNAME)
#                                    => username
#   /api/users/{X}/sessions/{sid}/revoke
#                                  -> user_service.revoke_session(X, sid)
#                                    -> UserStore.get(X) [keyed by id (UUID)]
#                                    => user_id
#   /api/users/{X}/delete         -> UserStore-keyed mutation => user_id
#   /api/users/{X}/role           -> UserStore-keyed mutation => user_id
#   /api/users/{X}/state          -> UserStore-keyed mutation => user_id
#   /api/users/{X}/reset-password -> UserStore-keyed mutation => user_id
#   /api/users/{X}/revoke-sessions
#                                  -> UserStore-keyed mutation => user_id
#   /api/users/{X} (PATCH/GET)    -> UserStore-keyed access => user_id
#
# Add a row when a new ``/api/users/{X}/<resource>`` endpoint lands.
EXPECTED_ID_FIELD: dict[str, str] = {
    "sessions": "user_id",
    "login-history": "username",
    "delete": "user_id",
    "role": "user_id",
    "state": "user_id",
    "reset-password": "user_id",
    "revoke-sessions": "user_id",
    # No trailing segment — the bare /api/users/{X} singleton.
    "": "user_id",
}


# Map TS variable expressions (as they appear inside ``${...}``) to the
# identity field they hold. The right-hand side is the SAME vocabulary
# as :data:`EXPECTED_ID_FIELD` values so the cross-check is a simple
# string equality.
TS_VARIABLE_TO_FIELD: dict[str, str] = {
    "user_id": "user_id",
    "user_id as string": "user_id",
    "userId": "user_id",
    "vars.user_id": "user_id",
    "user.id": "user_id",
    "username": "username",
    "user.username": "username",
}


# Files to scan. We keep this explicit (rather than a glob) so a sibling
# agent that renames a directory can't accidentally hide drift behind
# a missing match.
_TS_FILES_TO_SCAN: tuple[Path, ...] = (
    UI_FEATURES / "users-admin" / "hooks.ts",
    UI_FEATURES / "me" / "hooks.ts",
    UI_FEATURES / "sessions" / "hooks.ts",
)


# Allowlist of ``(ts_file_relpath, full_pattern)`` -> reason pairs for
# legitimate drift. Should ship empty / near-empty; every entry is a
# domain bug waiting to land.
KNOWN_DRIFT: dict[tuple[str, str], str] = {
    # The admin-keyed login-history hook still passes user.id (UUID),
    # but the backend filters audit events on target==username. The
    # v1.0.199 fix routed self-service through /api/me/login-history;
    # the admin path remains broken until the UI switches to
    # user.username (or the backend learns to look up username from
    # user_id before filtering).
    # TODO: domain fix — pass user.username to useUserLoginHistory or
    #       resolve user_id -> username inside the backend handler.
    (
        "ui/src/features/users-admin/hooks.ts",
        "api/users/${encodeURIComponent(user_id as string)}/login-history",
    ): (
        "admin login-history fetch still passes UUID; backend filters "
        "audit events by username. Same root cause as bug class D — "
        "fix lands separately."
    ),
}


# Match a single fetcher-style URL template:
#   `api/users/${EXPR}/<rest>`
# We capture the EXPR (between ``${`` and the first ``}``) and the
# trailing path. The trailing path may be empty (bare singleton) or
# multi-segment (``/sessions/${sid}/revoke``); for the cross-check we
# only care about the FIRST segment after the user-id slot.
_FETCH_URL_RE = re.compile(
    r"`api/users/\$\{([^}]+)\}(?:/([^/`$]+))?[^`]*`",
)


def _strip_encode_uri_component(expr: str) -> str:
    """Peel ``encodeURIComponent(...)`` off a TS expression.

    The inner expression is what actually carries the identity value —
    the encoder is just URL-escaping. Strips one layer; nested
    encoders are pathological and would fail the lookup downstream
    (which is the desired loud failure)."""
    expr = expr.strip()
    m = re.match(r"encodeURIComponent\((.*)\)$", expr)
    if m:
        return m.group(1).strip()
    return expr


class UiFetchBackendIdentityConsistencyRatchet(unittest.TestCase):
    """Every ``/api/users/{X}/<resource>`` URL constructed in the UI
    must interpolate a variable that holds the SAME identity field
    the backend's storage / audit layer keys ``<resource>`` on.

    Why a ratchet
    -------------
    Bug class D in v1.0.199: UI fetched
    ``/api/users/{user.id}/login-history`` (UUID), backend filtered
    audit events on ``target == username``. The shape mismatch is
    invisible at runtime (everything 200s with an empty list), so it
    survived multiple debug cycles. A static cross-check is the only
    cheap way to catch this class of drift before it ships.

    Scan strategy
    -------------
    For each TS file in :data:`_TS_FILES_TO_SCAN`:
      1. Regex-extract every ``api/users/${EXPR}/<resource>`` URL.
      2. Strip any ``encodeURIComponent(...)`` wrapper from EXPR.
      3. Look the bare expression up in :data:`TS_VARIABLE_TO_FIELD`
         (unknown expressions are flagged separately — extend the
         table when you add a new identity-bearing variable).
      4. Look the resource up in :data:`EXPECTED_ID_FIELD` (the
         backend's keying contract for that resource).
      5. Assert the two match. If not, the entry must be on
         :data:`KNOWN_DRIFT` with a documented domain TODO.
    """

    def test_ui_fetches_match_backend_identity_keying(self) -> None:
        violations: list[str] = []
        unknown_vars: list[str] = []
        unknown_resources: list[str] = []
        seen_any = False
        for path in _TS_FILES_TO_SCAN:
            if not path.is_file():
                self.fail(
                    f"Expected TS file missing: {path.relative_to(ROOT)}. "
                    f"Update _TS_FILES_TO_SCAN if the feature was renamed."
                )
            text = path.read_text(encoding="utf-8")
            rel = str(path.relative_to(ROOT))
            for m in _FETCH_URL_RE.finditer(text):
                seen_any = True
                full = m.group(0)
                raw_expr = m.group(1)
                resource = (m.group(2) or "").strip()
                bare_expr = _strip_encode_uri_component(raw_expr)
                ts_field = TS_VARIABLE_TO_FIELD.get(bare_expr)
                if ts_field is None:
                    # Not necessarily a violation — could be a freshly-
                    # added local. Surface separately so the table is
                    # extended deliberately, not silently.
                    unknown_vars.append(
                        f"{rel}: unknown TS variable {bare_expr!r} in "
                        f"{full}; add to TS_VARIABLE_TO_FIELD."
                    )
                    continue
                expected = EXPECTED_ID_FIELD.get(resource)
                if expected is None:
                    unknown_resources.append(
                        f"{rel}: unknown resource segment "
                        f"{resource!r} in {full}; add to "
                        f"EXPECTED_ID_FIELD."
                    )
                    continue
                if ts_field == expected:
                    continue
                # Use the un-encoded URL pattern (matches the KNOWN_DRIFT
                # key so domain drift entries are stable across encode
                # cosmetics).
                drift_key = (rel, _normalise_for_drift_key(full))
                if drift_key in KNOWN_DRIFT:
                    continue
                violations.append(
                    f"{rel}: UI fetches {full!r} interpolating "
                    f"{bare_expr!r} (holds {ts_field!r}), but backend "
                    f"keys /{resource}/ on {expected!r}. Either fix "
                    f"the UI to pass the matching identity, or add "
                    f"the pattern to KNOWN_DRIFT with a one-line TODO."
                )
        # Loud failure if the regex caught zero hits — most likely the
        # TS files were renamed or the URL prefix changed.
        self.assertTrue(
            seen_any,
            "No /api/users/{X}/... URL patterns matched in any "
            "scanned TS file. The ratchet's _FETCH_URL_RE or "
            "_TS_FILES_TO_SCAN list is out of date.",
        )
        self.assertFalse(
            violations,
            "UI fetch ↔ backend identity-field drift:\n  - "
            + "\n  - ".join(violations),
        )
        self.assertFalse(
            unknown_vars,
            "Unknown TS variables holding /api/users/ identity (extend "
            "TS_VARIABLE_TO_FIELD):\n  - " + "\n  - ".join(unknown_vars),
        )
        self.assertFalse(
            unknown_resources,
            "Unknown /api/users/{X}/<resource> segments (extend "
            "EXPECTED_ID_FIELD):\n  - " + "\n  - ".join(unknown_resources),
        )

    def test_known_drift_entries_have_documented_reasons(self) -> None:
        for key, reason in KNOWN_DRIFT.items():
            self.assertTrue(
                reason and len(reason) >= 20,
                f"KNOWN_DRIFT[{key!r}] must carry a substantive reason "
                f"and TODO note (>=20 chars); got {reason!r}.",
            )


def _normalise_for_drift_key(full_url_template: str) -> str:
    """Strip the surrounding backticks from a TS template literal so
    :data:`KNOWN_DRIFT` keys can be written without them."""
    s = full_url_template.strip()
    if s.startswith("`") and s.endswith("`"):
        s = s[1:-1]
    return s


if __name__ == "__main__":
    unittest.main()
