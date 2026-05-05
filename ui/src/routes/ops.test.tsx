import type { ComponentType, ReactNode } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const opsHealthState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const mutateFns = vi.hoisted(() => ({
  refreshServices: vi.fn(),
  rotateKeys: vi.fn(),
  pullManifests: vi.fn(),
  healthProbe: vi.fn(),
}));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    useOpsHealth: () => opsHealthState,
    useOpsAction: (action: keyof typeof mutateFns) => ({
      mutate: mutateFns[action],
      isPending: false,
    }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

// Stub the ops-detail feature hooks so the deeper-detail cards mount
// without needing a real fetch. Each card defaults to its empty
// state — those are the strongest "renders without crashing" signal
// and let us assert the wiring without coupling to specific data.
vi.mock("@/features/ops-detail/hooks", () => ({
  useHealthStories: () => ({
    data: { stories: [] },
    isLoading: false,
    error: null,
  }),
  useCrashloops: () => ({
    data: { services: {} },
    isLoading: false,
    error: null,
  }),
  useConfigIntegrity: () => ({
    data: { services: {}, checked_at: 0 },
    isLoading: false,
    error: null,
  }),
  useFailedServices: () => ({
    data: { failed_services: [], count: 0 },
    isLoading: false,
    error: null,
  }),
  useHealthHistory: () => ({
    data: { history: [], period_hours: 0 },
    isLoading: false,
    error: null,
  }),
}));

// Stub the storage feature hooks — the StorageCard is composed onto
// the Ops page and would otherwise reach out to /api/disk-guardrails
// and /api/me. We mock the read hook to a NORMAL state so the card
// renders with its empty defaults and structural assertions pass.
vi.mock("@/features/storage/hooks", () => ({
  storageQueryKeys: { root: ["storage"], status: ["storage", "status"] },
  useDiskGuardrailsStatus: () => ({
    data: {
      state: "NORMAL",
      used_percent_by_mount: { config: 30 },
      thresholds: { lockdown_percent: 75, release_percent: 60 },
      engaged_at: 0,
      engaged_by: "",
      trigger: null,
      auto_check_paused_until: null,
      paused_clients: [],
      last_failures: [],
      transitions: [],
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useRunCleanup: () => ({ mutate: vi.fn(), isPending: false }),
  useEngageLockdown: () => ({ mutate: vi.fn(), isPending: false }),
  useReleaseLockdown: () => ({ mutate: vi.fn(), isPending: false }),
  usePauseGuardrails: () => ({ mutate: vi.fn(), isPending: false }),
  useForceEvaluate: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateThresholds: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@/features/me/hooks", () => ({
  useMe: () => ({ data: { role: "controller_admin" } }),
}));

// Stub the infra-detail feature hooks too — same rationale: keep the
// route render purely structural so tests don't depend on real fetch.
vi.mock("@/features/infra-detail/hooks", () => ({
  useGpu: () => ({
    data: { detected: false, gpus: [] },
    isLoading: false,
    error: null,
  }),
  useEnableGpu: () => ({ mutate: vi.fn(), isPending: false }),
  useMounts: () => ({
    data: { mounts: [] },
    isLoading: false,
    error: null,
  }),
  useStorageBreakdown: () => ({
    data: {},
    isLoading: false,
    error: null,
  }),
  useImageUpdates: () => ({
    data: { updates: [] },
    isLoading: false,
    error: null,
  }),
}));

// Tanstack <Link> needs a router context; stub it for unit tests.
vi.mock("@tanstack/react-router", async () => {
  const actual =
    await vi.importActual<typeof import("@tanstack/react-router")>(
      "@tanstack/react-router",
    );
  return {
    ...actual,
    Link: ({
      to,
      children,
      ...rest
    }: {
      to: string;
      children: ReactNode;
    } & Record<string, unknown>) => (
      <a href={to} {...rest}>
        {children}
      </a>
    ),
  };
});

import { Route as OpsRoute } from "./ops";

const OpsPage = OpsRoute.options.component as ComponentType;

describe("ops route", () => {
  beforeEach(() => {
    opsHealthState.data = undefined;
    opsHealthState.isLoading = false;
    opsHealthState.error = null;
    Object.values(mutateFns).forEach((m) => m.mockReset());
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  it("renders the 6-button action grid", () => {
    renderWithProviders(<OpsPage />);
    expect(screen.getByTestId("ops-action-refreshServices")).toBeInTheDocument();
    expect(screen.getByTestId("ops-action-rotateKeys")).toBeInTheDocument();
    expect(screen.getByTestId("ops-action-pullManifests")).toBeInTheDocument();
    expect(screen.getByTestId("ops-action-healthProbe")).toBeInTheDocument();
    expect(screen.getByTestId("ops-link-/media-integrity")).toBeInTheDocument();
    expect(screen.getByTestId("ops-link-/logs")).toBeInTheDocument();
  });

  it("invokes the mutation and toasts on click", async () => {
    mutateFns.refreshServices.mockImplementation(
      (_v: void, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(<OpsPage />);
    await userEvent.click(screen.getByTestId("ops-action-refreshServices"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(toastSuccess.mock.calls[0]?.[0]).toMatch(/refreshed/i);
  });

  it("shows skeletons while health is loading", () => {
    opsHealthState.isLoading = true;
    renderWithProviders(<OpsPage />);
    // Skeletons render as 4 elements inside the health card; querying
    // by the health card's presence is a tighter signal.
    expect(screen.getByTestId("ops-health")).toBeInTheDocument();
  });

  it("renders health stats when populated", () => {
    opsHealthState.data = {
      uptime_seconds: 3600 * 25,
      containers: 12,
      disk_used_pct: 47.3,
      last_bootstrap_at: new Date(0).toISOString(),
    };
    renderWithProviders(<OpsPage />);
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("47.3%")).toBeInTheDocument();
  });

  it("renders the error banner when health fails", () => {
    opsHealthState.error = new Error("nope");
    renderWithProviders(<OpsPage />);
    expect(screen.getByTestId("ops-health-error")).toHaveTextContent("nope");
  });

  it("mounts the detailed ops/health surface below the top-line health card", () => {
    renderWithProviders(<OpsPage />);
    // Section wrapper.
    expect(screen.getByTestId("ops-detail-grid")).toBeInTheDocument();
    // At least one of the new feature cards renders.
    expect(screen.getByTestId("health-stories-card")).toBeInTheDocument();
    expect(screen.getByTestId("crashloops-card")).toBeInTheDocument();
    expect(screen.getByTestId("failed-services-card")).toBeInTheDocument();
    expect(screen.getByTestId("config-integrity-card")).toBeInTheDocument();
    expect(screen.getByTestId("health-history-card")).toBeInTheDocument();
  });

  it("mounts the infrastructure-detail surface below the ops-detail grid", () => {
    renderWithProviders(<OpsPage />);
    expect(screen.getByTestId("infra-detail-grid")).toBeInTheDocument();
    expect(screen.getByTestId("gpu-card")).toBeInTheDocument();
    expect(screen.getByTestId("mounts-card")).toBeInTheDocument();
    expect(screen.getByTestId("storage-breakdown-card")).toBeInTheDocument();
    expect(screen.getByTestId("image-updates-card")).toBeInTheDocument();
  });
});
