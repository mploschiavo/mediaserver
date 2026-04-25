// API path contract ratchet.
//
// We just shipped a regression where the CommandPalette referenced
// `/api/admin/reconcile` while the controller actually exposes
// `/api/media-integrity/reconcile`. That class of bug ("the UI calls a
// route that doesn't exist") is silent until somebody clicks it in
// production.
//
// This test walks every `*.ts` / `*.tsx` file under `src/` (skipping
// `*.test.*`, `*.stories.*`, and the generated `types.ts` itself),
// extracts every literal that starts with `/api/`, and asserts each
// one is a path declared in the OpenAPI-generated `paths` interface.
// Dynamic segments (`/jobs/abc-123` -> `/jobs/{id}`) are normalized
// against any spec path with the same segment count and a matching
// static prefix.
//
// When the test fails it lists every offending literal alongside the
// file it was found in — that's the contract violation.

import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import type { paths } from "./types";

// Hard allowlist: paths that are intentionally absent from the OpenAPI
// spec but legitimately called by the UI. Keep this list short and
// document each entry — every addition is a contract escape.
const ALLOWLIST: ReadonlySet<string> = new Set<string>([
  // Authelia handles the logout exchange; the controller exposes the
  // endpoint as a passthrough and openapi-typescript may or may not
  // include it depending on which spec is loaded. Hard-allowlist so
  // the ratchet keeps green either way.
  "/api/auth/logout",
  // Authelia's ext_authz verification endpoint. Referenced by the
  // App.tsx redirect-loop guard (only as a path-prefix check, never
  // fetched). Lives entirely on the Authelia side; not a controller
  // route.
  "/api/verify",
]);

const SRC_ROOT = path.resolve(__dirname, "..");
const SPEC_PATHS: readonly string[] = Object.keys(
  // The `paths` interface is a *type*, not a runtime value. We can't
  // ask TS for its keys at runtime, so we reparse the generated file
  // and pluck quoted top-level keys. This is intentional: the source
  // of truth is the file the codegen wrote.
  collectSpecPaths(),
).filter((key) => key.startsWith("/api/"));

// Type-level guard: ensure `paths` is the shape we expect (an object
// keyed by route strings). If the codegen ever changes its export
// signature, downstream usage of `Object.keys(paths)` will surface it.
const _typeGuard = (_p: paths): unknown => _p;
void _typeGuard;

interface FoundLiteral {
  readonly raw: string; // the literal as it appeared, query-string included
  readonly normalized: string; // query stripped, dynamic segments masked
  readonly file: string; // absolute path
}

describe("API path contract", () => {
  const files = walk(SRC_ROOT).filter(isCandidateFile);
  const literals = files.flatMap((f) => extractLiteralsFromFile(f));
  const distinct = dedupe(literals);

  it("collects at least one path from the OpenAPI spec", () => {
    // Sanity check: if the regex against types.ts ever returns nothing
    // the rest of the test would silently pass. Hard-fail here instead.
    expect(SPEC_PATHS.length).toBeGreaterThan(0);
  });

  it("collects at least one path literal from the source tree", () => {
    expect(distinct.length).toBeGreaterThan(0);
  });

  it("every /api/ literal in source maps to a spec path", () => {
    const violations: string[] = [];
    for (const found of distinct) {
      if (ALLOWLIST.has(found.normalized)) continue;
      if (matchesSpec(found.normalized, SPEC_PATHS)) continue;
      violations.push(
        `  ${found.normalized}  (raw: ${JSON.stringify(found.raw)} in ${path.relative(
          SRC_ROOT,
          found.file,
        )})`,
      );
    }

    if (violations.length > 0) {
      // Surface the full list so a developer can fix all in one go
      // rather than play whack-a-mole on each rerun.
      throw new Error(
        [
          `Found ${violations.length} /api/* literal(s) in src/ that are not declared in the OpenAPI spec.`,
          `Either add the route to the spec (and run \`pnpm gen:api\`) or fix the literal:`,
          "",
          ...violations,
          "",
          `Spec paths available (count=${SPEC_PATHS.length}): see src/api/types.ts`,
        ].join("\n"),
      );
    }
  });
});

// ---------- helpers --------------------------------------------------------

/**
 * Recursively walks a directory, returning every regular file's
 * absolute path. Skips `node_modules` and any dotfile directory just
 * in case (none should live under `src/`, but defense in depth).
 */
function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".")) continue;
    const full = path.join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      out.push(...walk(full));
    } else if (stat.isFile()) {
      out.push(full);
    }
  }
  return out;
}

/**
 * Files we want to scan: TS/TSX under src/, excluding the generated
 * spec, anything testlike, and Storybook stories. The contract only
 * cares about runtime callers.
 */
function isCandidateFile(file: string): boolean {
  if (!/\.(tsx|ts)$/.test(file)) return false;
  if (file.endsWith(".test.ts") || file.endsWith(".test.tsx")) return false;
  if (file.endsWith(".stories.tsx") || file.endsWith(".stories.ts")) {
    return false;
  }
  if (file.endsWith(`${path.sep}types.ts`)) return false;
  return true;
}

/**
 * Strip line and block comments before scanning. Without this we get
 * false positives from TODO comments like `// TODO: GET /api/library/stats`,
 * which are aspirational, not active call sites.
 *
 * The replacement is intentionally simple — we only need to drop
 * `//...` to end-of-line and `/* ... *\/` blocks. String literals that
 * happen to contain `//` are rare in this codebase and would only
 * yield false negatives (a real call site missed), which we'd catch
 * the moment the route 404s — far less painful than a noisy ratchet.
 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

/**
 * Pull every distinct `/api/...` literal out of one file. We scan
 * three quote styles (single, double, backtick) and also catch the
 * static prefix of template strings — `\`/api/jobs/${id}\`` becomes
 * `/api/jobs/`, which we later match by prefix against any spec path.
 */
function extractLiteralsFromFile(file: string): FoundLiteral[] {
  const text = stripComments(readFileSync(file, "utf8"));
  const out: FoundLiteral[] = [];

  // Match `/api/...` inside any of the three quote styles. The
  // character class stops at `?` (we strip the query later), `${`
  // (template-string interpolation start), whitespace, the closing
  // quote, or a backslash. That gives us the raw static path.
  const re = /["'`](\/api\/[A-Za-z0-9/_:?{}.-]+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const raw = m[1];
    if (raw === undefined) continue;
    out.push({ raw, normalized: normalize(raw), file });
  }

  // Also catch template-string prefixes like `/api/jobs/${id}`. The
  // primary regex above already captures the static prefix when the
  // first `${` interrupts the character class, so this is redundant
  // for the single common case — but we keep an explicit pass for
  // any literal that contains a `${` immediately after a slash
  // segment, just so future authors don't have to think about the
  // primary regex's quirks.
  const tplRe = /`(\/api\/[A-Za-z0-9/_:?{}.-]*)\$\{/g;
  let t: RegExpExecArray | null;
  while ((t = tplRe.exec(text)) !== null) {
    const raw = t[1];
    if (!raw) continue;
    out.push({ raw, normalized: normalize(raw), file });
  }

  return out;
}

/**
 * Normalize a found literal for matching:
 *   - drop the query string (`?dry_run=1`),
 *   - leave dynamic segments alone — we mask them in `matchesSpec`
 *     once we know the segment count of the candidate spec path.
 */
function normalize(raw: string): string {
  const noQuery = raw.split("?")[0] ?? raw;
  return noQuery.replace(/\/+$/, "") || raw;
}

/**
 * Returns true if `literal` matches any path in the spec. Matching
 * rules:
 *   - exact equality wins,
 *   - otherwise, segment-by-segment match where any spec segment
 *     wrapped in `{}` accepts any non-empty literal segment,
 *   - if the literal is a strict prefix of a spec path (template
 *     string with interpolation cut off mid-route), accept it: the
 *     remaining segments will be filled by the runtime variables
 *     and the spec is the authority on what's valid past the prefix.
 */
function matchesSpec(literal: string, specPaths: readonly string[]): boolean {
  if (specPaths.includes(literal)) return true;

  const litSegments = literal.split("/").filter(Boolean);

  for (const spec of specPaths) {
    const specSegments = spec.split("/").filter(Boolean);

    // Same length: try param-aware match.
    if (specSegments.length === litSegments.length) {
      let ok = true;
      for (let i = 0; i < specSegments.length; i++) {
        const s = specSegments[i]!;
        const l = litSegments[i]!;
        if (s === l) continue;
        if (s.startsWith("{") && s.endsWith("}") && l.length > 0) continue;
        ok = false;
        break;
      }
      if (ok) return true;
    }

    // Prefix match for template-string fragments. The literal must
    // be at least one segment shorter than the spec, and every
    // literal segment must align with the spec segment at the same
    // index (with `{}` accepting anything).
    if (
      specSegments.length > litSegments.length &&
      litSegments.length >= 2 // require at least `/api/<something>/`
    ) {
      let ok = true;
      for (let i = 0; i < litSegments.length; i++) {
        const s = specSegments[i]!;
        const l = litSegments[i]!;
        if (s === l) continue;
        if (s.startsWith("{") && s.endsWith("}") && l.length > 0) continue;
        ok = false;
        break;
      }
      if (ok) return true;
    }
  }
  return false;
}

function dedupe(literals: readonly FoundLiteral[]): FoundLiteral[] {
  const seen = new Map<string, FoundLiteral>();
  for (const lit of literals) {
    // Keep the first occurrence per (normalized,file) pair so the
    // error message points at a real source location while still
    // collapsing duplicates within a file.
    const key = `${lit.file}::${lit.normalized}`;
    if (!seen.has(key)) seen.set(key, lit);
  }
  return [...seen.values()];
}

/**
 * The OpenAPI generator emits `paths` as a TypeScript interface, so
 * the keys are erased at runtime. We re-read `types.ts` and pull
 * out every top-level quoted key — they are the route strings we
 * want to validate against. This is the single source of truth.
 */
function collectSpecPaths(): Record<string, true> {
  const typesFile = path.resolve(__dirname, "./types.ts");
  const text = readFileSync(typesFile, "utf8");

  // Find the start of the `paths` interface and read until its
  // matching closing brace, then pluck quoted keys from within.
  const start = text.search(/export\s+interface\s+paths\s*\{/);
  if (start < 0) {
    throw new Error(
      "Could not locate `export interface paths {` in src/api/types.ts. " +
        "Did the openapi-typescript output format change?",
    );
  }
  const braceStart = text.indexOf("{", start);
  let depth = 0;
  let end = -1;
  for (let i = braceStart; i < text.length; i++) {
    const ch = text[i];
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }
  if (end < 0) {
    throw new Error(
      "Could not find the closing brace of the `paths` interface in types.ts.",
    );
  }
  const body = text.slice(braceStart + 1, end);

  // Top-level keys live at depth 1 inside `paths`. We track brace
  // depth as we walk and only collect quoted strings followed by `:`
  // when we're back at depth 0 of the body.
  const out: Record<string, true> = {};
  let d = 0;
  let i = 0;
  while (i < body.length) {
    const ch = body[i];
    if (ch === "{") {
      d++;
      i++;
      continue;
    }
    if (ch === "}") {
      d--;
      i++;
      continue;
    }
    if (d === 0 && ch === '"') {
      // Read the quoted key.
      const closeQuote = body.indexOf('"', i + 1);
      if (closeQuote < 0) break;
      const key = body.slice(i + 1, closeQuote);
      // The next non-whitespace char should be `:` for it to be a
      // property key (rather than, say, a string in a comment we
      // failed to strip).
      let j = closeQuote + 1;
      while (j < body.length && /\s/.test(body[j]!)) j++;
      if (body[j] === ":") out[key] = true;
      i = closeQuote + 1;
      continue;
    }
    i++;
  }
  return out;
}
