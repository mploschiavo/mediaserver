/**
 * Ratchet: ban new stub hooks that fake their data with
 * `Promise.resolve(...)` instead of calling the controller.
 *
 * The bug class this catches:
 * --------------------------------------------------------------
 *   The /ops page had a "Last bootstrap" tile showing `12/31/1969,
 *   6:00:00 PM` (Unix epoch 0 in CT) because `useOpsHealth` was
 *   wired to `Promise.resolve({ ..., last_bootstrap_at: new Date(0) })`
 *   instead of an actual endpoint. The `// TODO(api):` comment a
 *   few lines up declared intent but had no enforcement, so the stub
 *   shipped to production. The same pattern was found in 6 more
 *   hooks (Routing, Webhooks, Users, MeProfile, LibraryStats,
 *   RecentAdditions), each producing wrong / empty / placeholder
 *   data on its dashboard tile.
 *
 * What this test does:
 * --------------------------------------------------------------
 *   - Parse `src/api/hooks.ts` (the canonical home for cross-feature
 *     hooks; feature-local hooks under `features/*` are exempt).
 *   - Find every `Promise.resolve(...)` that appears inside a
 *     `queryFn:` arrow body. That's the stub idiom.
 *   - Compare the count against ALLOWED_STUBS below. Any stub not on
 *     the list, or any growth of the count, fails CI.
 *
 * Burn-down policy:
 * --------------------------------------------------------------
 *   When you wire a stub up to a real endpoint, REMOVE its name from
 *   ALLOWED_STUBS in the same PR. The list only shrinks. Adding a
 *   new stub requires reviewer agreement (you'll need to add to the
 *   list — that's the friction this ratchet is designed to create).
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";

// Stubs that exist today and are tracked for burn-down. Each entry
// is the hook function name (e.g. "useOpsHealth"). Removing an entry
// asserts the hook now talks to the controller.
//
// Initial set (all 7) was burned down in one pass:
//   * useLibraryStats / useRecentAdditions / useMeProfile — DELETED
//     (no consumers; the /me page reads features/me/hooks.ts).
//   * useRouting / useWebhooks / useUsers — wired with adapters
//     against the live endpoints.
//   * useOpsHealth — wired to the new /api/ops/health endpoint.
//
// New stubs land here ONLY with reviewer agreement. Even a temporary
// stub during a feature ramp counts — that friction is the point.
const ALLOWED_STUBS = new Set<string>([]);

const HOOKS_PATH = resolve(__dirname, "hooks.ts");

interface StubMatch {
  hookName: string;
  line: number;
}

/**
 * Walk the source line by line and find any `Promise.resolve` that
 * appears within an `export function useFoo(...): UseQueryResult<...>`
 * block, inside a `queryFn:` arrow.
 *
 * We use line-based scanning rather than a TS AST parse because:
 *   (a) no extra dep on @typescript-eslint/parser or ts-morph,
 *   (b) the `queryFn: () => Promise.resolve` idiom is unambiguous —
 *       no false positives from comments or string literals using
 *       `Promise.resolve` in other contexts.
 *
 * If false positives ever do arise, a `// stub-hook-ratchet:ignore`
 * comment on the same line is honored.
 */
function findStubHooks(source: string): StubMatch[] {
  const lines = source.split("\n");
  const matches: StubMatch[] = [];
  let currentHook: string | null = null;
  let inQueryFnBlock = false;

  const fnDeclRe = /^export\s+function\s+(use[A-Z][A-Za-z0-9_]*)\s*\(/;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? "";
    const decl = line.match(fnDeclRe);
    if (decl) {
      currentHook = decl[1] ?? null;
      inQueryFnBlock = false;
      continue;
    }
    if (currentHook === null) continue;
    // Reset on closing brace at column 0 (top-level function close).
    if (/^}\s*$/.test(line)) {
      currentHook = null;
      inQueryFnBlock = false;
      continue;
    }
    if (/queryFn\s*:/.test(line)) {
      inQueryFnBlock = true;
    }
    if (
      inQueryFnBlock &&
      /Promise\.resolve\b/.test(line) &&
      !/stub-hook-ratchet:ignore/.test(line)
    ) {
      matches.push({ hookName: currentHook, line: i + 1 });
      // One match per hook is enough — multi-line stubs would
      // otherwise count their template literal and wrappers.
      inQueryFnBlock = false;
    }
  }
  return matches;
}

describe("stub-hook ratchet", () => {
  const source = readFileSync(HOOKS_PATH, "utf-8");
  const stubs = findStubHooks(source);

  it("does not introduce new stub hooks beyond the allowlist", () => {
    const found = new Set(stubs.map((s) => s.hookName));
    const unexpected = [...found].filter((h) => !ALLOWED_STUBS.has(h));
    expect(unexpected).toEqual([]);
  });

  it("does not leave allowlist entries that have already been wired up", () => {
    // Inverse — if a hook is on ALLOWED_STUBS but no longer matches the
    // stub idiom, the allowlist is stale. Removing it asserts the wire-up.
    const found = new Set(stubs.map((s) => s.hookName));
    const stale = [...ALLOWED_STUBS].filter((h) => !found.has(h));
    expect(stale, `Wired-up hooks remain on ALLOWED_STUBS — remove them: ${stale.join(", ")}`)
      .toEqual([]);
  });

  it("locks the total stub count so the ratchet shrinks, never grows", () => {
    expect(stubs.length).toBeLessThanOrEqual(ALLOWED_STUBS.size);
  });
});
