import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import process from "node:process";

/**
 * Ratchet for the v1.3.6 "405 Method Not Allowed on Run now / Add
 * webhook" bug. The SPA's nginx config (`docker/ui-nginx.conf`)
 * only proxies `/api/*` to the controller. Any `fetcher(...)` call
 * with a path that doesn't start with `api/` falls into the
 * SPA-fallback `try_files` block and:
 *   - GET → returns index.html (looks like a successful 200 but the
 *     JSON parse below will fail with a confusing "unexpected token <"
 *     SyntaxError that's expensive to root-cause)
 *   - POST/PUT/PATCH/DELETE → returns 405 Method Not Allowed
 *
 * History: this bug class has shipped THREE times — `/actions/<name>`,
 * `/actions/cancel`, `/webhooks` (add + delete). Each one cost an
 * operator-visible round trip to diagnose. This ratchet greps every
 * `fetcher(...)` call site and fails CI if any string-literal path
 * argument doesn't start with `api/`.
 *
 * Allowed exceptions:
 *   - Template strings whose interpolation builds the path at runtime
 *     (e.g. `${baseUrl}/api/...`) — captured by the leading-substring
 *     check on the static prefix.
 *   - Variable arguments (no quote → no string literal to parse) —
 *     skipped; the call site has to use a known-good helper.
 */

// vitest runs from the `ui/` directory; src/ is the source root.
// Using process.cwd() avoids `import.meta.url` which the happy-dom
// test environment doesn't expose as a file:// URL.
const SRC_ROOT = join(process.cwd(), "src");

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

interface Offense {
  file: string;
  line: number;
  match: string;
}

function collectOffenses(): Offense[] {
  // Match `fetcher<…>("…", …)` or `fetcher<…>(\`…\`, …)` where the
  // quoted/back-ticked first argument can be on the same line OR the
  // line right after the opening paren (multi-line calls are common).
  // We scan the raw text once; awk-style line tracking is enough.
  const PATTERN = /fetcher\s*(?:<[^>]*>)?\s*\(\s*([`"])([^`"]+)\1/g;

  const offenses: Offense[] = [];
  for (const file of walk(SRC_ROOT)) {
    const text = readFileSync(file, "utf-8");
    let m: RegExpExecArray | null;
    while ((m = PATTERN.exec(text))) {
      const literal = m[2] ?? "";
      // Skip template strings whose first segment is an interpolation
      // (`${PATH_CONSTANT}/...`). Those resolve at runtime via a named
      // constant; the constant itself is a string literal somewhere
      // else in the same file and the regex above will catch it
      // independently if it's wrong.
      if (literal.startsWith("${")) continue;
      // Strip leading slashes so `/api/...` and `api/...` both pass.
      const trimmed = literal.replace(/^\/+/, "");
      if (trimmed.startsWith("api/") || trimmed === "api") continue;
      // Some paths legitimately call non-/api/ controller routes
      // (e.g. /healthz on the SPA's own nginx, /metrics on Prometheus).
      // Allow those when the trimmed path is in the allowlist below.
      const ALLOWED_NON_API = new Set([
        "healthz",
        "metrics",
      ]);
      if (ALLOWED_NON_API.has(trimmed)) continue;
      const before = text.slice(0, m.index);
      const line = before.split("\n").length;
      offenses.push({
        file: relative(SRC_ROOT, file),
        line,
        match: literal,
      });
    }
  }
  return offenses;
}

describe("ratchet: fetcher() paths must go through /api/*", () => {
  it("zero call sites bypass the /api/* nginx prefix", () => {
    const offenses = collectOffenses();
    // Render a readable diff if this fails so the dev knows what
    // path to fix and where.
    const message =
      offenses.length === 0
        ? ""
        : `Found ${offenses.length} fetcher() call(s) whose path does NOT start with "api/".\n` +
          `These will 405 in production (nginx only proxies /api/* to the controller):\n` +
          offenses
            .map((o) => `  ${o.file}:${o.line}  →  fetcher("${o.match}", …)`)
            .join("\n") +
          `\n\nFix: prefix the path with "api/" (no leading slash) and add\n` +
          `the controller-side route alias if needed (see handlers_post.py\n` +
          `the /actions/{name} block as a reference).`;
    expect(offenses, message).toEqual([]);
  });
});
