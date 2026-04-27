/**
 * Pure helpers for the dashboard's PWA service worker.
 *
 * The SW fetches ``/sw-config.json`` from the controller on
 * install/update; the response shape is described by ``SwConfig``
 * below. All decision logic (should the SW serve the SPA shell for
 * THIS navigation? should it pass through to the network?) lives in
 * pure functions here so they can be unit-tested without needing
 * the ServiceWorkerGlobalScope.
 *
 * Why runtime config instead of build-time regex
 * ----------------------------------------------
 * The SW used to ship with hardcoded ``navigateFallbackDenylist``
 * regexes ("``/api/``" and "``/app/(?!media-stack-ui(?:/|$))``").
 * That coupled the bundle to the deployment shape — operators who
 * renamed the dashboard mount via the routing-admin page got the
 * old "SW hijacks sister apps" bug back.
 *
 * Pulling the patterns from the controller's routing engine means
 * the SW always tracks the live deployment. A single source of
 * truth, and a renamed prefix takes effect on the next SW update.
 */

/** Live shape of ``GET /sw-config.json``. */
export interface SwConfig {
  version: number;
  /** Dashboard's mount point, e.g. ``"/app/media-stack-ui"``. No
   *  trailing slash. */
  basepath: string;
  /** Regex strings the SW must NOT serve the SPA shell for. */
  denylist_patterns: string[];
  /** Convenience: every ``/app/<service>`` path the dashboard owns
   *  (today just the basepath; reserved for sub-apps). */
  allowed_app_prefixes: string[];
  /** Every other ``/app/<service>`` deployed alongside the
   *  dashboard. Surfaced for telemetry / future SW logic. */
  sister_app_prefixes: string[];
}

/** Defaults used when the endpoint is unreachable. Match the
 *  pre-runtime-config behavior so first-install offline still
 *  renders the SPA shell. */
export const SW_CONFIG_DEFAULTS: SwConfig = {
  version: 1,
  basepath: "/app/media-stack-ui",
  denylist_patterns: [
    "^/api/",
    "^/app/(?!media-stack-ui(?:/|$))",
  ],
  allowed_app_prefixes: ["/app/media-stack-ui"],
  sister_app_prefixes: [],
};

/** Best-effort fetch; falls back to defaults on any error so
 *  ``install`` never blocks. */
export async function fetchSwConfig(
  fetchImpl: typeof fetch,
): Promise<SwConfig> {
  try {
    const res = await fetchImpl("/sw-config.json", { cache: "no-store" });
    if (!res.ok) return SW_CONFIG_DEFAULTS;
    const raw = (await res.json()) as Partial<SwConfig>;
    return normalizeSwConfig(raw);
  } catch {
    return SW_CONFIG_DEFAULTS;
  }
}

/** Coerce a partially-populated payload into a full SwConfig. Any
 *  field that's missing or malformed gets the corresponding default. */
export function normalizeSwConfig(raw: Partial<SwConfig> | null | undefined): SwConfig {
  if (!raw || typeof raw !== "object") return SW_CONFIG_DEFAULTS;
  const basepath = typeof raw.basepath === "string" && raw.basepath
    ? trimTrailingSlash(raw.basepath)
    : SW_CONFIG_DEFAULTS.basepath;
  const denylist = Array.isArray(raw.denylist_patterns)
    ? raw.denylist_patterns.filter((p): p is string => typeof p === "string")
    : SW_CONFIG_DEFAULTS.denylist_patterns;
  const allowed = Array.isArray(raw.allowed_app_prefixes)
    ? raw.allowed_app_prefixes.filter((p): p is string => typeof p === "string")
    : [basepath];
  const sister = Array.isArray(raw.sister_app_prefixes)
    ? raw.sister_app_prefixes.filter((p): p is string => typeof p === "string")
    : [];
  return {
    version: typeof raw.version === "number" ? raw.version : 1,
    basepath,
    denylist_patterns: denylist,
    allowed_app_prefixes: allowed,
    sister_app_prefixes: sister,
  };
}

function trimTrailingSlash(p: string): string {
  return p.replace(/\/+$/, "");
}

/** Compile the regex strings into an array of ``RegExp``. Bad
 *  patterns are filtered out (they shouldn't ship from a healthy
 *  controller, but we don't want one bad regex to disable the
 *  whole denylist). */
export function compileDenylist(patterns: readonly string[]): RegExp[] {
  const out: RegExp[] = [];
  for (const p of patterns) {
    try {
      out.push(new RegExp(p));
    } catch {
      // Drop malformed; SW continues with the survivors.
    }
  }
  return out;
}

/**
 * Decide whether a navigation request should be served from the
 * SPA shell (``index.html`` cached by the SW) vs passed through to
 * the network.
 *
 * Rules:
 *
 *  1. Non-navigation requests: the SW's other route handlers
 *     handle them (precache, runtime caches). This function only
 *     answers "should I serve the SPA shell?".
 *
 *  2. URL outside the dashboard's basepath: pass through. Sister
 *     apps live at ``/app/<other>/`` and need their own backend.
 *
 *  3. URL matches any denylist regex: pass through. Covers
 *     ``/api/*`` (controller endpoints) and ``/app/<other>/*``
 *     (caught by the routing-engine-built pattern).
 *
 *  4. Otherwise: serve the SPA shell.
 */
export function shouldServeSpaShell(
  url: URL,
  config: SwConfig,
  denylist: readonly RegExp[],
): boolean {
  // Cross-origin → never our problem.
  if (typeof self !== "undefined" && "location" in self) {
    if (url.origin !== (self as { location: Location }).location.origin) {
      return false;
    }
  }
  // Outside our basepath → not our problem either.
  const base = config.basepath;
  if (base) {
    const inScope =
      url.pathname === base || url.pathname.startsWith(base + "/");
    if (!inScope) return false;
  }
  // Inside our scope but matches a denylist pattern → pass through.
  for (const re of denylist) {
    if (re.test(url.pathname)) return false;
  }
  return true;
}
