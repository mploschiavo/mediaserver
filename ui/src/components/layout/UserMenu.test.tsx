import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";

const navigateMock = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigateMock,
}));

const toastErrorMock = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    error: (...args: unknown[]) => toastErrorMock(...args),
    success: vi.fn(),
  },
}));

import { UserMenu } from "./UserMenu";

const originalFetch = globalThis.fetch;

const replaceMock = vi.fn();

beforeEach(() => {
  navigateMock.mockReset();
  toastErrorMock.mockReset();
  replaceMock.mockReset();
  // happy-dom's window.location.replace would throw "not implemented".
  // Replace it with a spy so the sign-out path always navigates to
  // Authelia regardless of what the logout POST returned.
  try {
    Object.defineProperty(window.location, "replace", {
      configurable: true,
      writable: true,
      value: replaceMock,
    });
  } catch {
    (window.location as unknown as { replace: (url: string) => void }).replace = replaceMock;
  }
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("UserMenu", () => {
  it("renders the trigger with the user's display name", () => {
    render(<UserMenu name="Alice" email="alice@example.com" />);
    // Name shows beside the avatar.
    expect(screen.getByText("Alice")).toBeInTheDocument();
  });

  it("computes initials when there is no avatarUrl", async () => {
    const { container } = render(<UserMenu name="Alice Liddell" />);
    // Radix AvatarFallback waits its delayMs before mounting the
    // initials span. Wait for the trigger's textContent to include
    // "AL" rather than racing the timer.
    await new Promise((resolve) => setTimeout(resolve, 250));
    const trigger = container.querySelector(
      'button[aria-label="Open account menu"]',
    );
    expect(trigger?.textContent ?? "").toContain("AL");
  });

  it('falls back to "??" when the name has no parseable initials', async () => {
    const { container } = render(<UserMenu name="" />);
    await new Promise((resolve) => setTimeout(resolve, 250));
    const trigger = container.querySelector(
      'button[aria-label="Open account menu"]',
    );
    expect(trigger?.textContent ?? "").toContain("??");
  });

  it("opens the dropdown when the trigger is clicked", async () => {
    render(<UserMenu name="Alice" email="alice@example.com" />);
    await userEvent.click(
      screen.getByRole("button", { name: /open account menu/i }),
    );
    expect(await screen.findByText("My profile")).toBeInTheDocument();
    expect(screen.getByText("Documentation")).toBeInTheDocument();
    expect(screen.getByText("Sign out")).toBeInTheDocument();
  });

  it("My profile selects /me via navigate()", async () => {
    render(<UserMenu name="Alice" />);
    await userEvent.click(
      screen.getByRole("button", { name: /open account menu/i }),
    );
    await userEvent.click(await screen.findByText("My profile"));
    expect(navigateMock).toHaveBeenCalledWith({ to: "/me" });
  });

  it("Sign out POSTs to /api/auth/logout and navigates to Authelia portal", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200 }) as Response);
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
    render(<UserMenu name="Alice" />);
    await userEvent.click(
      screen.getByRole("button", { name: /open account menu/i }),
    );
    await userEvent.click(await screen.findByText("Sign out"));
    expect(fetchMock).toHaveBeenCalled();
    const firstCall = fetchMock.mock.calls[0] as [unknown, RequestInit | undefined] | undefined;
    const [url, init] = firstCall ?? [undefined, undefined];
    expect(String(url)).toContain("/api/auth/logout");
    expect(init?.method).toBe("POST");
    expect(replaceMock).toHaveBeenCalled();
    expect(String(replaceMock.mock.calls[0]?.[0] ?? "")).toContain(
      "/app/authelia/",
    );
  });

  it("Sign out still navigates to Authelia even when the logout POST 401s", async () => {
    // The "session already expired" case used to leave the user
    // stuck; now: ignore the POST result, navigate anyway.
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 401,
    }) as Response);
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
    render(<UserMenu name="Alice" />);
    await userEvent.click(
      screen.getByRole("button", { name: /open account menu/i }),
    );
    await userEvent.click(await screen.findByText("Sign out"));
    expect(replaceMock).toHaveBeenCalled();
    expect(toastErrorMock).not.toHaveBeenCalled();
  });

  it("Documentation menu item links to the docs URL", async () => {
    render(<UserMenu name="Alice" />);
    await userEvent.click(
      screen.getByRole("button", { name: /open account menu/i }),
    );
    const link = (await screen.findByText("Documentation")).closest("a");
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/mploschiavo/mediaserver",
    );
  });

  it("Support the project menu item points at the PayPal donate URL", async () => {
    render(<UserMenu name="Alice" />);
    await userEvent.click(
      screen.getByRole("button", { name: /open account menu/i }),
    );
    const link = (await screen.findByText("Support the project")).closest("a");
    expect(link).toHaveAttribute(
      "href",
      "https://www.paypal.com/donate?hosted_button_id=XKDG7XXVEQK3W",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link?.getAttribute("rel") ?? "").toMatch(/noopener/);
  });
});
