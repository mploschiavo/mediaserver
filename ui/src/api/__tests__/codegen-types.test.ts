/**
 * Codegen-types coverage ratchet.
 *
 * Why this exists
 * ----------------
 * `ui/src/api/fixture-codegen-validation.ts` is the third edge of the
 * (live, spec, types) triangle: it asserts that each captured live
 * response in `tests/fixtures/api_responses/` is structurally
 * assignable to the spec-derived TypeScript type from `types.ts`.
 * The TS type-check (`pnpm typecheck` / `tsc -b`) is what enforces
 * the assertion at compile time.
 *
 * The bug this guards against: a new fixture lands under
 * `tests/fixtures/api_responses/` (e.g. someone runs
 * `bin/ops/recapture-all-fixtures.sh`) without being wired into
 * `fixture-codegen-validation.ts`. The Python contract test
 * (`tests/unit/api/test_api_response_contract.py`) catches the
 * fixture-vs-spec edge, but if the fixture is silently absent from
 * `fixture-codegen-validation.ts`, the spec-vs-types edge for that
 * specific shape goes unenforced.
 *
 * What this test asserts
 * -----------------------
 * Every JSON file under `tests/fixtures/api_responses/` must be
 * accounted for in `fixture-codegen-validation.ts`, either:
 *   1. Registered as a `T_fx_<safe_var> = paths["..."]...` block
 *      (the validated set), OR
 *   2. Listed in the `Skipped fixtures` header comment with a reason
 *      (the skipped set — typically endpoints whose path is
 *      templated, or whose operation is `x-status: planned`).
 *
 * Either way, the fixture's existence has been observed and a
 * decision recorded. A fixture that is neither registered nor
 * skipped is dead weight.
 *
 * How to fix a failure
 * --------------------
 * The failure message prints a copy-paste-ready snippet for each
 * unaccounted-for fixture. The canonical fix is:
 *
 *     python3 bin/ops/gen-fixture-codegen-validation.py
 *
 * which regenerates `fixture-codegen-validation.ts` from scratch.
 * The `fixture-codegen-validation-fresh.test.ts` ratchet enforces
 * that the committed file matches a fresh regen.
 */

import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const FIXTURES_DIR = resolve(
  __dirname,
  "../../../../tests/fixtures/api_responses",
);
const VALIDATION_FILE = resolve(__dirname, "../fixture-codegen-validation.ts");

/**
 * Convert a fixture stem to the TS identifier the generator emits.
 * Mirrors `safe_var` in `bin/ops/gen-fixture-codegen-validation.py`:
 * dashes and dots become underscores, prefixed with `fx_`.
 */
function safeVar(stem: string): string {
  return "fx_" + stem.replace(/-/g, "_").replace(/\./g, "_");
}

/**
 * Mirror of `OVERRIDES` / `path_from_stem` in the generator. Only
 * the `webhooks` override is currently active; keep this in sync if
 * the generator grows new override entries.
 */
const OVERRIDES: Record<string, string> = {
  webhooks: "/webhooks",
};

function pathFromStem(stem: string): string {
  return OVERRIDES[stem] ?? "/api/" + stem.replace(/_/g, "/");
}

interface Coverage {
  registered: string[];
  skipped: string[];
  unaccounted: string[];
}

function classifyFixtures(source: string, fixtureStems: string[]): Coverage {
  const registered: string[] = [];
  const skipped: string[] = [];
  const unaccounted: string[] = [];

  // The skipped block is a flat list of `//   <stem>.json — <reason>`
  // lines in the file header. Match the stem segment before `.json`.
  const skippedStems = new Set<string>();
  for (const line of source.split("\n")) {
    const m = line.match(/^\/\/\s+([A-Za-z0-9._-]+)\.json\s+—/);
    if (m && m[1]) {
      skippedStems.add(m[1]);
    }
  }

  for (const stem of fixtureStems) {
    const varName = safeVar(stem);
    // Match the canonical generator output. The `T_fx_<var> = paths[...]`
    // line is the load-bearing assertion; presence of that exact prefix
    // confirms the fixture is wired into the type-check.
    const typeAliasMarker = `type T_${varName} = paths[`;
    const constMarker = `const _check_${varName}:`;
    if (source.includes(typeAliasMarker) && source.includes(constMarker)) {
      registered.push(stem);
    } else if (skippedStems.has(stem)) {
      skipped.push(stem);
    } else {
      unaccounted.push(stem);
    }
  }

  return { registered, skipped, unaccounted };
}

/**
 * Build a copy-paste-ready code snippet for an unaccounted-for
 * fixture, mirroring the generator's emitted pattern. Caller can
 * paste this into `fixture-codegen-validation.ts` (or, preferably,
 * regen via the Python script).
 */
function snippetFor(stem: string): string {
  const varName = safeVar(stem);
  const path = pathFromStem(stem);
  const rel = `../../../tests/fixtures/api_responses/${stem}.json`;
  return [
    `// ${path}`,
    `import ${varName} from "${rel}";`,
    `type T_${varName} = paths["${path}"]["get"]["responses"][200]["content"]["application/json"];`,
    `const _check_${varName}: Loosen<T_${varName}> = ${varName};`,
    `void _check_${varName};`,
    "",
  ].join("\n");
}

describe("fixture-codegen-validation coverage", () => {
  it("every fixture is either registered or explicitly skipped", () => {
    const source = readFileSync(VALIDATION_FILE, "utf-8");
    const stems = readdirSync(FIXTURES_DIR)
      .filter((f) => f.endsWith(".json"))
      .map((f) => f.slice(0, -".json".length))
      .sort();

    expect(stems.length).toBeGreaterThan(0);

    const { registered, skipped, unaccounted } = classifyFixtures(
      source,
      stems,
    );

    if (unaccounted.length > 0) {
      const snippets = unaccounted.map(snippetFor).join("\n");
      const message =
        `Found ${unaccounted.length} fixture(s) under ` +
        `tests/fixtures/api_responses/ that are neither registered ` +
        `nor listed as skipped in fixture-codegen-validation.ts.\n\n` +
        `Run: python3 bin/ops/gen-fixture-codegen-validation.py\n\n` +
        `Or paste these snippets into fixture-codegen-validation.ts:\n\n` +
        snippets;
      expect.soft(unaccounted, message).toEqual([]);
    }

    expect(unaccounted).toEqual([]);
    // Sanity: at least one fixture must be registered. A file with
    // ZERO `T_fx_*` blocks would mean the generator silently emitted
    // an empty body and the typecheck contract is doing nothing.
    expect(registered.length).toBeGreaterThan(0);
    // Total accounting must match the on-disk fixture count exactly —
    // catches a future bug where the same stem somehow lands in both
    // buckets, or one bucket double-counts.
    expect(registered.length + skipped.length + unaccounted.length).toEqual(
      stems.length,
    );
  });
});
