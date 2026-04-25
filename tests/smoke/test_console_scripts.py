"""Phase 12-F smoke gate — every ``[project.scripts]`` entry-point
imports cleanly and (where applicable) responds to ``--help``.

Why this exists
---------------

The v1.0.193 Phase 12-C ENTRYPOINT cutover (see
``docs/architecture/adr/0001-repo-restructure.md``) made the
controller image invoke ``media-stack-controller`` directly rather
than ``python bin/controller.py``. If a console-script's import
target drifts from the actual module layout (e.g. someone moves
``cli/commands/install_main.py`` under ``services/apps/stack/cli/``
without updating ``pyproject.toml``), the entry-point points at a
non-existent module and the container crash-loops at startup with
``ModuleNotFoundError``. That regression is invisible to ``pytest``
(which can resolve everything via ``pythonpath = ["src", "."]``)
and to ``ruff``/``mypy`` (no static check verifies entry-point
targets). It only surfaces in production.

This module locks the contract two ways:

1. **Resolution gate (always runs).** For every entry under
   ``[project.scripts]`` in ``pyproject.toml``, import the named
   module and assert that the named callable exists. A missing
   module or attribute fails the test loudly with the exact
   ``name = module:attr`` from pyproject so the fix is one edit.

2. **`--help` smoke gate (argparse-aware).** For every entry whose
   target module uses ``argparse.ArgumentParser`` (auto-detected),
   invoke ``<script-name> --help`` via subprocess and assert exit
   code 0. Catches:
     - The script being on ``$PATH`` after ``pip install -e .``.
     - The argparse parser being constructable without crashing
       (e.g. a dynamically-built ``choices=`` list isn't empty,
       a ``default=`` doesn't error on missing env, etc.).
     - The module's top-level imports + module-scope code don't
       have side-effects that explode pre-argparse.

   Entries whose module has no ``argparse`` and instead reads
   ``sys.argv`` positionally (or ignores it) are listed in
   ``_NON_ARGPARSE_ALLOWLIST`` and skipped from ``--help``
   invocation only — they still go through the resolution gate.

Local invocation
----------------

    pip install -e .
    python -m pytest tests/smoke/test_console_scripts.py -v

Or via the existing smoke marker:

    python -m pytest -m smoke tests/smoke/test_console_scripts.py

CI invocation lives in ``.github/workflows/ci.yml`` under the
``console-scripts-smoke`` job.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Console-scripts whose target module does NOT use argparse and
# would therefore TREAT ``--help`` as a positional argument (or
# silently ignore it and run for real). The resolution gate still
# runs against these — only the ``--help`` subprocess invocation is
# skipped.
#
# When you add a new entry-point, prefer wiring it through
# ``argparse.ArgumentParser`` so this allowlist stays empty-ish.
# Drift here is fine; the resolution gate is the load-bearing one.
_NON_ARGPARSE_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Positional sys.argv parsing only. See module:
        #   src/media_stack/services/jobs/bootstrap_config_generator.py
        "media-stack-generate-bootstrap-config",
        # Ignores argv entirely; renders to a fixed path. See:
        #   src/media_stack/cli/commands/render_promises_reference.py
        "media-stack-render-promises",
    }
)


def _load_console_scripts() -> dict[str, str]:
    """Return ``{script_name: "module:attr"}`` from
    ``[project.scripts]`` in ``pyproject.toml``."""
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    project = data.get("project") or {}
    scripts = project.get("scripts") or {}
    if not scripts:
        pytest.fail(
            f"No [project.scripts] entries found in {PYPROJECT}. "
            "Phase 12 wired ~26 console-scripts; if they all "
            "vanished, something went very wrong."
        )
    return dict(scripts)


# Materialize once at import time — pytest parametrize needs it
# eagerly. Sorted for deterministic test ordering / output.
_CONSOLE_SCRIPTS: list[tuple[str, str]] = sorted(_load_console_scripts().items())


def _split_target(target: str) -> tuple[str, str]:
    if ":" not in target:
        pytest.fail(
            f"Malformed entry-point target {target!r}: "
            "expected 'module.path:callable'."
        )
    module_path, attr = target.rsplit(":", 1)
    return module_path, attr


@pytest.mark.parametrize(
    ("script_name", "target"),
    _CONSOLE_SCRIPTS,
    ids=[name for name, _ in _CONSOLE_SCRIPTS],
)
def test_console_script_resolves(script_name: str, target: str) -> None:
    """Every entry under ``[project.scripts]`` must import and
    expose its declared callable.

    This is the load-bearing assertion: it catches the regression
    where a Python file moves and ``pyproject.toml`` isn't updated.
    """
    module_path, attr = _split_target(target)
    try:
        module = importlib.import_module(module_path)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"console-script {script_name!r} declares target "
            f"{target!r} but importing module {module_path!r} "
            f"raised {type(exc).__name__}: {exc}\n"
            "Either the module moved (update pyproject.toml) or "
            "an import-time side-effect is broken (fix the module)."
        )
    if not hasattr(module, attr):
        pytest.fail(
            f"console-script {script_name!r} declares target "
            f"{target!r} but module {module_path!r} has no "
            f"attribute {attr!r}.\n"
            "Either the callable was renamed (update pyproject.toml) "
            "or it was deleted (restore it or drop the entry)."
        )
    callable_obj = getattr(module, attr)
    assert callable(callable_obj), (
        f"console-script {script_name!r} target {target!r} resolves "
        f"to {callable_obj!r}, which is not callable. The convention "
        "is `def main(...)` or `main = _instance.main`."
    )


def _module_uses_argparse(module_path: str) -> bool:
    """Return True if the module's source file references
    ``argparse``. Used to decide whether ``--help`` is meaningful
    for a given entry-point."""
    try:
        module = importlib.import_module(module_path)
    except Exception:  # noqa: BLE001
        # Resolution test will catch this — say "no" so we don't
        # try to subprocess a broken module on top of the import
        # failure.
        return False
    src_file = getattr(module, "__file__", None)
    if not src_file:
        return False
    try:
        text = Path(src_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "argparse" in text


@pytest.mark.parametrize(
    ("script_name", "target"),
    _CONSOLE_SCRIPTS,
    ids=[name for name, _ in _CONSOLE_SCRIPTS],
)
def test_console_script_help_exits_zero(script_name: str, target: str) -> None:
    """Every argparse-using entry-point responds to ``--help`` with
    exit code 0.

    Skipped when:
      - the script is in ``_NON_ARGPARSE_ALLOWLIST``;
      - the package isn't installed (no console-script binary on
        ``$PATH``) — this test is meaningful only after ``pip
        install -e .`` has run, which CI does and ``bin/test.sh``
        is starting to do.
    """
    if script_name in _NON_ARGPARSE_ALLOWLIST:
        pytest.skip(
            f"{script_name} target does not use argparse; "
            "covered by resolution test only."
        )
    module_path, _attr = _split_target(target)
    if not _module_uses_argparse(module_path):
        pytest.skip(
            f"{script_name} module {module_path} has no argparse "
            "usage — would run for real on --help. Add to "
            "_NON_ARGPARSE_ALLOWLIST or wire argparse."
        )
    binary = shutil.which(script_name)
    if binary is None:
        pytest.skip(
            f"{script_name} not on PATH — run `pip install -e .` "
            "before this test (CI does)."
        )
    proc = subprocess.run(
        [binary, "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"{script_name} --help exited {proc.returncode}.\n"
        f"stdout (first 500 chars): {proc.stdout[:500]!r}\n"
        f"stderr (first 500 chars): {proc.stderr[:500]!r}"
    )
    # argparse always emits "usage:" on --help. Soft check; if a
    # script switches off argparse without updating the allowlist,
    # this catches it.
    combined = (proc.stdout + proc.stderr).lower()
    assert "usage" in combined or "options" in combined, (
        f"{script_name} --help exited 0 but produced no usage/help "
        f"text. stdout={proc.stdout[:200]!r} stderr={proc.stderr[:200]!r}"
    )


def test_pyproject_scripts_block_is_nonempty() -> None:
    """Sanity: pyproject.toml has a non-empty ``[project.scripts]``
    block. Guards against accidental wholesale deletion that the
    parametrized tests above would silently treat as 'zero work'."""
    scripts = _load_console_scripts()
    # Phase 12 wired 26 entries; any drop below 20 deserves a
    # second look before the test rubber-stamps it.
    assert len(scripts) >= 20, (
        f"Only {len(scripts)} console-scripts declared "
        "in pyproject.toml — Phase 12 had ~26. Did entries get "
        "deleted accidentally?"
    )


def test_python_version_supports_tomllib() -> None:
    """``tomllib`` is stdlib in 3.11+; ``pyproject.toml`` declares
    ``requires-python = ">=3.11"``. Belt-and-braces guard so a
    reader of this file isn't surprised by the import."""
    assert sys.version_info >= (3, 11), (
        f"This smoke gate uses stdlib tomllib (Python 3.11+); "
        f"running on {sys.version_info}."
    )
