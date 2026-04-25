/**
 * Ratchet: ``src/api/types.ts`` must be a faithful regen of
 * ``src/media_stack/api/openapi.yaml`` via openapi-typescript.
 *
 * Why this exists
 * ----------------
 * The dashboard has hit the same bug class three times in a row:
 * the controller emits one shape, ``openapi.yaml`` declares
 * another, and the UI imports / hand-rolls a third. The Python
 * ``test_api_response_contract`` ratchet pins the
 * controller↔spec edge. This test pins the spec↔TS edge.
 * Together, drift on any one corner of the (live, spec, TS)
 * triangle now trips a CI failure.
 *
 * How it runs
 * ------------
 * Shells out to the same ``openapi-typescript`` binary
 * ``npm run gen:api`` uses, writes to a temp file, and compares
 * byte-for-byte against the committed ``src/api/types.ts``. If
 * the edit cycle was "edit openapi.yaml, run npm run gen:api,
 * commit both", this test passes. If it was "edit openapi.yaml,
 * forget to regen", or "hand-edit types.ts directly", this fails
 * with a one-line "run npm run gen:api" instruction.
 *
 * Cost
 * -----
 * Running openapi-typescript takes ~400ms. Run as a regular unit
 * test — it's still cheap relative to the bug-class it prevents.
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { describe, it, expect } from "vitest";

const COMMITTED_TYPES_PATH = resolve(__dirname, "types.ts");
const SPEC_PATH = resolve(
  __dirname,
  "../../../src/media_stack/api/openapi.yaml",
);

describe("openapi-typescript codegen freshness", () => {
  it("types.ts matches a fresh regen from openapi.yaml", () => {
    const tmp = mkdtempSync(join(tmpdir(), "openapi-fresh-"));
    const target = join(tmp, "types.ts");
    try {
      // Use the same binary `npm run gen:api` does. The package's
      // `exports` map confuses require.resolve in some workspace
      // layouts; reading the `bin` field from package.json and
      // joining manually is more portable across pnpm / yarn-PnP /
      // npm-flat installs.
      const pkgPath = require.resolve("openapi-typescript/package.json");
      const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
      const binEntry =
        typeof pkg.bin === "string"
          ? pkg.bin
          : pkg.bin?.["openapi-typescript"];
      if (!binEntry) {
        throw new Error(
          `openapi-typescript package.json has no usable bin entry; ` +
            `update this test if the package layout changed.`,
        );
      }
      const cli = resolve(pkgPath, "..", binEntry);
      // openapi-typescript writes a banner with a timestamp ONLY
      // if --immutable is set; default output is reproducible.
      execFileSync(
        process.execPath,
        [cli, SPEC_PATH, "-o", target],
        { stdio: "pipe" },
      );
      const fresh = readFileSync(target, "utf-8");
      const committed = readFileSync(COMMITTED_TYPES_PATH, "utf-8");
      if (fresh !== committed) {
        // Save the diff somewhere visible for debugging when CI
        // fails — `git diff` against this path shows the drift.
        const diffPath = join(tmp, "drift-fresh-vs-committed.diff");
        writeFileSync(diffPath, summarizeDiff(committed, fresh));
        expect(
          fresh,
          "src/api/types.ts is stale vs src/media_stack/api/openapi.yaml. " +
            "Run `npm run gen:api` and commit the result. " +
            `Drift summary at ${diffPath}.`,
        ).toEqual(committed);
      }
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });
});

/**
 * Render a compact line-level diff for the failure message. Avoids
 * dragging in `diff` package — the output is informational, not a
 * patch we'd apply.
 */
function summarizeDiff(a: string, b: string): string {
  const al = a.split("\n");
  const bl = b.split("\n");
  const out: string[] = [];
  const max = Math.max(al.length, bl.length);
  for (let i = 0; i < max; i++) {
    if (al[i] !== bl[i]) {
      out.push(`@${i + 1}: -${al[i] ?? ""}`);
      out.push(`@${i + 1}: +${bl[i] ?? ""}`);
      if (out.length > 80) {
        out.push("... (truncated)");
        break;
      }
    }
  }
  return out.join("\n");
}
