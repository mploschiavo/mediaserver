import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  fetcher,
  getBaseUrl,
  onAuthEvent,
  setBaseUrl,
} from "./client";

type FetchSpy = ReturnType<typeof vi.fn>;

function jsonResponse(body: unknown, status = 200, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
    ...init,
  });
}

function textResponse(body: string, status = 200): Response {
  return new Response(body, {
    status,
    headers: { "content-type": "text/plain" },
  });
}

function rawJsonResponse(raw: string, status = 200): Response {
  return new Response(raw, {
    status,
    headers: { "content-type": "application/json" },
  });
}

function lastInit(spy: FetchSpy): RequestInit {
  const call = spy.mock.calls[spy.mock.calls.length - 1];
  return call?.[1] as RequestInit;
}

function lastHeaders(spy: FetchSpy): Headers {
  const init = lastInit(spy);
  return new Headers(init.headers);
}

function lastUrl(spy: FetchSpy): string {
  const call = spy.mock.calls[spy.mock.calls.length - 1];
  return String(call?.[0]);
}

describe("fetcher", () => {
  let spy: FetchSpy;

  beforeEach(() => {
    spy = vi.fn();
    vi.stubGlobal("fetch", spy);
    setBaseUrl("");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns parsed JSON on 200 OK", async () => {
    spy.mockResolvedValueOnce(jsonResponse({ status: "ok" }));
    const out = await fetcher<{ status: string }>("api/health");
    expect(out).toEqual({ status: "ok" });
  });

  it("threads credentials: same-origin on every request", async () => {
    spy.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await fetcher("api/health");
    expect(lastInit(spy).credentials).toBe("same-origin");
  });

  it("respects an explicit credentials override", async () => {
    spy.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await fetcher("api/health", { credentials: "include" });
    expect(lastInit(spy).credentials).toBe("include");
  });

  it("throws ApiError with status + body on 4xx", async () => {
    spy.mockResolvedValueOnce(jsonResponse({ error: "bad" }, 400));
    await expect(fetcher("api/x")).rejects.toMatchObject({
      name: "ApiError",
      status: 400,
      body: { error: "bad" },
      message: "bad",
    });
  });

  it("throws ApiError on 5xx with statusText fallback", async () => {
    spy.mockResolvedValueOnce(jsonResponse({}, 503));
    await expect(fetcher("api/x")).rejects.toBeInstanceOf(ApiError);
  });

  it("auto-generates Idempotency-Key on POST when omitted", async () => {
    spy.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await fetcher("api/x", { method: "POST" });
    const key = lastHeaders(spy).get("Idempotency-Key");
    expect(key).toBeTruthy();
    expect(key!.length).toBeGreaterThanOrEqual(16);
  });

  it("respects an explicit Idempotency-Key on POST", async () => {
    spy.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await fetcher("api/x", { method: "POST", idempotencyKey: "abc-123" });
    expect(lastHeaders(spy).get("Idempotency-Key")).toBe("abc-123");
  });

  it("opts out of Idempotency-Key when explicit empty string is passed", async () => {
    spy.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await fetcher("api/x", { method: "POST", idempotencyKey: "" });
    expect(lastHeaders(spy).get("Idempotency-Key")).toBeNull();
  });

  it("does not set Idempotency-Key on GET", async () => {
    spy.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await fetcher("api/x");
    expect(lastHeaders(spy).get("Idempotency-Key")).toBeNull();
  });

  it("attaches the idempotency key to ApiError on POST failure", async () => {
    spy.mockResolvedValueOnce(jsonResponse({ error: "boom" }, 500));
    let caught: unknown;
    try {
      await fetcher("api/x", { method: "POST", idempotencyKey: "k1" });
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).idempotencyKey).toBe("k1");
  });

  it("emits 'unauthenticated' event on 401", async () => {
    spy.mockResolvedValueOnce(jsonResponse({ error: "nope" }, 401));
    const listener = vi.fn();
    const off = onAuthEvent(listener);
    await expect(fetcher("api/x")).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledWith("unauthenticated");
    off();
  });

  it("does not emit auth event on 200", async () => {
    spy.mockResolvedValueOnce(jsonResponse({ ok: true }));
    const listener = vi.fn();
    const off = onAuthEvent(listener);
    await fetcher("api/x");
    expect(listener).not.toHaveBeenCalled();
    off();
  });

  it("auth listener can be unsubscribed", async () => {
    spy.mockResolvedValueOnce(jsonResponse({}, 401));
    spy.mockResolvedValueOnce(jsonResponse({}, 401));
    const listener = vi.fn();
    const off = onAuthEvent(listener);
    await fetcher("api/x").catch(() => undefined);
    off();
    await fetcher("api/x").catch(() => undefined);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("sets Accept: application/json by default", async () => {
    spy.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await fetcher("api/x");
    expect(lastHeaders(spy).get("Accept")).toBe("application/json");
  });

  it("sets Content-Type when a body is provided", async () => {
    spy.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await fetcher("api/x", { method: "POST", body: JSON.stringify({}) });
    expect(lastHeaders(spy).get("Content-Type")).toBe("application/json");
  });

  it("preserves caller-provided Content-Type", async () => {
    spy.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await fetcher("api/x", {
      method: "POST",
      body: "x=1",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });
    expect(lastHeaders(spy).get("Content-Type")).toBe(
      "application/x-www-form-urlencoded",
    );
  });

  it("returns undefined for 204 No Content", async () => {
    spy.mockResolvedValueOnce(new Response(null, { status: 204 }));
    const out = await fetcher<undefined>("api/x", { method: "POST" });
    expect(out).toBeUndefined();
  });

  it("returns text body when content-type is not JSON", async () => {
    spy.mockResolvedValueOnce(textResponse("plain text"));
    const out = await fetcher<string>("api/plain");
    expect(out).toBe("plain text");
  });

  it("surfaces JSON parse failure as ApiError, not unhandled", async () => {
    spy.mockResolvedValueOnce(rawJsonResponse("{not-json"));
    await expect(fetcher("api/x")).rejects.toBeInstanceOf(ApiError);
  });

  it("setBaseUrl prepends to relative paths", async () => {
    setBaseUrl("https://example.test/");
    spy.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await fetcher("api/health");
    expect(lastUrl(spy)).toBe("https://example.test/api/health");
    expect(getBaseUrl()).toBe("https://example.test");
  });

  it("setBaseUrl strips a leading slash from path", async () => {
    setBaseUrl("https://example.test");
    spy.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await fetcher("/api/health");
    expect(lastUrl(spy)).toBe("https://example.test/api/health");
  });

  it("ApiError exposes status, body, and idempotencyKey when present", () => {
    const err = new ApiError("nope", 418, { teapot: true }, "key-1");
    expect(err.status).toBe(418);
    expect(err.body).toEqual({ teapot: true });
    expect(err.idempotencyKey).toBe("key-1");
  });

  it("listener exceptions do not break the request", async () => {
    spy.mockResolvedValueOnce(jsonResponse({}, 401));
    const off = onAuthEvent(() => {
      throw new Error("listener boom");
    });
    await expect(fetcher("api/x")).rejects.toBeInstanceOf(ApiError);
    off();
  });
});
