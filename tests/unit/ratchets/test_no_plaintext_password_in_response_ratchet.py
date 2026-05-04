"""Ratchet: no plaintext password ever lands in a response-shaped dict.

We AST-scan ``user_write_service.py`` and ``handlers_post.py`` for
dict literals / subscript assignments whose key matches the regex
``(?i)password`` AND whose value is the plaintext password variable
(by naming convention: ``password``, ``new_password``, ``plaintext``,
or any literal string). Approved sibling keys are allowlisted — the
stored hash, policy settings, history, and similar metadata are fine;
only the plaintext we care about.

Approved keys (NEVER match the ratchet):
  - ``password_set``       — boolean acknowledgement that the flow ran
  - ``password_hash``      — stored argon2 digest
  - ``password_ticket``    — one-time retrieval handle (NOT the secret)
  - ``password_history``   — list of prior hashes for replay protection
  - ``password_policy``    — policy config object

Disallowed (would trip the ratchet):
  - ``password``           — plaintext
  - ``generated_password`` — plaintext from a generator
  - ``plaintext_password`` — alias
  - ``new_password``       — plaintext from a reset
  - any key matching ``(?i)password`` not on the allowlist above

The check is conservative — it catches BOTH literal-assignment
patterns:
  ``d["generated_password"] = password``
and dict-literal patterns:
  ``return {"generated_password": password, ...}``
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

# ADR-0007 Phase E: handlers_post.py was deleted; the password-touching
# response surface lives in route + service modules. Each file below
# either reads/writes the plaintext password (route + service helpers)
# or hosts the canonical allowlisted shapes (password_policy_config).
#
# auth_password_tickets.py is intentionally NOT in this list — its
# entire purpose is the one-shot plaintext-retrieval handshake (the
# admin generates a ticket via POST and the operator GETs the
# ticket once to read the plaintext, after which the ticket is
# burned). The plaintext IS the response shape; flagging it would
# be a false positive. The same ticket flow was the only legitimate
# plaintext path under the legacy handlers_get.py too — and the
# original ratchet scanned only handlers_post.py, so it never saw
# the GET handler. Preserving that scope keeps semantics identical.
_TARGET_FILES: tuple[Path, ...] = (
    ROOT / "src" / "media_stack" / "core" / "auth" / "users" /
    "user_write_service.py",
    ROOT / "src" / "media_stack" / "api" / "routes" / "auth.py",
    ROOT / "src" / "media_stack" / "api" / "routes" / "post_users.py",
    ROOT / "src" / "media_stack" / "api" / "routes" / "post_user_resources.py",
    ROOT / "src" / "media_stack" / "api" / "routes" / "post_me.py",
    ROOT / "src" / "media_stack" / "api" / "routes" / "post_misc.py",
    ROOT / "src" / "media_stack" / "api" / "routes" / "users_get.py",
    ROOT / "src" / "media_stack" / "application" / "auth" / "users" / "bulk_ops.py",
)

_ALLOWED_KEYS: frozenset[str] = frozenset({
    "password_set",
    "password_hash",
    "password_ticket",
    "password_history",
    "password_policy",
    # "reset-password" is a ROUTE / ACTION name used as a dict key
    # in the endpoint dispatcher — not a password-VALUE key. Skipping
    # it avoids false positives on the URL-segment dispatch table.
    "reset-password",
    # "ticket_expires_at" is not on the regex but listed for clarity
    # in review (it's NOT a password — the regex won't match it).
})

_PASSWORD_KEY_RE = re.compile(r"password", re.IGNORECASE)


def _key_matches_password(key: str) -> bool:
    """Flag keys whose name contains 'password' and aren't allowlisted."""
    if key in _ALLOWED_KEYS:
        return False
    return bool(_PASSWORD_KEY_RE.search(key))


def _iter_string_keys(node: ast.AST):
    """Yield ``(key_str, value_node, lineno)`` for every dict-key write."""
    # Dict literals: ``{"k": v, ...}``
    for sub in ast.walk(node):
        if isinstance(sub, ast.Dict):
            for k_node, v_node in zip(sub.keys, sub.values):
                if isinstance(k_node, ast.Constant) and isinstance(k_node.value, str):
                    yield k_node.value, v_node, sub.lineno
        # Subscript assignment: ``d["k"] = v``
        if isinstance(sub, ast.Assign):
            for target in sub.targets:
                if isinstance(target, ast.Subscript):
                    slc = target.slice
                    # py3.9+: slice is the expression directly
                    if isinstance(slc, ast.Constant) and isinstance(slc.value, str):
                        yield slc.value, sub.value, sub.lineno


class NoPlaintextPasswordInResponseRatchet(unittest.TestCase):

    def test_no_plaintext_password_written_to_response_dicts(self) -> None:
        violations: list[str] = []
        scanned = 0
        for path in _TARGET_FILES:
            if not path.is_file():
                continue
            scanned += 1
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, str(path))
            for key_str, _value_node, lineno in _iter_string_keys(tree):
                if _key_matches_password(key_str):
                    violations.append(f"{path.name}:{lineno} key={key_str!r}")
        self.assertGreater(
            scanned, 0,
            "ratchet scanned no target files — _TARGET_FILES is "
            "stale. Update the path list.",
        )
        self.assertFalse(
            violations,
            "Plaintext-password keys found in response-shaped dicts "
            "(allowed keys: "
            f"{sorted(_ALLOWED_KEYS)}):\n  - "
            + "\n  - ".join(violations),
        )


class AllowlistShapeTests(unittest.TestCase):
    """Sanity: the allowlist entries don't match the regex trivially
    because the regex is literally 'password' — they would all match
    without the explicit skip. This asserts the ratchet CAN be
    triggered; a no-op regex would false-pass every run."""

    def test_allowed_entries_all_contain_password_substring(self) -> None:
        for key in _ALLOWED_KEYS:
            self.assertRegex(key, r"(?i)password")

    def test_regex_flags_a_known_disallowed_key(self) -> None:
        self.assertTrue(_key_matches_password("generated_password"))
        self.assertTrue(_key_matches_password("password"))
        self.assertTrue(_key_matches_password("new_password"))

    def test_regex_ignores_allowed_keys(self) -> None:
        for key in _ALLOWED_KEYS:
            self.assertFalse(
                _key_matches_password(key),
                f"allowlist key {key!r} incorrectly flagged",
            )


if __name__ == "__main__":
    unittest.main()
