/**
 * Ratchet: no forward-looking PR/version references in user-visible
 * UI text.
 *
 * The bug class this catches:
 * --------------------------------------------------------------
 *   Card descriptions like "Edit comes in v1.0.244 (PR-5)" or
 *   "Per-host edit lands in PR-6.5" that document internal release
 *   plans inside CardDescription / placeholder / aria-label strings
 *   the operator actually reads. They make the UI look like a
 *   work-in-progress dump and rot — by the time v1.0.244 ships, the
 *   note is stale.
 *
 * What this test does:
 * --------------------------------------------------------------
 *   - Walk every .tsx / .ts file under src/ (excluding test files,
 *     api/types.ts codegen, and a small allowlist).
 *   - For each line that doesn't start with whitespace + `*` or `//`
 *     (i.e. NOT a comment), match against the forbidden patterns:
 *       * "PR-N"             (PR-5, PR-9, PR-11.5, ...)
 *       * "lands in vX.Y.Z"  forward-looking version promises
 *       * "comes in vX.Y.Z"
 *       * "will land", "coming in PR" — phrase-only catches
 *   - Fail with file:line + the offending substring so the author can
 *     fix it in their PR rather than discover it in production.
 *
 * What's allowed:
 * --------------------------------------------------------------
 *   - JSDoc/inline comments referencing PRs (`* Card 6 from the design
 *     doc; PR-6 ships read-only`) — internal context, never rendered.
 *   - Backward-looking version notes in copy ("controller v1.0.179+")
 *     — those describe a min version of a real feature, not a
 *     forward-looking promise.
 *   - The allowlist below (filename → reason) for any deliberate
 *     exceptions. Adding to it requires reviewer sign-off.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { resolve, join } from "node:path";
import { describe, it, expect } from "vitest";

const SRC_ROOT = resolve(__dirname);

// Files that legitimately mention versions/PRs in user-visible text.
// Keep this list short — the goal is to catch leaks, not paper over
// them.
const ALLOWLIST: ReadonlyMap<string, string> = new Map([
  // The update banner BY DESIGN shows the running version number.
  ["components/layout/UpdateAvailableBanner.tsx", "displays the controller version"],
  // The branding/credits surface shows version info.
  ["components/layout/BuildInfo.tsx", "displays build version"],
]);

// Forbidden patterns. Each entry is { regex, label } where regex
// matches a forward-looking promise. Comments (// or /* */) are
// stripped before matching, so internal docs aren't flagged.
const FORBIDDEN: ReadonlyArray<{ regex: RegExp; label: string }> = [
  { regex: /\bPR-\d+(\.\d+)?\b/, label: "PR-N reference" },
  {
    regex: /\b(?:lands?|comes?|coming|ships?|shipping)\s+in\s+(?:v\d+\.\d+\.\d+|PR-\d+)/i,
    label: "forward-looking version promise",
  },
  { regex: /\bwill\s+land\b/i, label: "future-tense promise" },
];

function listSourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      out.push(...listSourceFiles(full));
      continue;
    }
    if (!/\.(tsx?|jsx?)$/.test(entry)) continue;
    if (entry.endsWith(".test.ts") || entry.endsWith(".test.tsx")) continue;
    if (entry.endsWith(".d.ts")) continue;
    out.push(full);
  }
  return out;
}

/**
 * Strip all single-line and block comments from a JS/TS source string,
 * preserving line structure (so file:line numbers in error messages
 * still match). String literals are left intact — that's where the
 * leaks land.
 */
function stripComments(src: string): string {
  // First, remove block comments (/* ... */) — replace with same
  // number of newlines so line numbers stay aligned.
  let out = src.replace(/\/\*[\s\S]*?\*\//g, (m) => {
    const newlines = m.match(/\n/g);
    return newlines ? "\n".repeat(newlines.length) : "";
  });
  // Then strip single-line comments (// ...). Naive but acceptable —
  // the only pathological case is a `//` inside a string literal,
  // which the strict regex below would mis-handle. Use a simple
  // line-by-line scan that respects strings.
  out = out
    .split("\n")
    .map((line) => {
      let inSingle = false;
      let inDouble = false;
      let inBacktick = false;
      for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        const prev = i > 0 ? line[i - 1] : "";
        if (prev === "\\") continue; // escape
        if (ch === "'" && !inDouble && !inBacktick) inSingle = !inSingle;
        else if (ch === '"' && !inSingle && !inBacktick) inDouble = !inDouble;
        else if (ch === "`" && !inSingle && !inDouble) inBacktick = !inBacktick;
        else if (
          ch === "/" &&
          line[i + 1] === "/" &&
          !inSingle &&
          !inDouble &&
          !inBacktick
        ) {
          return line.slice(0, i);
        }
      }
      return line;
    })
    .join("\n");
  return out;
}

describe("no-version-promises ratchet", () => {
  it("no .tsx/.ts file leaks PR-N or forward-version references in user-visible text", () => {
    const files = listSourceFiles(SRC_ROOT);
    const violations: string[] = [];

    for (const full of files) {
      const rel = full.slice(SRC_ROOT.length + 1);
      if (ALLOWLIST.has(rel)) continue;
      // Always allow this ratchet file itself (it discusses the
      // forbidden patterns by name).
      if (rel === "no-version-promises-ratchet.test.ts") continue;

      const src = readFileSync(full, "utf-8");
      const stripped = stripComments(src);
      const lines = stripped.split("\n");

      lines.forEach((line, idx) => {
        for (const { regex, label } of FORBIDDEN) {
          const m = line.match(regex);
          if (m) {
            violations.push(
              `${rel}:${idx + 1} (${label}): ${line.trim().slice(0, 120)}`,
            );
          }
        }
      });
    }

    if (violations.length > 0) {
      const msg =
        "Forward-looking PR/version references leaked into user-visible UI text.\n" +
        "Either drop the reference (the operator doesn't care about your release plan)\n" +
        "or move it into a JSDoc/inline comment (which this ratchet skips).\n\n" +
        "Violations:\n  " +
        violations.join("\n  ");
      throw new Error(msg);
    }
    expect(violations).toEqual([]);
  });
});
