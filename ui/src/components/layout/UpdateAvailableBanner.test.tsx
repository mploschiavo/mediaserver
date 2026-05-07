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

// Use Vitest's official ``stubEnv`` API for ``import.meta.env`` —
// direct assignment to an env key with ``undefined`` doesn't clear
// the slot (the property stays defined with value ``undefined``,
// AND the trim/?? chain in the component reads back the prior
// non-empty value in some happy-dom builds), so the
// "build version isn't injected" branch couldn't be exercised.
// ``unstubAllEnvs`` in afterEach restores the runner's pristine
// view across tests.
function setBuildVersion(v: string | undefined): void {
  if (v === undefined) {
    vi.unstubAllEnvs();
  } else {
    vi.stubEnv("VITE_BUILD_VERSION", v);
  }
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
    vi.unstubAllEnvs();
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
    renderWithProviders(<UpdateAvailableBanner />);
    // Provider wrappers (TooltipProvider) can emit shadow DOM nodes,
    // so assert on the banner's own test-id rather than container.firstChild.
    expect(screen.queryByTestId("update-available-banner")).toBeNull();
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
