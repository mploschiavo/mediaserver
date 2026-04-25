import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { fetcher, onAuthEvent } from "./client";

/**
 * Regression test for the v1.3.4 "Logs page briefly shows then
 * disappears" bug. Cause: AppShell mounts <TriggeredBanner /> on
 * every page; it queries `/api/guardrails`; if that endpoint 401s
 * (admin-gated, mid-bootstrap, or envoy ext-authz hasn't been
 * told about the new path), the fetcher emits `unauthenticated`,
 * App.tsx sees it, and `window.location.replace("/app/authelia/")`
 * fires — the user is bounced off whatever page they were on.
 *
 * Fix: opt-in `silenceAuthEvent: true` for advisory queries that
 * mount globally. A 401 from those does NOT trigger the redirect.
 * Mutations + primary content reads keep the original behaviour.
 *
 * These cases lock the contract.
 */
describe("fetcher — silenceAuthEvent option", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  const events: string[] = [];
  let unsubscribe: (() => void) | undefined;

  beforeEach(() => {
    events.length = 0;
    unsubscribe = onAuthEvent((e) => events.push(e));
    fetchMock = vi.fn(
      async () =>
        new Response('{"error":"nope"}', {
          status: 401,
          headers: { "content-type": "application/json" },
        }),
    );
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
  });

  afterEach(() => {
    unsubscribe?.();
  });

  it("emits `unauthenticated` on 401 by default (the existing redirect path)", async () => {
    await expect(fetcher("api/anything")).rejects.toThrow();
    expect(events).toEqual(["unauthenticated"]);
  });

  it("does NOT emit `unauthenticated` on 401 when silenceAuthEvent is true", async () => {
    await expect(
      fetcher("api/guardrails", { silenceAuthEvent: true }),
    ).rejects.toThrow();
    expect(events).toEqual([]);
  });

  it("still emits on 401 for the same path when silenceAuthEvent is false", async () => {
    await expect(
      fetcher("api/guardrails", { silenceAuthEvent: false }),
    ).rejects.toThrow();
    expect(events).toEqual(["unauthenticated"]);
  });

  it("the silenceAuthEvent flag does NOT leak into the fetch RequestInit", async () => {
    await expect(
      fetcher("api/guardrails", { silenceAuthEvent: true }),
    ).rejects.toThrow();
    const init = fetchMock.mock.calls[0]?.[1] as Record<string, unknown> | undefined;
    expect(init).toBeDefined();
    expect("silenceAuthEvent" in (init ?? {})).toBe(false);
  });
});
