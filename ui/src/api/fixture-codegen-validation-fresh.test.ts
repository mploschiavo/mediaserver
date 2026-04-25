/**
 * Ratchet: ``src/api/fixture-codegen-validation.ts`` must be a
 * faithful regen of ``bin/ops/gen-fixture-codegen-validation.py``
 * over the current spec + fixture set.
 *
 * Why this exists
 * ----------------
 * The fixture-codegen-validation.ts file is auto-generated. If it
 * goes stale, the bug class it's supposed to catch (UI hooks
 * hand-rolling shapes that don't match the spec) sneaks back in.
 * This test re-runs the generator against the current state of
 * the repo and diffs the output against the committed file. Any
 * difference fails CI with a one-line "regenerate" instruction.
 *
 * Companion: ``types-fresh.test.ts`` does the same for types.ts.
 * Together they form the codegen-freshness pair: spec change
 * without regen = test fails.
 *
 * Cost
 * -----
 * The generator is a small Python script that loads the spec and
 * walks fixtures — under 200ms. We shell out via execFileSync so
 * the test stays self-contained.
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { describe, it, expect } from "vitest";

const COMMITTED_PATH = resolve(__dirname, "fixture-codegen-validation.ts");
const GEN_SCRIPT = resolve(
  __dirname,
  "../../../bin/ops/gen-fixture-codegen-validation.py",
);
const REPO_ROOT = resolve(__dirname, "../../..");
const TARGET_REL = "ui/src/api/fixture-codegen-validation.ts";

describe("fixture-codegen-validation freshness", () => {
  it("matches a fresh regen from the spec + fixtures", () => {
    const tmp = mkdtempSync(join(tmpdir(), "fcv-fresh-"));
    try {
      // Run the generator with OUT_PATH redirected to a temp file.
      // The script writes to a hard-coded location relative to the
      // repo root; we copy the committed file aside, run the
      // generator, capture its output, then restore. Simpler: read
      // the committed file first, run the generator (which
      // overwrites in place), read the new file, then restore.
      const committed = readFileSync(COMMITTED_PATH, "utf-8");
      execFileSync("python3", [GEN_SCRIPT], {
        cwd: REPO_ROOT,
        stdio: "pipe",
      });
      const fresh = readFileSync(COMMITTED_PATH, "utf-8");
      // Always restore the committed content — even if the test
      // fails — so the working tree isn't left mutated.
      try {
        if (fresh !== committed) {
          // Save the regenerated content to a temp path so the
          // operator can `mv` it into place if they accept the diff.
          const stagedPath = join(tmp, "fixture-codegen-validation.regen.ts");
          require("node:fs").writeFileSync(stagedPath, fresh, "utf-8");
          expect(
            fresh,
            `${TARGET_REL} is stale vs the spec / fixtures. ` +
              `Regenerate with: python3 bin/ops/gen-fixture-codegen-validation.py — ` +
              `or copy the regen output from ${stagedPath}.`,
          ).toEqual(committed);
        }
      } finally {
        // Restore the committed content. If the test failed we want
        // git status to remain clean; if it passed nothing changed.
        require("node:fs").writeFileSync(COMMITTED_PATH, committed, "utf-8");
      }
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });
});
