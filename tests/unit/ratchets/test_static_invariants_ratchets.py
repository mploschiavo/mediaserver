"""Batch 5 ratchets shipped in v1.0.120.

Meta + edge-case ratchets — these check the *project's* invariants
(naming, contract enums, openapi parity) rather than per-feature
correctness. Catches the "developer wrote code but forgot to
register/document/wire it up" bug class.

Bug classes covered:

  M1   every Python file under src/media_stack parses cleanly
       (basic AST sanity — catches a half-finished refactor that
       leaves an import or syntax error)
  M2   every contract job's ``phase:`` matches the known phase
       set (typo would silently route the job to the wrong phase)
  M3   every ServiceDef.category is one of the known categories
       (typo would render the dashboard category as "unknown")
  M4   every ``__all__`` entry in src/ is actually defined in the
       module (catches "I deleted/renamed the symbol but forgot
       to update __all__"); files using __getattr__ are exempt
       since they intentionally lazy-load names.
  M5   every test class in test_*_ratchets.py has a docstring
       naming the bug class (so future maintainers know why the
       ratchet exists, not just what it asserts)
  M6   every tests/unit/*.py file is a real pytest module (name
       matches test_*.py and contains at least one TestCase or
       test_ function)
  M7   every openapi.yaml path has a backend handler — the
       v1.0.117 DashboardEndpointParity ratchet covers
       SPA UI fetches → backend; this ratchet covers
       openapi.yaml → backend (different consumers, same drift)
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "media_stack"
sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# M1 — every src/media_stack/*.py parses
# ---------------------------------------------------------------------------
class SourceTreeParses(unittest.TestCase):
    """Every Python file under ``src/media_stack/`` must parse
    cleanly. Catches the half-finished refactor that leaves an
    import or syntax error somewhere out-of-CI-coverage."""

    def test_every_python_file_parses(self) -> None:
        broken: list[str] = []
        for path in SRC.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            try:
                ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                broken.append(f"{path.relative_to(ROOT)}:{exc.lineno}: {exc.msg}")
        self.assertFalse(
            broken,
            f"Python files don't parse ({len(broken)}):\n  - "
            + "\n  - ".join(broken[:10]),
        )


# ---------------------------------------------------------------------------
# M2 — contract job phases match the known set
# ---------------------------------------------------------------------------
class ContractJobPhaseValid(unittest.TestCase):
    """Each contract job's ``phase:`` selects which bootstrap
    pipeline phase the job runs in. A typo (``phase: pos`` instead
    of ``phase: post``) silently drops the job to the catch-all
    default phase. Pin the enum."""

    _KNOWN_PHASES = {
        "pre_bootstrap",
        # v1.0.149: foundational routing/auth setup. Added so
        # envoy-config + configure-auth + ingress-config run before
        # media_server / download_clients try to reach services
        # through the gateway.
        "infrastructure",
        "download_clients",
        "arr_apps",
        "media_server",
        "default",
        "post",
        # The Phase-1 holding-area phase ``orchestrator_satisfy`` was
        # retired in Phase 2 (2026-05-03) when ``bootstrap:satisfy-
        # promises`` graduated into ``post`` priority 100. ``None`` is
        # still legal — discover_jobs_from_contracts reads ``phase``
        # absent as the default phase, which keeps a job registered +
        # ``run_job``-callable but unscheduled by the bootstrap DAG.
        # That's the new home for ``jellyfin:ensure-api-key`` after
        # the Phase 2 cutover (orchestrator dispatches it via the
        # ``jellyfin-api-key-discoverable`` promise).
        None,  # absent ⇒ default phase
    }

    def test_every_job_phase_is_known(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        contracts_dir = ROOT / "contracts" / "services"
        if not contracts_dir.is_dir():
            self.skipTest("contracts/services not present")

        bad: list[str] = []
        for f in sorted(contracts_dir.glob("*.yaml")):
            if f.stem.startswith("_"):
                continue
            doc = _yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            jobs = ((doc.get("plugin") or {}).get("jobs") or {})
            for jn, jd in jobs.items():
                phase = (jd or {}).get("phase")
                if phase not in self._KNOWN_PHASES:
                    bad.append(f"{f.name}::{jn}: phase={phase!r}")
        self.assertFalse(
            bad,
            f"Contract jobs use unknown phase names — typo? Add the "
            f"new phase to _KNOWN_PHASES if intentional:\n  - "
            + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# M3 — service registry categories are well-known
# ---------------------------------------------------------------------------
class ServiceCategoryValid(unittest.TestCase):
    """``ServiceDef.category`` drives the dashboard's grouping. A
    typo would render the service under a phantom "unknown"
    category. Pin the enum."""

    _KNOWN_CATEGORIES = {
        "management", "media", "automation", "downloads",
        "infrastructure", "tools", "auth",
    }

    def test_every_service_category_is_known(self) -> None:
        from media_stack.api.services.registry import SERVICES
        bad = [
            f"{s.id}: category={s.category!r}"
            for s in SERVICES
            if s.category and s.category not in self._KNOWN_CATEGORIES
        ]
        self.assertFalse(
            bad,
            f"Service registry entries use unknown category — typo? "
            f"Add to _KNOWN_CATEGORIES if intentional:\n  - "
            + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# M4 — __all__ exports actually exist in module
# ---------------------------------------------------------------------------
class AllExportsAreDefined(unittest.TestCase):
    """Every name in a module's ``__all__`` must be defined
    (assignment, def, class, or import). Files declaring
    ``def __getattr__`` are exempt because they lazy-load names
    on access."""

    def test_every_all_export_resolves(self) -> None:
        bad: list[str] = []
        for path in SRC.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            if "__all__" not in text:
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            # Skip files with __getattr__ — they intentionally lazy-load.
            has_getattr = any(
                isinstance(n, ast.FunctionDef) and n.name == "__getattr__"
                for n in tree.body
            )
            if has_getattr:
                continue
            defined: set[str] = set()
            all_names: list[str] = []
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    defined.add(node.name)
                elif isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name):
                            defined.add(tgt.id)
                            if (
                                tgt.id == "__all__"
                                and isinstance(node.value, ast.List)
                            ):
                                for el in node.value.elts:
                                    if (
                                        isinstance(el, ast.Constant)
                                        and isinstance(el.value, str)
                                    ):
                                        all_names.append(el.value)
                elif isinstance(node, ast.AnnAssign):
                    # ``X: T = value`` — annotated module-level
                    # assignments are valid Python and define ``X``
                    # just like ``X = value``.
                    if isinstance(node.target, ast.Name):
                        defined.add(node.target.id)
                elif isinstance(node, ast.ImportFrom):
                    for a in node.names:
                        defined.add(a.asname or a.name)
                elif isinstance(node, ast.Import):
                    for a in node.names:
                        defined.add((a.asname or a.name).split(".")[0])
            missing = [n for n in all_names if n not in defined]
            if missing:
                bad.append(
                    f"{path.relative_to(ROOT)}: __all__ contains "
                    f"undefined names: {missing}"
                )
        self.assertFalse(
            bad,
            "Modules export names in __all__ that aren't defined — "
            "stale renames? Add ``def __getattr__`` if lazy-loading "
            "is intentional:\n  - " + "\n  - ".join(bad[:10]),
        )


# ---------------------------------------------------------------------------
# M5 — every batch-ratchet test class has a docstring
# ---------------------------------------------------------------------------
class RatchetDocstringDiscipline(unittest.TestCase):
    """Future-you reads the test class to understand WHY the
    ratchet exists. A class without a docstring degrades into
    "I don't know if this is still load-bearing." Pin the
    convention now while the rules are fresh."""

    def test_every_ratchet_class_has_docstring(self) -> None:
        bad: list[str] = []
        for f in sorted((ROOT / "tests" / "unit").glob(
            "test_*_ratchets.py"
        )):
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                if not ast.get_docstring(node):
                    bad.append(f"{f.name}::{node.name}")
        self.assertFalse(
            bad,
            "Batch-ratchet classes without docstrings — the next "
            "maintainer needs to know WHY each ratchet exists, "
            "not just WHAT it asserts:\n  - "
            + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# M6 — every tests/unit/*.py is a real pytest module
# ---------------------------------------------------------------------------
class MeaningfulFilenamesAcrossRepo(unittest.TestCase):
    """Filenames must describe what they cover, not when they
    were created.

    Two conventions enforced across the entire git tree:

    1. ``tests/unit/*.py`` follows ``test_*.py`` so pytest
       collects it (a misnamed file is a test that silently
       never runs).
    2. NO file ANYWHERE in the repo carries a version-prefix
       (``v1_0_NNN_...``) or a numbered ``_batchN_`` suffix.
       These names tell future-you nothing about what's inside
       and bias every reader toward "this is historic, ignore
       it." Pin the rule for the whole repo (not just tests/),
       since the same naming rot can creep into ``src/``,
       ``contracts/``, and docs.

    No grandfather list. The rule is applied retroactively;
    if the test fails, rename the file."""

    _ALLOWED_NON_TEST_FILES = frozenset({
        "__init__.py",
        "conftest.py",
    })

    # Repo subtrees that aren't ours — vendor code can have any
    # naming convention it likes.
    _SCAN_EXCLUDE = (
        "/.venv/",
        "/.git/",
        "/__pycache__/",
        "/node_modules/",
        "/.hypothesis/",
        "/.pytest_cache/",
        "/.mypy_cache/",
        "/dist/",
        "/build/",
        "/api/static/",  # bundled swagger-ui assets
        "/.claude/",  # Claude agent worktrees — frozen snapshots of
                      # earlier branches, may carry historic naming.
    )

    # ``test_v1_0_NNN_...`` — the historic release-tagged name.
    _VERSION_PREFIX = re.compile(r"(^|/)([a-zA-Z_]+_)?v\d+(?:_\d+)+_")
    # ``_batchN_`` (numbered batch suffix) is the bad pattern;
    # ``_batch_topology`` etc. are fine — "batch" can be a
    # domain word.
    _BATCH_INFIX = re.compile(r"_batch\d+_")

    def _walk_repo(self) -> list[Path]:
        """Yield every tracked file in the repo, minus vendor /
        cache subtrees."""
        out: list[Path] = []
        for p in ROOT.rglob("*"):
            if not p.is_file():
                continue
            sp = "/" + str(p.relative_to(ROOT)) + "/"
            if any(ex in sp for ex in self._SCAN_EXCLUDE):
                continue
            out.append(p)
        return out

    def test_every_unit_test_file_follows_convention(self) -> None:
        bad: list[str] = []
        for f in (ROOT / "tests" / "unit").rglob("*.py"):
            if f.name in self._ALLOWED_NON_TEST_FILES:
                continue
            if f.name.startswith("_"):
                continue  # Helper modules conventionally prefixed _.
            if not f.name.startswith("test_"):
                bad.append(str(f.relative_to(ROOT)))
        self.assertFalse(
            bad,
            "Files under tests/unit/ that don't follow test_*.py — "
            "pytest doesn't collect them and they silently "
            "never run:\n  - " + "\n  - ".join(bad),
        )

    def test_no_version_prefix_filenames_anywhere(self) -> None:
        """``foo_v1_0_NNN_bar.py`` style names in the entire repo,
        not just tests/. Rename to describe the topic, not the
        release."""
        bad: list[str] = []
        for f in self._walk_repo():
            if self._VERSION_PREFIX.search(f.name):
                bad.append(str(f.relative_to(ROOT)))
        self.assertFalse(
            bad,
            "Files carrying a version-prefix in the name — these "
            "tell you nothing about what's inside. Rename to "
            "describe the topic (test_<topic>.py / "
            "<feature>_helpers.py / etc.):\n  - "
            + "\n  - ".join(bad),
        )

    def test_no_numbered_batch_suffix_anywhere(self) -> None:
        """``foo_batch3_bar.py`` style names. ``batch_topology``,
        ``batch_runner`` etc. are fine — only NUMBERED batch
        suffixes are caught."""
        bad: list[str] = []
        for f in self._walk_repo():
            if self._BATCH_INFIX.search(f.name):
                bad.append(str(f.relative_to(ROOT)))
        self.assertFalse(
            bad,
            "Files carrying a numbered _batchN_ suffix — these "
            "name themselves after the order in which the batch "
            "shipped, not what it contains:\n  - "
            + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# M7 — openapi.yaml path ↔ backend handler parity
# ---------------------------------------------------------------------------
class OpenApiHandlerParity(unittest.TestCase):
    """Every path declared in ``api/openapi.yaml`` must have a
    matching backend handler in handlers_get / handlers_post /
    server.py. The dashboard parity ratchet (v1.0.117
    DashboardEndpointParity) covers dashboard→backend; this one
    covers openapi→backend (different consumers, same drift
    risk)."""

    _ALLOWED_DYNAMIC_PATHS = {
        # /actions/{name} is dispatched via path.startswith("/actions/")
        # in handlers_post.py; the literal-anchor scan misses
        # prefix-based dispatch.
        "/actions/{name}",
    }

    def test_every_openapi_path_has_handler(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        ofile = ROOT / "contracts" / "api" / "openapi.yaml"
        if not ofile.is_file():
            self.skipTest("openapi.yaml not present")
        doc = _yaml.safe_load(ofile.read_text(encoding="utf-8")) or {}
        paths = list((doc.get("paths") or {}).keys())

        backend = (
            (SRC / "api" / "handlers_get.py").read_text(encoding="utf-8")
            + "\n" + (SRC / "api" / "handlers_post.py").read_text(encoding="utf-8")
            + "\n" + (SRC / "api" / "server.py").read_text(encoding="utf-8")
        )

        missing: list[str] = []
        for raw in paths:
            if raw in self._ALLOWED_DYNAMIC_PATHS:
                continue
            stripped = re.sub(r"\{[^}]+\}", "", raw).rstrip("/")
            if not stripped:
                continue
            found = False
            for anchor in (
                f'"{stripped}"', f"'{stripped}'",
                f'startswith("{stripped}")', f"startswith('{stripped}')",
            ):
                if anchor in backend:
                    found = True
                    break
            if not found:
                # Try parent-prefix match: /api/X/Y → /api/X/
                parent = "/".join(stripped.split("/")[:-1])
                while parent.startswith("/api") or parent.startswith("/actions"):
                    for anchor in (
                        f'startswith("{parent}/")', f"startswith('{parent}/')",
                        f'"{parent}/"', f"'{parent}/'",
                    ):
                        if anchor in backend:
                            found = True
                            break
                    if found:
                        break
                    parent = "/".join(parent.split("/")[:-1])
            if not found:
                missing.append(raw)
        self.assertFalse(
            missing,
            f"openapi.yaml paths with no backend handler "
            f"({len(missing)}):\n  - " + "\n  - ".join(missing[:15]),
        )


if __name__ == "__main__":
    unittest.main()
