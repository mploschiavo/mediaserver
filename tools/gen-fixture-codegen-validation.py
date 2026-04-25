#!/usr/bin/env python3
"""Generate ``ui/src/api/fixture-codegen-validation.ts`` from the
captured ``tests/fixtures/api_responses/*.json`` set + ``openapi.yaml``.

What the generated file does
----------------------------
For every fixture whose path has a documented GET 200 schema, emits:

    import api_libraries from "...libraries.json";
    type T_libraries = paths["/api/libraries"]["get"]["responses"][200]
                       ["content"]["application/json"];
    const _check_libraries: T_libraries = api_libraries;

That const assignment is the contract: TypeScript verifies the JSON
fixture is structurally assignable to the spec-derived type. If a
card hand-rolls a type like ``EpgProvider {base_url}`` while the
spec emits ``url_template``, the codegen type still has
``url_template`` — and the card's hand-rolled type doesn't enter the
picture here. So this catches a different bug class than the existing
ratchets:

  * test_api_response_contract.py:  live response   <-> openapi.yaml
  * types-fresh.test.ts:            openapi.yaml    <-> types.ts
  * fixture-codegen-validation.ts:  live response   <-> types.ts (compile-time)

Together the three close every edge of the (live, spec, types)
triangle.

Usage
-----
    python3 tools/gen-fixture-codegen-validation.py

Output is deterministic — re-running with no spec/fixture changes
produces identical bytes. The companion ``fixture-codegen-validation-fresh.test.ts``
diffs the generated file vs disk and fails CI if regen is needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "src" / "media_stack" / "api" / "openapi.yaml"
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "api_responses"
OUT_PATH = ROOT / "ui" / "src" / "api" / "fixture-codegen-validation.ts"

# Fixture stem -> override API path (for paths the convention can't
# derive: dashed names that map to slashed paths, /api-less paths).
OVERRIDES: dict[str, str] = {
    "webhooks": "/webhooks",
}


def path_from_stem(stem: str) -> str:
    return OVERRIDES.get(stem) or "/api/" + stem.replace("_", "/")


def has_documented_get_200(spec: dict, path: str) -> bool:
    """Schema is documented AND not flagged x-status: planned."""
    paths = spec.get("paths") or {}
    op = (paths.get(path) or {}).get("get")
    if not isinstance(op, dict):
        return False
    if str(op.get("x-status") or "").lower() == "planned":
        return False
    r200 = (op.get("responses") or {}).get("200") or (op.get("responses") or {}).get(200)
    if not isinstance(r200, dict):
        return False
    return isinstance(((r200.get("content") or {}).get("application/json") or {}).get("schema"), dict)


def safe_var(stem: str) -> str:
    """Convert a fixture stem to a valid TS identifier."""
    return "fx_" + stem.replace("-", "_").replace(".", "_")


def main() -> int:
    if not SPEC_PATH.exists():
        print(f"openapi.yaml not found at {SPEC_PATH}", file=sys.stderr)
        return 1
    spec = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))

    fixtures = sorted(FIXTURES_DIR.glob("*.json"))
    rows: list[tuple[str, str, str]] = []  # (stem, path, var)
    skipped: list[tuple[str, str]] = []  # (stem, reason)
    for fx in fixtures:
        stem = fx.stem
        path = path_from_stem(stem)
        if not has_documented_get_200(spec, path):
            skipped.append((stem, f"no GET 200 schema for {path} (or x-status: planned)"))
            continue
        rows.append((stem, path, safe_var(stem)))

    header = f'''// AUTO-GENERATED — do not edit by hand.
// Source: {SPEC_PATH.relative_to(ROOT)} + {FIXTURES_DIR.relative_to(ROOT)}
// Regenerate: python3 tools/gen-fixture-codegen-validation.py
//
// What this exists for
// --------------------
// TypeScript-level contract that every captured live response (the
// fixtures committed under tests/fixtures/api_responses/) is
// structurally assignable to the spec-derived type from types.ts.
// tsc -b in `npm run build` / `npm run typecheck` validates these
// const assignments — drift produces a compile error.
//
// This catches the bug class where a UI card hand-rolls an
// interface that doesn't match the spec (e.g. {{base_url}} vs
// {{url_template}}). The card itself compiles fine; this file
// forces fixtures through the SPEC-DERIVED type so the divergence
// is caught.
//
// Skipped fixtures (no GET 200 schema, or x-status: planned):
'''
    if skipped:
        for stem, reason in skipped:
            header += f"//   {stem}.json — {reason}\n"
    else:
        header += "//   (none)\n"
    header += "\n"
    header += '/* eslint-disable @typescript-eslint/no-unused-vars */\n'
    header += '/* eslint-disable unused-imports/no-unused-vars */\n\n'
    header += 'import type { paths } from "./types";\n\n'

    body_lines: list[str] = []
    for stem, path, var in rows:
        # Path needs to escape slashes? No — TS index-access strings
        # take the path verbatim with quotes.
        rel_fixture = f"../../../tests/fixtures/api_responses/{stem}.json"
        type_alias = f"T_{var}"
        body_lines.append(f'// {path}')
        body_lines.append(f'import {var} from "{rel_fixture}";')
        body_lines.append(
            f'type {type_alias} = '
            f'paths["{path}"]["get"]["responses"][200]'
            f'["content"]["application/json"];'
        )
        body_lines.append(f'const _check_{var}: {type_alias} = {var};')
        body_lines.append(f'void _check_{var};')
        body_lines.append('')

    # Wrap each codegen type in a `Loosen<T>` helper so JSON's
    # widening of string-literal-enums to plain `string` doesn't
    # trip every assignment. We still catch FIELD NAME drift,
    # MISSING fields, and SHAPE mismatches — which is the bug
    # class the UI hand-rolled-interface trap fell into. Enum
    # value drift is already caught by the Python contract test
    # which uses jsonschema with full literal validation.
    helper = """\
// Recursively widen string / number / boolean literals (and enum
// unions) to their base types. JSON imports lose literal-type
// information at the parse boundary, so asserting fixture:
// paths[...] would fail every enum field. Loosen distributes over
// unions (no [T] wrapper) so a property typed
// ``"a" | "b" | undefined`` becomes ``string | undefined``.
//
// What this preserves
// -------------------
// * Field names — extra/missing keys still error.
// * Object vs array — wrong-kind shape still errors.
// * Nested structure — every level recursed.
//
// What this loosens (intentional)
// -------------------------------
// * Enum values — the Python contract test uses jsonschema with
//   full enum validation. Don't double-up here.
// * String/number/boolean literal narrowing — JSON loses these.
type Loosen<T> =
    T extends readonly (infer U)[] ? Loosen<U>[] :
    T extends string ? string :
    T extends number ? number :
    T extends boolean ? boolean :
    T extends null ? null :
    T extends undefined ? undefined :
    // `Record<string, never>` is what openapi-typescript emits for
    // an unconstrained `type: object` (no properties / no
    // additionalProperties). The spec author meant "any object" —
    // widen to permit the live response.
    T extends Record<string, never> ? Record<string, unknown> :
    T extends object ? { [K in keyof T]: Loosen<T[K]> } :
    T;

"""
    # Re-render assignments with Loosen<T_var> as the asserted type.
    body_lines2: list[str] = []
    for stem, path, var in rows:
        rel_fixture = f"../../../tests/fixtures/api_responses/{stem}.json"
        type_alias = f"T_{var}"
        body_lines2.append(f'// {path}')
        body_lines2.append(f'import {var} from "{rel_fixture}";')
        body_lines2.append(
            f'type {type_alias} = '
            f'paths["{path}"]["get"]["responses"][200]'
            f'["content"]["application/json"];'
        )
        body_lines2.append(f'const _check_{var}: Loosen<{type_alias}> = {var};')
        body_lines2.append(f'void _check_{var};')
        body_lines2.append('')

    out = header + helper + "\n".join(body_lines2).rstrip() + "\n"
    OUT_PATH.write_text(out, encoding="utf-8")
    print(f"wrote {OUT_PATH.relative_to(ROOT)}")
    print(f"  validated: {len(rows)} fixtures")
    print(f"  skipped:   {len(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
