import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const statusState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
  refetch: vi.fn(),
}));

const meState = vi.hoisted(() => ({
  data: undefined as { role?: string } | undefined,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useDiskGuardrailsStatus: () => statusState,
    useRunCleanup: () => ({ mutate: vi.fn(), isPending: false }),
    useEngageLockdown: () => ({ mutate: vi.fn(), isPending: false }),
    useReleaseLockdown: () => ({ mutate: vi.fn(), isPending: false }),
    usePauseGuardrails: () => ({ mutate: vi.fn(), isPending: false }),
    useForceEvaluate: () => ({ mutate: vi.fn(), isPending: false }),
    useUpdateThresholds: () => ({ mutate: vi.fn(), isPending: false }),
  };
});

vi.mock("@/features/me/hooks", () => ({
  useMe: () => meState,
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    to: _to,
    ...rest
  }: {
    children: React.ReactNode;
    to?: string;
  } & Record<string, unknown>) => <a {...rest}>{children}</a>,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { StorageCard } from "./StorageCard";

beforeEach(() => {
  statusState.data = undefined;
  statusState.isLoading = false;
  statusState.error = null;
  statusState.refetch = vi.fn();
  meState.data = { role: "controller_admin" };
});

const baseStatus = {
  state: "NORMAL",
  used_percent_by_mount: { config: 42.1, data: 65.8 },
  thresholds: { lockdown_percent: 75, release_percent: 60 },
  engaged_at: 0,
  engaged_by: "",
  trigger: null,
  auto_check_paused_until: null,
  paused_clients: [],
  last_failures: [],
  transitions: [],
};

describe("StorageCard", () => {
  it("shows skeletons while loading", () => {
    statusState.isLoading = true;
    renderWithProviders(<StorageCard />);
    expect(screen.getByTestId("storage-card-loading")).toBeInTheDocument();
  });

  it("renders all sub-cards on success", () => {
    statusState.data = baseStatus;
    renderWithProviders(<StorageCard />);
    expect(screen.getByTestId("storage-status-header")).toBeInTheDocument();
    expect(screen.getByTestId("storage-action-buttons")).toBeInTheDocument();
    expect(
      screen.getByTestId("storage-threshold-inputs"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("storage-cleanup-policy"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("storage-transition-feed"),
    ).toBeInTheDocument();
  });

  it("invalidates the status query on a synthetic media-stack:event", async () => {
    statusState.data = baseStatus;
    const { queryClient } = renderWithProviders(<StorageCard />);
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    window.dispatchEvent(
      new CustomEvent("media-stack:event", {
        detail: { event_type: "storage.lockdown_engaged" },
      }),
    );
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(
        expect.objectContaining({ queryKey: ["storage", "status"] }),
      ),
    );
  });

  it("renders read-only buttons when role isn't admin", () => {
    statusState.data = baseStatus;
    meState.data = { role: "viewer" };
    renderWithProviders(<StorageCard />);
    expect(screen.getByTestId("storage-action-engage")).toBeDisabled();
    expect(screen.getByTestId("storage-threshold-save")).toBeDisabled();
  });

  it("renders an error tile when status fetch fails", () => {
    statusState.error = new Error("boom");
    renderWithProviders(<StorageCard />);
    expect(screen.getByTestId("storage-card-error")).toBeInTheDocument();
  });
});
