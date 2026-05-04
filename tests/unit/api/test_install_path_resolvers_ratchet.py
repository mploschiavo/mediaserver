"""Ratchet: every controller-side path-candidate resolver MUST cover
the four deploy layouts (source dev, wheel install-root, wheel
shared-data, legacy bind-mount).

Why this ratchet exists: the same bug class has bitten three times now.

  * v1.0.231 — media-integrity factory looked up
    ``servarr-policy.yaml`` only at the source-tree path and
    ``/contracts/`` (legacy). The wheel image moved contracts to
    ``/opt/media-stack/contracts/`` and the subsystem silently
    disabled at boot ("media-integrity service not configured" with
    no obvious cause). Fixed by adding ``_CONTRACT_PATH_INSTALL`` to
    the candidate list.

  * v1.0.235 — ``_OPENAPI_YAML_PATH`` in handlers_get.py used
    ``parents[3]`` only. From the wheel's
    ``site-packages/media_stack/api/handlers_get.py`` that resolves
    to ``/usr/local/lib/python3.12/contracts/api/openapi.yaml`` —
    a directory that doesn't exist. So ``GET /api/openapi.json``
    fell through to a legacy 50-endpoint stub and the api-docs page
    rendered empty. Fixed by adding the four-path candidate list.

  * Wave 6 candidate — every other path-from-source-tree lookup is
    a latent landmine. This ratchet enforces the contract: every
    ``_resolve_*_path()`` / ``_*_PATH_CANDIDATES`` site MUST cover
    install-root + shared-data + legacy bind-mount.

The ratchet uses static-analysis: walk all known resolver functions
in the codebase, assert their candidate tuple includes the
install-root path. New resolvers must register themselves here so we
have one place that says "resolvers known to this ratchet".
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


class InstallPathResolversRatchet(unittest.TestCase):
    # Every (module, candidates_attr) pair the controller relies on
    # to find a runtime config / spec / contract YAML across the
    # four deploy layouts. A new resolver MUST add itself here AND
    # cover at minimum: source dev, /opt/media-stack/, and one of
    # legacy or shared-data path.
    _RESOLVERS: tuple[tuple[str, str], ...] = (
        (
            "media_stack.services.media_integrity.policy",
            "_CONTRACT_PATH_CANDIDATES",
        ),
        (
            "media_stack.api.services.openapi",
            "_OPENAPI_PATH_CANDIDATES",
        ),
    )

    # Substrings that MUST appear in the candidate-list source for the
    # ratchet to consider it complete.
    _REQUIRED_LAYOUT_HINTS = (
        "/opt/media-stack/",  # install-root layout
    )

    def test_every_known_resolver_covers_install_root(self) -> None:
        import importlib

        for module_name, attr in self._RESOLVERS:
            mod = importlib.import_module(module_name)
            self.assertTrue(
                hasattr(mod, attr),
                f"{module_name}.{attr} missing — resolver removed without "
                f"updating the ratchet",
            )
            candidates = getattr(mod, attr)
            paths = [str(p) for p in candidates]
            joined = "\n".join(paths)
            for hint in self._REQUIRED_LAYOUT_HINTS:
                self.assertIn(
                    hint, joined,
                    f"{module_name}.{attr} missing layout hint {hint!r}; "
                    f"current candidates:\n  " + "\n  ".join(paths),
                )

    def test_resolver_registry_doesnt_shrink(self) -> None:
        """Floor: this ratchet covers at least 2 resolvers today.
        Bumps up as new ones are added — never down. Catches the
        "delete a resolver from the ratchet to silence a failure"
        anti-pattern."""
        self.assertGreaterEqual(
            len(self._RESOLVERS), 2,
            f"Resolver-registry shrank below floor 2; either the "
            f"controller has fewer path-resolvers (unlikely) or "
            f"someone removed a registry entry without justification.",
        )

    def test_no_new_naive_parents_n_resolvers(self) -> None:
        """Source-grep: any new module-scope resolver pattern that
        uses ONLY ``parents[N]`` to reach contracts MUST be added to
        ``_RESOLVERS`` with a candidate list that includes
        ``/opt/media-stack/``. Catches the next "this works in dev
        but fails in the wheel image" regression at lint time."""
        # Walk src/media_stack/, find module-scope assignments to a
        # PATH-suffixed name reading from parents[N] / contracts. If
        # the same module also has a candidate-list for that path,
        # we're fine. If not, flag.
        import re
        src = ROOT / "src" / "media_stack"
        bad: list[str] = []
        # Match: NAME = Path(__file__).resolve().parents[N] / "contracts" / ...
        # Only flag when N >= 2 — parents[1] reaches the package
        # internals (`src/media_stack/`) which the wheel ships
        # bundled, so a self-resolving package-internal lookup is
        # safe across deploy modes. parents[2]+ reaches the repo
        # root, which the wheel does NOT ship intact — that's the
        # bug class.
        pat = re.compile(
            r'^([A-Z_][A-Z0-9_]*_PATH)\s*=\s*[(\s]*Path\(__file__\).*parents\[([2-9]|\d{2,})\].*contracts',
            re.M,
        )
        for f in src.rglob("*.py"):
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                continue
            for m in pat.finditer(text):
                name = m.group(1)
                # Allowed if the same module also defines a
                # NAME_CANDIDATES tuple (the ratchet pattern).
                if f"{name}_CANDIDATES" in text or "_PATH_CANDIDATES" in text:
                    continue
                rel = f.relative_to(ROOT)
                bad.append(f"{rel} :: {name}")
        if bad:
            self.fail(
                "Found path resolver(s) using bare parents[N] without a "
                "candidate list — this works in source-tree dev but FAILS "
                "in the wheel image (see v1.0.231 + v1.0.235 incidents):\n  "
                + "\n  ".join(bad)
            )


if __name__ == "__main__":
    unittest.main()
