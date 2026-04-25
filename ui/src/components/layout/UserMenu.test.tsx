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

beforeEach(() => {
  navigateMock.mockReset();
  toastErrorMock.mockReset();
  // happy-dom's window.location.reload would throw "not implemented".
  // Replace it with a no-op spy so the success path doesn't blow up.
  try {
    Object.defineProperty(window.location, "reload", {
      configurable: true,
      writable: true,
      value: vi.fn(),
    });
  } catch {
    // If the host already exposes a writable .reload, just overwrite.
    (window.location as unknown as { reload: () => void }).reload = vi.fn();
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

  it("Sign out calls /api/auth/logout and reloads on success", async () => {
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
  });

  it("Sign out toasts an error when the logout request fails", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 500,
    }) as Response);
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
    render(<UserMenu name="Alice" />);
    await userEvent.click(
      screen.getByRole("button", { name: /open account menu/i }),
    );
    await userEvent.click(await screen.findByText("Sign out"));
    // Wait for the error path.
    await screen.findByText("Sign out");
    expect(toastErrorMock).toHaveBeenCalled();
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
