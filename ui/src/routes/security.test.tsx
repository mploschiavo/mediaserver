import type { ComponentType } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

// All three security queries are stubbed to "loading" so the page
// renders without firing real fetches.
vi.mock("@/features/security-signals/hooks", () => ({
  useFailedLogins: () => ({
    data: undefined,
    isLoading: true,
    error: null,
    refetch: vi.fn(),
  }),
  useNewLocations: () => ({
    data: undefined,
    isLoading: true,
    error: null,
    refetch: vi.fn(),
  }),
  useConcurrentSpikes: () => ({
    data: undefined,
    isLoading: true,
    error: null,
    refetch: vi.fn(),
  }),
}));

import { Route as SecurityRoute } from "./security";

const SecurityRouteComponent = SecurityRoute.options.component as ComponentType;

describe("security route", () => {
  it("registers the /security path", () => {
    expect((SecurityRoute.options as unknown as { path: string }).path).toBe("/security");
  });

  it("mounts the security signals page with header + three cards", () => {
    renderWithProviders(<SecurityRouteComponent />);
    expect(screen.getByText("Security signals")).toBeInTheDocument();
    expect(screen.getByTestId("security-page")).toBeInTheDocument();
    expect(screen.getByTestId("failed-logins-card")).toBeInTheDocument();
    expect(screen.getByTestId("new-locations-card")).toBeInTheDocument();
    expect(screen.getByTestId("concurrent-spikes-card")).toBeInTheDocument();
  });
});
