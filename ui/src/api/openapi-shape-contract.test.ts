// OpenAPI shape contract ratchet.
//
// Background: nine controller endpoints shipped with
// `additionalProperties: true` schemas because the spec author hadn't
// gotten around to typing the real shape yet. The wave-4/5 UI agents
// guessed wrong about the keys (e.g. `apps[]` instead of flat config,
// `oidc_providers[]` instead of singular `oidc_provider`), and the
// resulting cards rendered empty or with stub data because the
// real payload doesn't carry the keys they were reading.
//
// This contract walks the YAML spec and asserts that every endpoint
// in `RATCHETED_PATHS` declares a real `properties` block on its
// 200-response schema — i.e. the response is NOT just
// `additionalProperties: true` (or worse, no schema at all). New
// agents adding endpoints should either tighten the schema OR add
// the path to `INTENTIONAL_FREEFORM` with a justification comment.
//
// The check is intentionally string-level: we don't load `js-yaml`
// here (the project already uses `openapi-typescript` for the type
// codegen, but its result is structural). Reading the YAML as text
// and walking it line-by-line keeps the test lightweight and avoids
// pulling in a YAML parser.

import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Endpoints whose 200-response shape MUST be declared with explicit
 * `properties:` in `openapi.yaml`. Add new endpoints here as they
 * mature past the prototype phase. Removing an entry is fine but
 * should be paired with an `INTENTIONAL_FREEFORM` addition + comment.
 */
const RATCHETED_PATHS: ReadonlyArray<string> = [
  "/api/libraries",
  "/api/recent",
  "/api/routing",
  "/api/auth/config",
  "/api/display-preferences",
  "/api/password-policy",
  "/api/branding",
  "/api/jobs",
  "/api/livetv-sources",
];

/**
 * Endpoints that legitimately return free-form blobs. Each entry must
 * carry a `// reason: ...` comment explaining why the shape is opaque
 * to the UI. If you can describe the shape, take the entry out and
 * tighten the schema instead.
 */
const INTENTIONAL_FREEFORM: ReadonlySet<string> = new Set<string>([
  // reason: `/api/env` returns the raw process environment table —
  // every controller build emits a different superset depending on
  // which apps are configured. Operator UI doesn't index into it; the
  // EnvViewerCard renders it as a key/value table.
  "/api/env",
  // reason: `/api/envvars` is the bootstrap config bag — the keys are
  // user-defined (per-stack profile YAML) so the controller can't
  // enumerate them.
  "/api/envvars",
]);

describe("OpenAPI 200-response shape contract", () => {
  // Load the YAML once. Resolved relative to this file so the
  // ratchet keeps working even if vitest cwd shifts.
  const yamlPath = path.resolve(
    __dirname,
    "../../../contracts/api/openapi.yaml",
  );
  const yaml = readFileSync(yamlPath, "utf8");

  it("locates the OpenAPI YAML on disk", () => {
    expect(yaml.length).toBeGreaterThan(1000);
    expect(yaml).toContain("openapi:");
  });

  for (const route of RATCHETED_PATHS) {
    it(`${route} declares an explicit properties block on its 200 response`, () => {
      const block = extractGet200Schema(yaml, route);
      expect(block, `Could not locate GET ${route} 200-response schema`).not.toBe(
        null,
      );
      // Either the schema declares its own `properties:` (preferred)
      // or it `$ref`s a component schema (which we trust by
      // construction — the components are typed). We also tolerate
      // `oneOf` / `allOf` composition as long as `additionalProperties:
      // true` is NOT the only declaration.
      const hasProperties = /\bproperties:\s*\n/.test(block!);
      const hasRef = /\$ref:\s*['"]?#\/components\//.test(block!);
      const hasComposition = /\b(oneOf|allOf|anyOf):\s*\n/.test(block!);
      const trimmed = block!.trim();
      const isOnlyFreeform =
        /^type:\s*object\s*\n\s*additionalProperties:\s*true\s*$/.test(
          trimmed,
        );
      expect(
        hasProperties || hasRef || hasComposition,
        `${route}: 200 response must declare properties / $ref / composition.\n` +
          `Got:\n${trimmed}`,
      ).toBe(true);
      expect(
        isOnlyFreeform,
        `${route}: 200 response is still a bare additionalProperties:true bag.`,
      ).toBe(false);
    });
  }

  it("does not regress: known good endpoints stay tightened", () => {
    // Spot-check that every ratcheted path resolved a schema (the
    // per-path tests above would also fail, but a single aggregate
    // assertion makes the failure mode explicit when the spec moves
    // around and the regex misses every path).
    const failures: string[] = [];
    for (const route of RATCHETED_PATHS) {
      if (extractGet200Schema(yaml, route) === null) {
        failures.push(route);
      }
    }
    if (failures.length > 0) {
      throw new Error(
        `Could not locate GET ${failures.join(", ")} in the YAML — ` +
          `did the path layout change? Update the regex in extractGet200Schema.`,
      );
    }
  });

  it("INTENTIONAL_FREEFORM entries don't accidentally land in RATCHETED_PATHS", () => {
    for (const route of RATCHETED_PATHS) {
      expect(INTENTIONAL_FREEFORM.has(route)).toBe(false);
    }
  });
});

/**
 * Extract the YAML text between `<route>:` and the next top-level path
 * key, then narrow to the GET method's 200-response schema body. We
 * intentionally do this with line scanning rather than a YAML parser
 * — the file is large and we only need a substring around the schema
 * declaration to make the assertions above.
 *
 * Returns the raw indented YAML block (everything below `schema:`)
 * up to the next sibling-indent line, or null if the route or the
 * 200 response can't be located.
 */
function extractGet200Schema(yaml: string, route: string): string | null {
  // Find the path entry. Two leading spaces because every path in
  // openapi.yaml lives under the top-level `paths:` map.
  const pathMarker = `\n  ${route}:\n`;
  const start = yaml.indexOf(pathMarker);
  if (start < 0) return null;

  // Bound the path block by looking ahead for the next top-level
  // `  /api/...:` or `  /actions/...:` etc. We accept any 2-space
  // indented YAML key at the same depth as the path itself.
  const sliceStart = start + pathMarker.length;
  const remainder = yaml.slice(sliceStart);
  // Match `^  /` or `^  #` (tag separator) at the start of a line.
  const nextPath = remainder.search(/\n {2}[/A-Za-z]/);
  const block =
    nextPath > 0 ? remainder.slice(0, nextPath) : remainder.slice(0, 8000);

  // Only consider the GET method. The first line of `block` is the
  // first key beneath the path entry — it has NO leading `\n`. So
  // accept either `\n    get:\n` (later in the block) OR `    get:\n`
  // at the very start.
  let getStart = block.indexOf("\n    get:\n");
  if (getStart < 0 && block.startsWith("    get:\n")) {
    getStart = 0;
  }
  if (getStart < 0) return null;
  const getBlock = block.slice(getStart);
  // Find the `"200":` response (handles single + double quotes).
  const r200 = getBlock.search(/\n {8}['"]?200['"]?:\s*\n/);
  if (r200 < 0) return null;
  const after200 = getBlock.slice(r200);
  // Find the `schema:` line under the 200 content/application/json.
  const schemaIdx = after200.indexOf("schema:");
  if (schemaIdx < 0) return null;

  // Capture the indented YAML block beneath `schema:`. The schema
  // body is indented at 14 spaces (`              ` per the file's
  // 2-space convention nested 7 levels deep). We scan forward and
  // stop at the first line that's de-indented past 14 spaces or at
  // the next sibling key (e.g. another response code).
  const schemaStart = after200.indexOf("\n", schemaIdx) + 1;
  const lines = after200.slice(schemaStart).split("\n");
  const collected: string[] = [];
  let baseIndent = -1;
  for (const line of lines) {
    if (line.trim() === "") {
      collected.push(line);
      continue;
    }
    const indent = line.match(/^ */)?.[0].length ?? 0;
    if (baseIndent === -1) baseIndent = indent;
    if (indent < baseIndent) break;
    collected.push(line);
    if (collected.length > 200) break;
  }
  return collected.join("\n");
}
