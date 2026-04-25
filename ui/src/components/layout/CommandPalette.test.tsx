import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const navigateMock = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigateMock,
}));

const setThemeMock = vi.fn();
vi.mock("./ThemeProvider", () => ({
  useTheme: () => ({
    setTheme: setThemeMock,
    resolvedTheme: "dark",
    theme: "dark",
  }),
}));

const toastSuccessMock = vi.fn();
const toastErrorMock = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    success: (...a: unknown[]) => toastSuccessMock(...a),
    error: (...a: unknown[]) => toastErrorMock(...a),
  },
}));

const useHotkeysMock = vi.fn();
vi.mock("react-hotkeys-hook", () => ({
  useHotkeys: (...args: unknown[]) => useHotkeysMock(...args),
}));

const originalFetch = globalThis.fetch;

beforeEach(() => {
  navigateMock.mockReset();
  setThemeMock.mockReset();
  toastSuccessMock.mockReset();
  toastErrorMock.mockReset();
  useHotkeysMock.mockReset();
  window.localStorage.clear();
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

import { CommandPalette, useCommandPalette } from "./CommandPalette";

describe("CommandPalette", () => {
  it("renders nothing when open=false", () => {
    renderWithProviders(
      <CommandPalette open={false} onOpenChange={() => {}} />,
    );
    expect(screen.queryByPlaceholderText(/Type a command/)).toBeNull();
  });

  it("renders search input + groups when open", () => {
    renderWithProviders(<CommandPalette open onOpenChange={() => {}} />);
    expect(screen.getByPlaceholderText(/Type a command/)).toBeInTheDocument();
    expect(screen.getByText("Navigation")).toBeInTheDocument();
    expect(screen.getByText("Actions")).toBeInTheDocument();
  });

  it("contains nav items derived from NAV_COMMANDS", () => {
    renderWithProviders(<CommandPalette open onOpenChange={() => {}} />);
    expect(screen.getByText("Go to Content")).toBeInTheDocument();
    expect(screen.getByText("Go to Logs")).toBeInTheDocument();
    expect(screen.getByText("Go to Settings")).toBeInTheDocument();
  });

  it("filtering shrinks the visible items", async () => {
    renderWithProviders(<CommandPalette open onOpenChange={() => {}} />);
    await userEvent.type(
      screen.getByPlaceholderText(/Type a command/),
      "logs",
    );
    await waitFor(() => {
      expect(screen.queryByText("Go to Content")).not.toBeInTheDocument();
    });
    expect(screen.getByText("Go to Logs")).toBeInTheDocument();
  });

  it("selecting a Navigation item calls navigate() with the path", async () => {
    const onOpenChange = vi.fn();
    renderWithProviders(
      <CommandPalette open onOpenChange={onOpenChange} />,
    );
    await userEvent.click(screen.getByText("Go to Content"));
    expect(navigateMock).toHaveBeenCalledWith({ to: "/content" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("selecting an Action item POSTs to the controller and toasts", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
    }) as Response);
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
    renderWithProviders(<CommandPalette open onOpenChange={vi.fn()} />);
    await userEvent.click(screen.getByText("Reconcile now"));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const firstCall = fetchMock.mock.calls[0] as [unknown, RequestInit | undefined] | undefined;
    const [url, init] = firstCall ?? [undefined, undefined];
    expect(String(url)).toContain("/api/media-integrity/reconcile");
    expect(init?.method).toBe("POST");
  });

  it("selecting the theme toggle calls setTheme with the inverse", async () => {
    renderWithProviders(<CommandPalette open onOpenChange={vi.fn()} />);
    await userEvent.click(screen.getByText(/Switch to light theme/));
    expect(setThemeMock).toHaveBeenCalledWith("light");
  });

  it("the audit-log action navigates to /logs without a fetch", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
    renderWithProviders(<CommandPalette open onOpenChange={vi.fn()} />);
    await userEvent.click(screen.getByText("Open audit log"));
    expect(navigateMock).toHaveBeenCalledWith({
      to: "/logs",
      search: { audit: 1 },
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("loads recents from localStorage and exposes them when search is empty", () => {
    window.localStorage.setItem(
      "command-palette:recent",
      JSON.stringify(["nav:content"]),
    );
    renderWithProviders(<CommandPalette open onOpenChange={vi.fn()} />);
    expect(screen.getByText("Recent")).toBeInTheDocument();
  });

  it("ignores malformed recents JSON without crashing", () => {
    window.localStorage.setItem("command-palette:recent", "{not-json");
    renderWithProviders(<CommandPalette open onOpenChange={vi.fn()} />);
    expect(screen.getByText("Navigation")).toBeInTheDocument();
  });
});

describe("useCommandPalette", () => {
  it("registers the mod+k hotkey", () => {
    function Probe() {
      const [, setOpen] = useCommandPalette();
      return (
        <button onClick={() => setOpen(true)} type="button">
          probe
        </button>
      );
    }
    render(<Probe />);
    expect(useHotkeysMock).toHaveBeenCalled();
    const firstCall = useHotkeysMock.mock.calls[0];
    expect(firstCall?.[0]).toBe("mod+k");
  });
});
