import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

// All branches of UpdateAvailableBanner pivot on:
//   1. `useStackUpdate()`'s returned `current_version` (the controller's
//      *running* release, threaded through `/api/stack/update`), and
//   2. `import.meta.env.VITE_BUILD_VERSION` (baked at SPA build time).
// Stub the hook directly so each test owns its own pair.
const updateState = vi.hoisted(() => ({
  data: undefined as
    | { available: boolean; current_version?: string; latest_version?: string }
    | undefined,
  error: null as Error | null,
  isLoading: false,
  refetch: vi.fn(async () => undefined),
}));

vi.mock("@/features/stack-lifecycle/hooks", () => ({
  useStackUpdate: () => ({
    data: updateState.data,
    error: updateState.error,
    isLoading: updateState.isLoading,
    refetch: updateState.refetch,
  }),
}));

import { UpdateAvailableBanner } from "./UpdateAvailableBanner";

// Vitest rewrites `import.meta.env` to a writable object on the test
// runner, so we can flip VITE_BUILD_VERSION per-test. Saving + restoring
// across the suite keeps tests order-independent.
const ORIGINAL_BUILD_VERSION = import.meta.env.VITE_BUILD_VERSION;

function setBuildVersion(v: string | undefined): void {
  // Cast through `Record<string, unknown>` so we can set a typed
  // env field at runtime; the readonly modifier is a TS-only concern.
  (import.meta.env as unknown as Record<string, string | undefined>)
    .VITE_BUILD_VERSION = v;
}

describe("UpdateAvailableBanner", () => {
  beforeEach(() => {
    updateState.data = undefined;
    updateState.error = null;
    updateState.isLoading = false;
    updateState.refetch.mockReset();
    updateState.refetch.mockResolvedValue(undefined);
    setBuildVersion("1.0.233");
  });

  afterEach(() => {
    setBuildVersion(ORIGINAL_BUILD_VERSION);
  });

  it("renders nothing while the update probe is loading", () => {
    updateState.isLoading = true;
    const { container } = renderWithProviders(<UpdateAvailableBanner />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when the update probe errors", () => {
    updateState.error = new Error("offline");
    const { container } = renderWithProviders(<UpdateAvailableBanner />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when the build version isn't injected", () => {
    setBuildVersion(undefined);
    updateState.data = { available: false, current_version: "1.0.234" };
    const { container } = renderWithProviders(<UpdateAvailableBanner />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when the controller's running version is missing", () => {
    updateState.data = { available: false };
    const { container } = renderWithProviders(<UpdateAvailableBanner />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when build and running versions match", () => {
    updateState.data = { available: false, current_version: "1.0.233" };
    const { container } = renderWithProviders(<UpdateAvailableBanner />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the banner when the running version moves past the build version", () => {
    updateState.data = { available: false, current_version: "1.0.234" };
    renderWithProviders(<UpdateAvailableBanner />);
    expect(screen.getByTestId("update-available-banner")).toBeInTheDocument();
    expect(
      screen.getByTestId("update-available-banner-version"),
    ).toHaveTextContent("1.0.234");
  });

  it("surfaces a Refresh button that unregisters the SW and reloads", async () => {
    updateState.data = { available: false, current_version: "1.0.234" };

    // Stub navigator.serviceWorker.getRegistration → unregister(). The
    // banner should call both before triggering a reload.
    const unregister = vi.fn(async () => true);
    const getRegistration = vi.fn(async () => ({ unregister }));
    Object.defineProperty(globalThis.navigator, "serviceWorker", {
      configurable: true,
      value: { getRegistration },
    });

    // Stub window.location.reload — happy-dom's default reload throws
    // "Not implemented", which would mask the test signal.
    const reload = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, reload },
    });

    renderWithProviders(<UpdateAvailableBanner />);
    await userEvent.click(
      screen.getByTestId("update-available-banner-refresh"),
    );

    await waitFor(() => expect(getRegistration).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(unregister).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
  });

  it("still reloads when no SW is registered (getRegistration → undefined)", async () => {
    updateState.data = { available: false, current_version: "1.0.234" };

    const getRegistration = vi.fn(async () => undefined);
    Object.defineProperty(globalThis.navigator, "serviceWorker", {
      configurable: true,
      value: { getRegistration },
    });

    const reload = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, reload },
    });

    renderWithProviders(<UpdateAvailableBanner />);
    await userEvent.click(
      screen.getByTestId("update-available-banner-refresh"),
    );
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
  });

  it("falls through to a plain reload when the SW APIs are missing", async () => {
    updateState.data = { available: false, current_version: "1.0.234" };

    // Drop navigator.serviceWorker entirely.
    Object.defineProperty(globalThis.navigator, "serviceWorker", {
      configurable: true,
      value: undefined,
    });

    const reload = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, reload },
    });

    renderWithProviders(<UpdateAvailableBanner />);
    await userEvent.click(
      screen.getByTestId("update-available-banner-refresh"),
    );
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
  });

  it("trims whitespace before comparing versions to avoid false-positives", () => {
    updateState.data = { available: false, current_version: "  1.0.233  " };
    setBuildVersion("1.0.233");
    const { container } = renderWithProviders(<UpdateAvailableBanner />);
    expect(container.firstChild).toBeNull();
  });
});
