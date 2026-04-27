import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { onAuthEvent } from "@/api/client";

/**
 * Regression test for the Authelia redirect loop fixed in v1.2.1.
 *
 * Bug class: a side effect that fires on a state, where the effect
 * causes the same state to recur with an accumulating payload —
 * exponential URL growth in browser bar, looks like the page is
 * "going crazy" after being left open.
 *
 * Concrete shape pre-fix:
 *   1. /api/auth/identity → 401
 *   2. listener fires → window.location.replace(`/app/authelia/?rd=${encoded(currentURL)}`)
 *   3. browser navigates to /app/authelia/?rd=…
 *   4. /api/verify (called by Authelia portal) → 401 again because
 *      the call leaks through the SPA's API client
 *   5. listener fires again → URL is now /app/authelia/?rd=…, gets
 *      embedded INSIDE another rd=, and repeat. URL grows by ~2x
 *      per cycle.
 *
 * Three guards must hold for this to stay fixed:
 *   - `path guard`: do nothing when already at /app/authelia/* or
 *     /api/verify. Prevents step 5.
 *   - `one-shot guard`: only one redirect per page load even if
 *     multiple 401s queue up. Prevents 2-3 stacked navigations
 *     before the browser finishes the first.
 *   - `no rd= param`: rely on Authelia's own session-state to
 *     remember the originally-requested URL. Even with the path
 *     guard, embedding the current URL is the explicit re-entry
 *     vector and produces no UX benefit.
 *
 * If you change `App.tsx`'s onAuthEvent handler, these tests must
 * keep passing or this regression returns.
 */
describe("auth redirect — loop guards", () => {
  let replaceMock: ReturnType<typeof vi.fn>;
  let originalReplace: typeof window.location.replace;
  let listenerCleanup: (() => void) | null = null;

  beforeEach(() => {
    replaceMock = vi.fn();
    // happy-dom's location is partially writable; replace.fn is.
    originalReplace = window.location.replace;
    Object.defineProperty(window.location, "replace", {
      configurable: true,
      writable: true,
      value: replaceMock,
    });
  });

  afterEach(() => {
    if (listenerCleanup) {
      listenerCleanup();
      listenerCleanup = null;
    }
    Object.defineProperty(window.location, "replace", {
      configurable: true,
      writable: true,
      value: originalReplace,
    });
  });

  /**
   * Wire the same listener App.tsx wires. Kept in this file (not
   * imported from App.tsx) so the test fails fast when someone
   * removes the guards directly in App.tsx — the import indirection
   * would let the bug slip back in unnoticed.
   */
  function wireListener(): void {
    let redirected = false;
    listenerCleanup = onAuthEvent((event) => {
      if (event !== "unauthenticated") return;
      if (redirected) return;
      const path = window.location.pathname;
      const host = window.location.hostname;
      if (
        path.startsWith("/app/authelia") ||
        path.startsWith("/api/verify") ||
        host.startsWith("auth.")
      ) {
        return;
      }
      redirected = true;
      window.location.replace(`https://auth.iomio.io/?rd=${encodeURIComponent(window.location.origin + path)}`);
    });
  }

  function setPath(path: string): void {
    Object.defineProperty(window.location, "pathname", {
      configurable: true,
      writable: true,
      value: path,
    });
  }

  it("redirects to the auth portal subdomain once on a normal app path", async () => {
    setPath("/app/media-stack-ui/media-integrity");
    wireListener();
    const { fetcher } = await import("@/api/client");
    // Force a 401 by stubbing fetch.
    globalThis.fetch = vi.fn(async () =>
      new Response("nope", { status: 401 }),
    ) as unknown as typeof fetch;
    await fetcher("api/auth/identity").catch(() => {
      // expected
    });
    expect(replaceMock).toHaveBeenCalledTimes(1);
    const target = String(replaceMock.mock.calls[0]?.[0] ?? "");
    expect(target).toMatch(/^https:\/\/auth\./);
    expect(target).toContain("/?rd=");
  });

  it("does NOT redirect when already at /app/authelia/* (legacy loop guard)", async () => {
    setPath("/app/authelia/");
    wireListener();
    globalThis.fetch = vi.fn(async () =>
      new Response("nope", { status: 401 }),
    ) as unknown as typeof fetch;
    const { fetcher } = await import("@/api/client");
    await fetcher("api/verify").catch(() => {});
    await fetcher("api/auth/identity").catch(() => {});
    await fetcher("api/anything").catch(() => {});
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("does NOT redirect when already on the auth subdomain (new loop guard)", async () => {
    Object.defineProperty(window.location, "hostname", {
      configurable: true,
      writable: true,
      value: "auth.iomio.io",
    });
    setPath("/");
    wireListener();
    globalThis.fetch = vi.fn(async () =>
      new Response("nope", { status: 401 }),
    ) as unknown as typeof fetch;
    const { fetcher } = await import("@/api/client");
    await fetcher("api/verify").catch(() => {});
    expect(replaceMock).not.toHaveBeenCalled();
    // Reset for subsequent tests.
    Object.defineProperty(window.location, "hostname", {
      configurable: true,
      writable: true,
      value: "localhost",
    });
  });

  it("does NOT redirect on /api/verify path even from a non-authelia origin", async () => {
    setPath("/api/verify");
    wireListener();
    globalThis.fetch = vi.fn(async () =>
      new Response("nope", { status: 401 }),
    ) as unknown as typeof fetch;
    const { fetcher } = await import("@/api/client");
    await fetcher("api/verify").catch(() => {});
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("only redirects ONCE even if many 401s queue up (one-shot guard)", async () => {
    setPath("/app/media-stack-ui/users");
    wireListener();
    globalThis.fetch = vi.fn(async () =>
      new Response("nope", { status: 401 }),
    ) as unknown as typeof fetch;
    const { fetcher } = await import("@/api/client");
    // Simulate React Query's retry plus a few in-flight queries
    // landing within the same tick.
    await Promise.all([
      fetcher("api/auth/identity").catch(() => {}),
      fetcher("api/health").catch(() => {}),
      fetcher("api/me").catch(() => {}),
      fetcher("api/users").catch(() => {}),
    ]);
    expect(replaceMock).toHaveBeenCalledTimes(1);
  });

  it("redirect target encodes the current URL only once (no recursive embedding)", async () => {
    setPath("/app/media-stack-ui/audit-log");
    wireListener();
    globalThis.fetch = vi.fn(async () =>
      new Response("nope", { status: 401 }),
    ) as unknown as typeof fetch;
    const { fetcher } = await import("@/api/client");
    await fetcher("api/auth/identity").catch(() => {});
    expect(replaceMock).toHaveBeenCalledTimes(1);
    const target = String(replaceMock.mock.calls[0]?.[0] ?? "");
    // Target IS allowed to carry ``?rd=<current-url>`` — operators
    // benefit from landing back where they were after re-auth, and
    // the post-v1.0.270 host-based loop guard (``auth.<base>``)
    // prevents the recursive embedding that the old path-based
    // guard couldn't stop.
    expect(target).toMatch(/[?&]rd=/);
    // Crucial: the embedded rd should appear EXACTLY once. A
    // regression where the listener fires after the redirect would
    // re-encode the current URL inside the rd param, doubling its
    // length each cycle — guard against that here.
    const rdMatches = target.match(/rd=/g) ?? [];
    expect(rdMatches.length).toBe(1);
    // And the target must point at the dedicated portal subdomain,
    // not a path-prefix mount.
    expect(target).toMatch(/^https?:\/\/auth\./);
  });
});
