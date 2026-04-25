import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { ConnectionStatus } from "./ConnectionStatus";

const originalFetch = globalThis.fetch;

function makeResponse(ok: boolean, status = ok ? 200 : 500): Response {
  return {
    ok,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => ({ ok }),
    text: async () => (ok ? "ok" : "fail"),
  } as unknown as Response;
}

function stubFetch(impl: () => Promise<Response>) {
  globalThis.fetch = vi.fn(impl as unknown as typeof globalThis.fetch);
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("ConnectionStatus", () => {
  it("renders a button with an aria-label that mentions controller state", async () => {
    stubFetch(async () => makeResponse(true));
    renderWithProviders(<ConnectionStatus />);
    const btn = await screen.findByRole("button");
    expect(btn).toBeInTheDocument();
    expect(btn.getAttribute("aria-label") ?? "").toMatch(/Controller/i);
  });

  it("paints the live (green) dot when /api/health returns ok", async () => {
    stubFetch(async () => makeResponse(true));
    const { container } = renderWithProviders(<ConnectionStatus />);
    await waitFor(() => {
      expect(container.querySelector(".bg-success")).not.toBeNull();
    });
  });

  it("paints the dead (red) dot when /api/health throws", async () => {
    stubFetch(async () => {
      throw new Error("network down");
    });
    const { container } = renderWithProviders(<ConnectionStatus />);
    await waitFor(() => {
      expect(container.querySelector(".bg-danger")).not.toBeNull();
    });
  });

  it("renders some status dot regardless of fetch outcome", async () => {
    stubFetch(async () => makeResponse(false, 503));
    const { container } = renderWithProviders(<ConnectionStatus />);
    await waitFor(() => {
      const hasAny =
        container.querySelector(".bg-success") ||
        container.querySelector(".bg-warning") ||
        container.querySelector(".bg-danger");
      expect(hasAny).not.toBeNull();
    });
  });

  it("calls /api/health to perform its poll", async () => {
    const fetchMock = vi.fn(async () => makeResponse(true));
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
    renderWithProviders(<ConnectionStatus />);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const firstCall = fetchMock.mock.calls[0] as [unknown] | undefined;
    const firstCallUrl = firstCall?.[0];
    expect(String(firstCallUrl)).toContain("/api/health");
  });
});
