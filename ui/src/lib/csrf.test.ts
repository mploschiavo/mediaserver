import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { fetcher } from "@/api/client";

/**
 * Regression test for the CSRF token bug fixed in v1.3.3.
 *
 * Bug class: the controller's `core/auth/csrf.py` enforces double-
 * submit CSRF on every mutating method (POST/PUT/PATCH/DELETE)
 * outside the explicit exempt list. The protector reads the
 * `media_stack_csrf` cookie, expects the same value back as the
 * `X-CSRF-Token` header, and 403s on mismatch.
 *
 * The SPA's `fetcher` was sending `Idempotency-Key` and
 * `credentials: "same-origin"` — but never the `X-CSRF-Token`
 * header. So every controller-issued mutation (Snapshots → "Take
 * snapshot now", Reconcile, Add ban, Generate token, …) was
 * silently 403ing with "CSRF token missing or invalid" the first
 * time an operator clicked it.
 *
 * Why no test caught it pre-1.3.3:
 *   1. Unit tests stub `globalThis.fetch` directly, so the request
 *      bypasses any cookie/CSRF middleware. They assert the URL
 *      and body, not the headers.
 *   2. Playwright e2e specs covered the visual flow but not the
 *      request shape against a real CSRF-enforcing server.
 *   3. The OpenAPI shape contract locked field names, not headers.
 *
 * The fix: `fetcher` reads the cookie before every mutating call
 * and copies the value into the header. This test locks that
 * behaviour at the request-shape level so future refactors of
 * `client.ts` can't drop the header again.
 */
describe("CSRF — fetcher echoes the cookie's token in X-CSRF-Token", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let originalCookie: PropertyDescriptor | undefined;

  beforeEach(() => {
    fetchMock = vi.fn(
      async () => new Response("{}", { status: 200 }),
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
    // Force a known cookie value for the test. happy-dom's
    // document.cookie is mutable but we reset it each time.
    originalCookie = Object.getOwnPropertyDescriptor(document, "cookie");
    Object.defineProperty(document, "cookie", {
      configurable: true,
      get: () => "media_stack_csrf=token-abc-123; foo=bar",
      set: () => undefined,
    });
  });

  afterEach(() => {
    if (originalCookie) {
      Object.defineProperty(document, "cookie", originalCookie);
    }
  });

  function lastInit(): RequestInit {
    return (fetchMock.mock.calls[0]?.[1] ?? {}) as RequestInit;
  }
  function lastHeaderValue(name: string): string | null {
    const init = lastInit();
    const h = init.headers;
    if (!h) return null;
    if (h instanceof Headers) return h.get(name);
    return ((h as unknown as Record<string, string>)[name] ?? null);
  }

  it("adds X-CSRF-Token to a POST mutation", async () => {
    await fetcher("api/snapshot", {
      method: "POST",
      body: JSON.stringify({}),
    });
    expect(lastHeaderValue("X-CSRF-Token")).toBe("token-abc-123");
  });

  it("adds X-CSRF-Token to PUT / PATCH / DELETE too", async () => {
    for (const method of ["PUT", "PATCH", "DELETE"]) {
      fetchMock.mockClear();
      await fetcher("api/foo", { method });
      expect(lastHeaderValue("X-CSRF-Token")).toBe("token-abc-123");
    }
  });

  it("does NOT add X-CSRF-Token on a GET", async () => {
    await fetcher("api/health", { method: "GET" });
    expect(lastHeaderValue("X-CSRF-Token")).toBeNull();
  });

  it("respects an explicit caller-supplied X-CSRF-Token override", async () => {
    await fetcher("api/snapshot", {
      method: "POST",
      headers: { "X-CSRF-Token": "explicit-override" },
    });
    expect(lastHeaderValue("X-CSRF-Token")).toBe("explicit-override");
  });

  it("omits the header when the cookie is absent (backend will 403, by design)", async () => {
    Object.defineProperty(document, "cookie", {
      configurable: true,
      get: () => "foo=bar",
      set: () => undefined,
    });
    await fetcher("api/snapshot", { method: "POST" });
    expect(lastHeaderValue("X-CSRF-Token")).toBeNull();
  });
});
