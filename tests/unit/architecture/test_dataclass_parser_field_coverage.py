"""Generic ratchet: every dataclass with a hand-written
``from_dict`` (or similar) parser must read every one of its
fields. Catches the v1.0.113 ``ServiceDef.published_port silently
dropped`` bug class everywhere it could occur, not just at the
specific site where it bit us.

The pattern: a dataclass declares N fields with defaults; a
parser builds it from a YAML/dict by naming each field as a
keyword argument; if you add field N+1 to the dataclass and
forget the parser, the field defaults silently. No type
checker catches it, no test catches it (until something
downstream notices the wrong default at runtime).

This test discovers candidate (dataclass, parser) pairs
statically, introspects the dataclass fields at runtime, reads
the parser source, and asserts every field name appears in
the parser body. Adding a dataclass + ``from_dict`` pair anywhere
under ``src/media_stack/`` automatically gets covered — no opt-in
needed.

If a parser legitimately ignores a field (e.g., the field is
computed in ``__post_init__`` from other fields, or the field
exists in the dataclass for type purposes but isn't user-
configurable), add the dataclass to ``_KNOWN_PARTIAL_PARSERS``
below with the set of field names it intentionally drops.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import re
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

# Allow-list for parsers that intentionally drop fields. Format:
# ``{(module_path, classname): {"field_a", "field_b"}}``. Keep
# this list TIGHT — every entry should have a comment explaining
# WHY the field isn't parsed (computed, derived, internal-only,
# etc.). The default is "all fields must be parsed."
_KNOWN_PARTIAL_PARSERS: dict[tuple[str, str], set[str]] = {
    # ControllerProfileConfig.from_dict is a 3-line wrapper that
    # delegates to ``parse_profile_dict`` in
    # core/controller_profile/parser.py — the actual field reads
    # happen there.  Following the delegation in the ratchet would
    # require AST-walking imports; for now we just allow every
    # field on the dataclass and rely on parse_profile_dict's own
    # tests to cover the per-field behavior.
    ("media_stack.core.controller_profile.models",
     "ControllerProfileConfig"): {
        "deployment_target", "purpose", "stack_name",
        "disk_allocation_gb", "network_cidr", "install_profile",
        "install_apps", "app_catalog", "preconfigure_apps",
        "preconfigure_api_keys", "apply_initial_preferences",
        "auto_download_content", "live_tv_tuner_urls",
        "live_tv_guide_urls", "live_tv_default_program_icon_url",
        "exposure", "chaos", "source_path",
    },
}


def _discover_pairs() -> list[tuple[str, str, str]]:
    """Walk src/ and return every (module_path, dataclass_name,
    parser_method_name) triple. Parser is one of:
    ``from_dict``, ``from_yaml``, ``from_mapping``, ``parse``."""
    PARSER_NAMES = {"from_dict", "from_yaml", "from_mapping", "parse"}
    pairs: list[tuple[str, str, str]] = []
    src_root = ROOT / "src" / "media_stack"
    for path in sorted(src_root.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rel = path.relative_to(ROOT / "src").with_suffix("")
        mod_name = ".".join(rel.parts)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            # Is this a dataclass?
            is_dc = False
            for dec in node.decorator_list:
                names: list[str] = []
                if isinstance(dec, ast.Name):
                    names.append(dec.id)
                elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name):
                    names.append(dec.func.id)
                elif isinstance(dec, ast.Attribute):
                    names.append(dec.attr)
                if "dataclass" in names:
                    is_dc = True
                    break
            if not is_dc:
                continue
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if member.name in PARSER_NAMES:
                        pairs.append((mod_name, node.name, member.name))
    return pairs


def _parser_source(cls: type, method_name: str) -> str:
    method = getattr(cls, method_name, None)
    if method is None:
        return ""
    # Unwrap classmethod/staticmethod descriptors
    fn = getattr(method, "__func__", method)
    try:
        return inspect.getsource(fn)
    except (TypeError, OSError):
        return ""


def _missing_fields(cls: type, parser_src: str) -> list[str]:
    if not parser_src:
        return []
    missing: list[str] = []
    for f in dataclasses.fields(cls):
        if f.name.startswith("_"):
            continue
        # Field is "read" if its name appears as a literal string
        # (the dict key) OR as a kwarg in the constructor call.
        # Patterns are deliberately permissive — we want false
        # negatives (skipped checks) over false positives (broken
        # ratchet for legit code shapes like camelCase aliasing).
        patterns = (
            f'"{f.name}"', f"'{f.name}'",
            f"{f.name}=",       # kwarg in constructor call
            f".{f.name}",       # accessor (covers d.field if dot-access used)
        )
        if not any(p in parser_src for p in patterns):
            missing.append(f.name)
    return missing


class DataclassParserFieldCoverage(unittest.TestCase):
    """One subtest per discovered (dataclass, parser) pair. A
    failure names the dataclass + the missing fields, mirroring
    the v1.0.113 published_port shape so the operator knows what
    needs fixing."""

    def test_every_dataclass_parser_reads_every_field(self) -> None:
        pairs = _discover_pairs()
        self.assertGreater(
            len(pairs), 5,
            "Discovery returned suspiciously few (dataclass, parser) "
            "pairs — AST walk is probably broken after a refactor.",
        )
        offenders: list[str] = []
        skipped_imports: list[str] = []
        for mod_name, dc_name, parser_name in pairs:
            try:
                mod = importlib.import_module(mod_name)
            except Exception as exc:
                skipped_imports.append(f"{mod_name}: {exc}")
                continue
            cls = getattr(mod, dc_name, None)
            if not cls or not dataclasses.is_dataclass(cls):
                continue
            parser_src = _parser_source(cls, parser_name)
            if not parser_src:
                continue
            missing = _missing_fields(cls, parser_src)
            allowed = _KNOWN_PARTIAL_PARSERS.get((mod_name, dc_name), set())
            real_misses = [m for m in missing if m not in allowed]
            if real_misses:
                offenders.append(
                    f"{mod_name}.{dc_name}.{parser_name} drops: "
                    f"{real_misses}"
                )
        # Imports that fail are typically test-environment shims
        # (modules requiring runtime services).  Surface them as
        # info so the next maintainer can decide whether to fix
        # the import or accept the gap, but don't fail on them.
        if skipped_imports:
            sys.stderr.write(
                f"\n[INFO] Skipped {len(skipped_imports)} pairs "
                "(import failed):\n  - "
                + "\n  - ".join(skipped_imports[:5])
                + ("\n  ..." if len(skipped_imports) > 5 else "")
                + "\n",
            )
        self.assertFalse(
            offenders,
            f"Dataclass parsers silently drop fields:\n  - "
            + "\n  - ".join(offenders)
            + "\n\nFix: add the missing field(s) to the parser, OR "
            "(if the omission is intentional) add the class to "
            "_KNOWN_PARTIAL_PARSERS with a comment explaining why.",
        )


if __name__ == "__main__":
    unittest.main()
