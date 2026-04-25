import { afterEach, describe, expect, it, vi } from "vitest";

import { newIdempotencyKey } from "./idempotency";

describe("newIdempotencyKey", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses crypto.randomUUID when available", () => {
    const fakeCrypto = { randomUUID: () => "uuid-from-crypto" };
    vi.stubGlobal("crypto", fakeCrypto);
    expect(newIdempotencyKey()).toBe("uuid-from-crypto");
  });

  it("falls back to Math.random hex when randomUUID is missing", () => {
    vi.stubGlobal("crypto", {});
    const key = newIdempotencyKey();
    expect(key).toMatch(/^[0-9a-f]{32}$/);
  });

  it("returns distinct keys across calls", () => {
    // Force the fallback path so we exercise the real generator.
    vi.stubGlobal("crypto", {});
    const a = newIdempotencyKey();
    const b = newIdempotencyKey();
    expect(a).not.toBe(b);
  });
});
