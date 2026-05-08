import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import process from "node:process";

/**
 * Ratchet for the ADR-0005-era "Run now broken / unknown path
 * '/api/actions/run-media-hygiene'" bug class.
 *
 * The sister ratchet ``fetcher_path_contract.test.ts`` catches
 * paths that don't start with ``api/``. This ratchet catches the
 * NEXT layer: a path that DOES start with ``api/`` but doesn't
 * match any registered route in ``contracts/api/openapi.yaml``.
 * The SPA's nginx happily proxies the request through; the
 * controller's Router has no matching route; the operator sees
 * a 404 with "unknown path '/api/X'" and a button that
 * silently fails.
 *
 * History: ``/api/actions/{name}`` was missing — only the
 * root-path ``/actions/{name}`` was registered — and Run-now
 * shipped broken to production. This ratchet would have caught
 * it before the bake.
 */

const SRC_ROOT = join(process.cwd(), "src");
const SPEC_PATH = join(process.cwd(), "..", "contracts", "api", "openapi.yaml");

function* walk(dir: string): Generator<string> {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      yield* walk(full);
    } else if (
      st.isFile() &&
      (full.endsWith(".ts") || full.endsWith(".tsx")) &&
      !full.endsWith(".d.ts") &&
      !full.endsWith(".test.ts") &&
      !full.endsWith(".test.tsx") &&
      !full.endsWith(".stories.ts") &&
      !full.endsWith(".stories.tsx")
    ) {
      yield full;
    }
  }
}

interface FetcherCall {
  file: string;
  line: number;
  raw: string;
  normalized: string;
}

function collectFetcherCalls(): FetcherCall[] {
  const PATTERN = /fetcher\s*(?:<[^>]*>)?\s*\(\s*([`"])([^`"]+)\1/g;
  const calls: FetcherCall[] = [];
  for (const file of walk(SRC_ROOT)) {
    const text = readFileSync(file, "utf-8");
    let m: RegExpExecArray | null;
    while ((m = PATTERN.exec(text))) {
      const literal = m[2] ?? "";
      if (literal.startsWith("${")) continue;
      // First, replace ``${...}`` interpolations with ``{param}``
      // BEFORE stripping the query string, so a literal ``?`` inside
      // the path is distinguishable from the runtime-built query
      // string (which is itself an interpolation).
      let normalized = literal.replace(/\$\{[^}]+\}/g, "{param}");
      // Strip leading slashes and any literal query string.
      const stripped = normalized.replace(/^\/+/, "").split("?")[0] ?? "";
      normalized = stripped.replace(/\/+$/, "");
      // Adjacent ``{param}{param}`` runs (path segment + query
      // string built from interpolations like
      // ``api/logs/${source}${qs}``) collapse to a single ``{param}``
      // so the path-shape matches the spec's path-template form.
      normalized = normalized.replace(/(?:\{param\})+/g, "{param}");
      // If the trailing segment is a concrete query-string token
      // like ``{param}&filter=foo``, drop it.
      const finalized = "/" + normalized;
      const before = text.slice(0, m.index);
      const line = before.split("\n").length;
      calls.push({
        file: relative(SRC_ROOT, file),
        line,
        raw: literal,
        normalized: finalized,
      });
    }
  }
  return calls;
}

function loadOpenApiPaths(): Set<string> {
  const text = readFileSync(SPEC_PATH, "utf-8");
  const PATH_LINE = /^ {2}(\/[^:\s]+):\s*$/gm;
  const paths = new Set<string>();
  let m: RegExpExecArray | null;
  while ((m = PATH_LINE.exec(text))) {
    const raw = m[1];
    if (!raw) continue;
    const normalized = raw.replace(/\{[^}]+\}/g, "{param}");
    paths.add(normalized);
  }
  return paths;
}

/**
 * Match a fetcher path against the spec set. Exact match first.
 * Otherwise, walk each spec path's segments and treat ``{param}``
 * as wildcard-matching any single concrete segment (so a
 * concrete call like ``/api/actions/cancel`` matches the spec's
 * path-template ``/api/actions/{name}``).
 */
function isCovered(specPaths: Set<string>, normalized: string): boolean {
  if (specPaths.has(normalized)) return true;
  const callSegs = normalized.split("/");
  for (const spec of specPaths) {
    const specSegs = spec.split("/");
    if (specSegs.length !== callSegs.length) continue;
    let allMatch = true;
    for (let i = 0; i < specSegs.length; i++) {
      const s = specSegs[i];
      const c = callSegs[i];
      if (s === c) continue;
      if (s === "{param}") continue; // any concrete value matches
      allMatch = false;
      break;
    }
    if (allMatch) return true;
  }
  return false;
}

describe("ratchet: every fetcher() path is registered in openapi.yaml", () => {
  // Bridge allowlist — paths handled by nginx or the controller
  // but not (yet) in the OpenAPI spec. Each entry needs a reason.
  const ALLOWED_NON_OPENAPI = new Set([
    // SPA's own nginx static-asset proxy.
    "/api/static/{param}",
    // PWA service-worker config; declared at root /sw-config.json.
    "/api/sw-config",
  ]);

  it("zero fetcher() paths are missing from the controller's OpenAPI spec", () => {
    const calls = collectFetcherCalls();
    const specPaths = loadOpenApiPaths();
    const missing: FetcherCall[] = [];
    const seenMissing = new Set<string>();
    for (const call of calls) {
      if (!call.normalized.startsWith("/api/")) continue;
      if (ALLOWED_NON_OPENAPI.has(call.normalized)) continue;
      if (isCovered(specPaths, call.normalized)) continue;
      if (seenMissing.has(call.normalized)) continue;
      seenMissing.add(call.normalized);
      missing.push(call);
    }
    const message =
      missing.length === 0
        ? ""
        : `Found ${missing.length} fetcher() path(s) NOT registered in contracts/api/openapi.yaml.\n` +
          `These will 404 with "unknown path '<path>'" in production (nginx proxies\n` +
          `the request through; the controller's Router has no matching route):\n` +
          missing
            .map(
              (o) =>
                `  ${o.file}:${o.line}  ->  fetcher("${o.raw}", ...)\n` +
                `    normalized: ${o.normalized}`,
            )
            .join("\n") +
          `\n\nFix: register the path in openapi.yaml + add a route handler\n` +
          `(see api/routes/post_misc.py::handle_action_api as a reference for\n` +
          `path-aliases that exist solely so the SPA's /api/* nginx proxy block\n` +
          `reaches them).`;
    expect(missing, message).toEqual([]);
  });
});
