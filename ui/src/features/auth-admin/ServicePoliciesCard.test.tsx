import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const policiesState = vi.hoisted(() => ({
  data: undefined as { services?: Record<string, unknown> } | undefined,
  isLoading: false,
  error: null as Error | null,
}));

const updateMutate = vi.hoisted(() => vi.fn());
const updatePending = vi.hoisted(() => ({ value: false }));

vi.mock("./hooks", () => ({
  useServicePolicies: () => policiesState,
  useUpdateAuthConfig: () => ({
    mutate: updateMutate,
    isPending: updatePending.value,
  }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { ServicePoliciesCard } from "./ServicePoliciesCard";

beforeEach(() => {
  policiesState.data = { services: {} };
  policiesState.isLoading = false;
  policiesState.error = null;
  updateMutate.mockReset();
  updatePending.value = false;
});

describe("ServicePoliciesCard", () => {
  it("renders the empty state when there are no services", () => {
    renderWithProviders(<ServicePoliciesCard />);
    expect(screen.getByText(/No services registered/i)).toBeInTheDocument();
  });

  it("renders a loading skeleton while the query resolves", () => {
    policiesState.isLoading = true;
    policiesState.data = undefined;
    renderWithProviders(<ServicePoliciesCard />);
    expect(screen.getByTestId("policies-loading")).toBeInTheDocument();
  });

  it("renders an error message when the query fails", () => {
    policiesState.error = new Error("auth gone");
    policiesState.data = undefined;
    renderWithProviders(<ServicePoliciesCard />);
    expect(screen.getByTestId("policies-error")).toHaveTextContent("auth gone");
  });

  it("renders rows from a flat policy map", () => {
    policiesState.data = {
      services: { sonarr: "two_factor", radarr: "one_factor" },
    };
    renderWithProviders(<ServicePoliciesCard />);
    expect(screen.getByTestId("policy-svc-sonarr")).toBeInTheDocument();
    expect(screen.getByTestId("policy-svc-radarr")).toBeInTheDocument();
  });

  it("renders rows from a nested {policy: ...} shape", () => {
    policiesState.data = {
      services: {
        jellyfin: { policy: "native" },
        prowlarr: { policy: "two_factor" },
      },
    };
    renderWithProviders(<ServicePoliciesCard />);
    expect(screen.getByTestId("policy-svc-jellyfin")).toBeInTheDocument();
    expect(screen.getByTestId("policy-svc-prowlarr")).toBeInTheDocument();
  });

  it("disables save when the draft matches the server snapshot", () => {
    policiesState.data = { services: { sonarr: "two_factor" } };
    renderWithProviders(<ServicePoliciesCard />);
    expect(screen.getByTestId("policy-save")).toBeDisabled();
  });

  it("renders a Save button as the bulk-update affordance", () => {
    policiesState.data = { services: { sonarr: "two_factor" } };
    renderWithProviders(<ServicePoliciesCard />);
    expect(screen.getByTestId("policy-save")).toBeInTheDocument();
  });

  it("does not dispatch an update on first render with a clean draft", () => {
    policiesState.data = { services: { sonarr: "two_factor" } };
    renderWithProviders(<ServicePoliciesCard />);
    expect(updateMutate).not.toHaveBeenCalled();
  });

  it("renders a select input per row driven by the policy enum", () => {
    policiesState.data = {
      services: { sonarr: "two_factor", radarr: "bypass" },
    };
    renderWithProviders(<ServicePoliciesCard />);
    expect(screen.getByTestId("policy-select-sonarr")).toBeInTheDocument();
    expect(screen.getByTestId("policy-select-radarr")).toBeInTheDocument();
  });
});
