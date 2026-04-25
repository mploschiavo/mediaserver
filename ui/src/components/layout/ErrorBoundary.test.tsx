import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import type { ReactElement } from "react";

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
  },
}));

import { ErrorBoundary } from "./ErrorBoundary";

function Boom({ message = "kaboom" }: { message?: string }): ReactElement {
  throw new Error(message);
}

const originalReload = window.location.reload;
const originalClipboard = navigator.clipboard;
let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  toastSuccess.mockReset();
  toastError.mockReset();
  // Silence the React dev-mode unhandled-render error noise; the
  // boundary tests intentionally throw.
  consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  consoleErrorSpy.mockRestore();
  // Best-effort restore of the host singletons we monkey-patched.
  try {
    Object.defineProperty(window.location, "reload", {
      configurable: true,
      writable: true,
      value: originalReload,
    });
  } catch {
    // happy-dom occasionally seals the descriptor; tests below redefine it.
  }
  try {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      writable: true,
      value: originalClipboard,
    });
  } catch {
    // ignore — test cases reset what they need.
  }
});

describe("ErrorBoundary", () => {
  it("renders children when no error is thrown", () => {
    render(
      <ErrorBoundary>
        <span>healthy</span>
      </ErrorBoundary>,
    );
    expect(screen.getByText("healthy")).toBeInTheDocument();
    expect(screen.queryByText("Something broke")).not.toBeInTheDocument();
  });

  it("renders the default fallback panel when a child throws", () => {
    render(
      <ErrorBoundary>
        <Boom message="render exploded" />
      </ErrorBoundary>,
    );
    expect(
      screen.getByRole("heading", { name: "Something broke" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("error-boundary-message")).toHaveTextContent(
      "render exploded",
    );
    expect(
      screen.getByRole("button", { name: /reload page/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /copy diagnostics/i }),
    ).toBeInTheDocument();
  });

  it("renders a custom fallback when provided", () => {
    render(
      <ErrorBoundary fallback={<span>route-specific oops</span>}>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByText("route-specific oops")).toBeInTheDocument();
    expect(screen.queryByText("Something broke")).not.toBeInTheDocument();
  });

  it("truncates very long error messages to <=200 chars + ellipsis", () => {
    const long = "x".repeat(500);
    render(
      <ErrorBoundary>
        <Boom message={long} />
      </ErrorBoundary>,
    );
    const txt = screen.getByTestId("error-boundary-message").textContent ?? "";
    // 200 chars + a single ellipsis.
    expect(txt.length).toBe(201);
    expect(txt.endsWith("…")).toBe(true);
  });

  it("Reload page invokes window.location.reload()", async () => {
    const reloadMock = vi.fn();
    Object.defineProperty(window.location, "reload", {
      configurable: true,
      writable: true,
      value: reloadMock,
    });
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    await userEvent.click(screen.getByRole("button", { name: /reload page/i }));
    expect(reloadMock).toHaveBeenCalledOnce();
  });

  it("Copy diagnostics writes JSON payload to navigator.clipboard", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      writable: true,
      value: { writeText },
    });
    render(
      <ErrorBoundary>
        <Boom message="diag err" />
      </ErrorBoundary>,
    );
    await userEvent.click(
      screen.getByRole("button", { name: /copy diagnostics/i }),
    );
    await waitFor(() => expect(writeText).toHaveBeenCalledOnce());
    const firstCall = writeText.mock.calls[0] as [string] | undefined;
    const payload = JSON.parse(String(firstCall?.[0])) as {
      message: string;
      route: string;
      userAgent: string;
      ts: string;
    };
    expect(payload.message).toBe("diag err");
    expect(typeof payload.route).toBe("string");
    expect(typeof payload.userAgent).toBe("string");
    expect(typeof payload.ts).toBe("string");
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
  });

  it("Copy diagnostics surfaces an error toast when clipboard rejects", async () => {
    const writeText = vi.fn(async () => {
      throw new Error("denied");
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      writable: true,
      value: { writeText },
    });
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    await userEvent.click(
      screen.getByRole("button", { name: /copy diagnostics/i }),
    );
    await waitFor(() => expect(toastError).toHaveBeenCalled());
  });
});
