"""Ratchet: keep Authelia as one pluggable auth backend, not a
hardcoded dependency.

The goal is that swapping Authelia for Authentik (or Keycloak, or
any future backend) requires NO edits to ``core/`` or ``api/`` —
only adding a new provider under ``services/apps/<backend>/``.
Today that isn't quite true: historical code in ``core/auth/`` knows
about Authelia specifically. This ratchet records the current
violations and prevents NEW ones from landing.

What it scans
-------------
Every ``.py`` file under ``src/media_stack/``:

- ``from media_stack.services.apps.authelia ...``
- ``import media_stack.services.apps.authelia ...``
- Any module name or symbol containing "authelia" being imported
  (catches ``from ... import AutheliaFoo`` even if the module path
  is neutral).

Excluded from the scan:
- Files inside ``src/media_stack/services/apps/authelia/`` —
  authelia-specific code is allowed to import its own siblings.
- Files on the allowlist below, each with a documented reason.

The allowlist is the accepted-debt ledger. It may only shrink over
time — the ratchet fails if a NEW file joins the violator set
without being added to the allowlist.

CIA / AAA alignment
-------------------
- **Confidentiality**: a pluggable auth layer means the identity
  provider is swappable when its cryptographic properties change
  (e.g. Authelia moves from session cookies to OIDC tokens).
- **Authentication**: the authn provider is policy, not protocol.
  A hard dependency prevents clean rotation.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "media_stack"


# Files that are ALLOWED to import Authelia-specific modules or
# symbols. Each entry is paired with a reason — a reviewer should
# be able to read the file and agree the dependency is unavoidable
# today. Empty today EXCEPT for intra-``core/auth/`` references
# between the Authelia config generator and the OIDC crypto helper,
# which form a tight coupled pair that predates the pluggable
# protocol split.
#
# Every other file that ``mentions`` Authelia does so only via
# string identifiers ("authelia" as a service key or URL token),
# not via imports — the ratchet correctly ignores those.
_ALLOWED_AUTHELIA_AWARE: frozenset[str] = frozenset({
    # Authelia config generator — owns the YAML layout of Authelia's
    # configuration.yml and users_database.yml. Imports sibling
    # authelia_oidc_crypto for HS/RS key generation. Can't be
    # abstracted without designing a generic multi-backend config
    # surface (tracked in docs/roadmap/session-visibility-followups.md).
    "core/auth/authelia_config_generator.py",
    # Configure-auth bootstrap job — invokes both the generator and
    # the crypto helper above.
    "core/auth/configure_auth_job.py",
})


def _imports_authelia(tree: ast.AST) -> list[str]:
    """Return a list of Authelia-specific import descriptions from
    ``tree``. Empty means the file is clean.
    """
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if "authelia" in mod.lower():
                hits.append(f"from {mod} import ...")
            else:
                for alias in node.names:
                    if "authelia" in alias.name.lower():
                        hits.append(
                            f"from {mod} import {alias.name}",
                        )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "authelia" in alias.name.lower():
                    hits.append(f"import {alias.name}")
    return hits


def _is_authelia_owned(rel_path: str) -> bool:
    """Files under services/apps/authelia/ are Authelia's own home
    and may freely import siblings."""
    return rel_path.startswith("services/apps/authelia/")


def _scan_source() -> dict[str, list[str]]:
    """Return {rel_path: [hits]} for every .py in src/ that imports
    something Authelia-specific."""
    violations: dict[str, list[str]] = {}
    for py in sorted(SRC.rglob("*.py")):
        if "__pycache__" in str(py):
            continue
        rel = str(py.relative_to(SRC))
        if _is_authelia_owned(rel):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), str(py))
        except SyntaxError:
            continue
        hits = _imports_authelia(tree)
        if hits:
            violations[rel] = hits
    return violations


class PluggableAutheliaRatchet(unittest.TestCase):

    def test_no_unexpected_authelia_imports(self) -> None:
        violations = _scan_source()
        unexpected = {
            path: hits for path, hits in violations.items()
            if path not in _ALLOWED_AUTHELIA_AWARE
        }
        self.assertFalse(
            unexpected,
            "New files are importing Authelia-specific code. "
            "Either refactor to use the neutral UserProvider / "
            "AccountStateProvider / SessionAdminProvider protocol, "
            "or add the file to _ALLOWED_AUTHELIA_AWARE with a "
            "documented reason.\n\nViolators:\n" + "\n".join(
                f"  {path}:\n    " + "\n    ".join(hits)
                for path, hits in unexpected.items()
            ),
        )

    def test_allowlist_only_references_real_files(self) -> None:
        """Prevent stale allowlist entries after a file rename or
        delete — a stale entry is dead code."""
        for rel in _ALLOWED_AUTHELIA_AWARE:
            full = SRC / rel
            self.assertTrue(
                full.is_file(),
                f"_ALLOWED_AUTHELIA_AWARE references non-existent "
                f"file: {rel}",
            )

    def test_every_allowlisted_file_actually_imports_authelia(
        self,
    ) -> None:
        """An allowlist entry that DOESN'T have an Authelia import
        can be removed — the file's dependency was refactored away.
        Catching this keeps the allowlist honest."""
        violations = _scan_source()
        removable: list[str] = []
        for rel in _ALLOWED_AUTHELIA_AWARE:
            if rel not in violations:
                removable.append(rel)
        self.assertFalse(
            removable,
            "These allowlisted files no longer import anything "
            "Authelia-specific — remove them from the allowlist "
            "so it reflects real accepted debt:\n  - "
            + "\n  - ".join(removable),
        )


class PluggableAutheliaScanHelperTests(unittest.TestCase):
    """Unit tests for the AST scanner — the ratchet rests on these."""

    def test_detects_from_module_import(self) -> None:
        tree = ast.parse(
            "from media_stack.services.apps.authelia.user_provider "
            "import AutheliaFileProvider"
        )
        self.assertTrue(_imports_authelia(tree))

    def test_detects_bare_import(self) -> None:
        tree = ast.parse(
            "import media_stack.services.apps.authelia.user_provider"
        )
        self.assertTrue(_imports_authelia(tree))

    def test_detects_symbol_with_authelia_name(self) -> None:
        # Module path is neutral but the imported NAME has authelia.
        tree = ast.parse(
            "from somewhere_else import AutheliaFooHelper"
        )
        self.assertTrue(_imports_authelia(tree))

    def test_neutral_code_is_clean(self) -> None:
        tree = ast.parse("from pathlib import Path\nimport json")
        self.assertFalse(_imports_authelia(tree))

    def test_string_literal_not_a_hit(self) -> None:
        # Using the string "authelia" as an identifier value is
        # not an import; the ratchet only scans imports.
        tree = ast.parse(
            "PROVIDER_NAME = 'authelia'\n"
            "def x(): return 'authelia'"
        )
        self.assertFalse(_imports_authelia(tree))


if __name__ == "__main__":
    unittest.main()
