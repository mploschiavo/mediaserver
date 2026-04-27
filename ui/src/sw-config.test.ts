import { describe, expect, it, vi } from "vitest";
import {
  compileDenylist,
  fetchSwConfig,
  normalizeSwConfig,
  shouldServeSpaShell,
  SW_CONFIG_DEFAULTS,
  type SwConfig,
} from "./sw-config";

/**
 * Unit tests for the dashboard SW's pure helper module. The SW
 * itself can't be tested without a ServiceWorkerGlobalScope; the
 * decision logic is here (so tests are cheap) and the SW just
 * wires events to these functions.
 *
 * Coverage targets every branch of:
 *   - normalizeSwConfig (8 branches around malformed payloads)
 *   - fetchSwConfig (HTTP errors + JSON errors fall back to defaults)
 *   - compileDenylist (good/bad regex)
 *   - shouldServeSpaShell (origin / scope / denylist branches)
 */

describe("normalizeSwConfig", () => {
  it("returns defaults for null", () => {
    expect(normalizeSwConfig(null)).toEqual(SW_CONFIG_DEFAULTS);
  });

  it("returns defaults for undefined", () => {
    expect(normalizeSwConfig(undefined)).toEqual(SW_CONFIG_DEFAULTS);
  });

  it("returns defaults for non-object", () => {
    // The signature blocks this, but a malformed JSON parse could
    // still slip through at runtime.
    expect(
      normalizeSwConfig("nope" as unknown as Partial<SwConfig>),
    ).toEqual(SW_CONFIG_DEFAULTS);
  });

  it("trims trailing slashes from basepath", () => {
    const out = normalizeSwConfig({
      basepath: "/app/media-stack-ui///",
    });
    expect(out.basepath).toBe("/app/media-stack-ui");
  });

  it("falls back to default basepath on empty/wrong-type input", () => {
    expect(normalizeSwConfig({ basepath: "" }).basepath).toBe(
      SW_CONFIG_DEFAULTS.basepath,
    );
    expect(
      normalizeSwConfig({
        basepath: 42 as unknown as string,
      }).basepath,
    ).toBe(SW_CONFIG_DEFAULTS.basepath);
  });

  it("filters non-string entries from denylist_patterns", () => {
    const out = normalizeSwConfig({
      denylist_patterns: [
        "^/api/",
        42 as unknown as string,
        null as unknown as string,
        "^/admin/",
      ],
    });
    expect(out.denylist_patterns).toEqual(["^/api/", "^/admin/"]);
  });

  it("falls back to default denylist when not array", () => {
    const out = normalizeSwConfig({
      denylist_patterns: "not-an-array" as unknown as string[],
    });
    expect(out.denylist_patterns).toEqual(
      SW_CONFIG_DEFAULTS.denylist_patterns,
    );
  });

  it("derives allowed_app_prefixes from basepath when missing", () => {
    const out = normalizeSwConfig({
      basepath: "/custom/dashboard",
    });
    expect(out.allowed_app_prefixes).toEqual(["/custom/dashboard"]);
  });

  it("preserves explicit allowed_app_prefixes", () => {
    const out = normalizeSwConfig({
      basepath: "/app/media-stack-ui",
      allowed_app_prefixes: ["/app/media-stack-ui", "/app/sub-app"],
    });
    expect(out.allowed_app_prefixes).toEqual([
      "/app/media-stack-ui",
      "/app/sub-app",
    ]);
  });

  it("preserves sister_app_prefixes (filtering non-strings)", () => {
    const out = normalizeSwConfig({
      sister_app_prefixes: [
        "/app/sonarr",
        null as unknown as string,
        "/app/jellyfin",
      ],
    });
    expect(out.sister_app_prefixes).toEqual([
      "/app/sonarr",
      "/app/jellyfin",
    ]);
  });

  it("defaults sister_app_prefixes to empty array", () => {
    expect(normalizeSwConfig({}).sister_app_prefixes).toEqual([]);
  });

  it("preserves an explicit version", () => {
    expect(normalizeSwConfig({ version: 7 }).version).toBe(7);
  });

  it("defaults version to 1 when missing or wrong type", () => {
    expect(normalizeSwConfig({}).version).toBe(1);
    expect(
      normalizeSwConfig({
        version: "1" as unknown as number,
      }).version,
    ).toBe(1);
  });
});

describe("fetchSwConfig", () => {
  it("returns parsed payload on 200", async () => {
    const fakeFetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        version: 1,
        basepath: "/dashboard",
        denylist_patterns: ["^/api/"],
        allowed_app_prefixes: ["/dashboard"],
        sister_app_prefixes: [],
      }),
    })) as unknown as typeof fetch;

    const out = await fetchSwConfig(fakeFetch);
    expect(out.basepath).toBe("/dashboard");
    expect(out.denylist_patterns).toEqual(["^/api/"]);
  });

  it("falls back on non-ok HTTP status", async () => {
    const fakeFetch = vi.fn(async () => ({
      ok: false,
      json: async () => ({}),
    })) as unknown as typeof fetch;

    expect(await fetchSwConfig(fakeFetch)).toEqual(SW_CONFIG_DEFAULTS);
  });

  it("falls back on fetch throw (network error)", async () => {
    const fakeFetch = vi.fn(async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch;

    expect(await fetchSwConfig(fakeFetch)).toEqual(SW_CONFIG_DEFAULTS);
  });

  it("falls back on JSON parse error", async () => {
    const fakeFetch = vi.fn(async () => ({
      ok: true,
      json: async () => {
        throw new Error("bad json");
      },
    })) as unknown as typeof fetch;

    expect(await fetchSwConfig(fakeFetch)).toEqual(SW_CONFIG_DEFAULTS);
  });

  it("uses the no-store cache directive on the fetch call", async () => {
    const fakeFetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({}),
    })) as unknown as typeof fetch;

    await fetchSwConfig(fakeFetch);
    const opts = (fakeFetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]?.[1] as RequestInit;
    expect(opts).toBeDefined();
    expect(opts.cache).toBe("no-store");
  });
});

describe("compileDenylist", () => {
  it("compiles valid regexes", () => {
    const list = compileDenylist(["^/api/", "^/admin/"]);
    expect(list).toHaveLength(2);
    expect(list[0]?.test("/api/foo")).toBe(true);
    expect(list[1]?.test("/admin/foo")).toBe(true);
  });

  it("drops invalid regex without failing", () => {
    const list = compileDenylist(["^/api/", "[unclosed", "^/admin/"]);
    expect(list).toHaveLength(2);
    expect(list[0]?.test("/api/foo")).toBe(true);
    expect(list[1]?.test("/admin/foo")).toBe(true);
  });

  it("returns empty for empty input", () => {
    expect(compileDenylist([])).toEqual([]);
  });

  it("returns empty when all patterns are invalid", () => {
    expect(compileDenylist(["[bad", "(also-bad"])).toEqual([]);
  });
});

describe("shouldServeSpaShell", () => {
  const config: SwConfig = {
    version: 1,
    basepath: "/app/media-stack-ui",
    denylist_patterns: [
      "^/api/",
      "^/app/(?!media-stack-ui(?:/|$))",
    ],
    allowed_app_prefixes: ["/app/media-stack-ui"],
    sister_app_prefixes: ["/app/sonarr", "/app/jellyfin"],
  };
  const denylist = compileDenylist(config.denylist_patterns);

  // Use the test environment's own origin so the function's
  // cross-origin guard doesn't reject our test URLs. happy-dom sets
  // ``self.location`` to ``http://localhost:3000`` by default.
  const ORIGIN =
    typeof self !== "undefined" && "location" in self
      ? (self as unknown as { location: Location }).location.origin
      : "http://localhost:3000";
  const u = (path: string) => new URL(path, ORIGIN);

  it("serves SPA shell for the basepath itself", () => {
    expect(
      shouldServeSpaShell(u("/app/media-stack-ui"), config, denylist),
    ).toBe(true);
  });

  it("serves SPA shell for deep links inside the basepath", () => {
    expect(
      shouldServeSpaShell(u("/app/media-stack-ui/me"), config, denylist),
    ).toBe(true);
    expect(
      shouldServeSpaShell(
        u("/app/media-stack-ui/jobs?focus=x"),
        config,
        denylist,
      ),
    ).toBe(true);
  });

  it("passes through sister-app paths", () => {
    expect(
      shouldServeSpaShell(u("/app/sonarr/series/1"), config, denylist),
    ).toBe(false);
    expect(
      shouldServeSpaShell(u("/app/jellyfin/web/"), config, denylist),
    ).toBe(false);
  });

  it("passes through /api/* paths via a direct-path probe", () => {
    expect(shouldServeSpaShell(u("/api/me"), config, denylist)).toBe(false);
  });

  it("passes through paths outside the basepath", () => {
    expect(
      shouldServeSpaShell(u("/sw-config.json"), config, denylist),
    ).toBe(false);
    expect(
      shouldServeSpaShell(u("/manifest.webmanifest"), config, denylist),
    ).toBe(false);
  });

  it("no basepath = always check denylist", () => {
    const rootConfig: SwConfig = {
      ...config,
      basepath: "",
      denylist_patterns: ["^/api/"],
    };
    const rootDeny = compileDenylist(rootConfig.denylist_patterns);
    expect(
      shouldServeSpaShell(u("/me"), rootConfig, rootDeny),
    ).toBe(true);
    expect(
      shouldServeSpaShell(u("/api/me"), rootConfig, rootDeny),
    ).toBe(false);
  });

  it("rejects URLs that look like the basepath but extend it", () => {
    expect(
      shouldServeSpaShell(
        u("/app/media-stack-ui-other"),
        config,
        denylist,
      ),
    ).toBe(false);
  });

  it("rejects cross-origin URLs", () => {
    // Build a different-origin URL by swapping protocol/host.
    const otherOrigin = "https://attacker.example/app/media-stack-ui/me";
    expect(
      shouldServeSpaShell(new URL(otherOrigin), config, denylist),
    ).toBe(false);
  });
});
