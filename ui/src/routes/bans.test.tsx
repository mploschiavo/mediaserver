import type { ComponentType } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

vi.mock("@/features/bans/hooks", () => ({
  useUserBans: () => ({ data: [], isLoading: false, error: null }),
  useIpBans: () => ({ data: [], isLoading: false, error: null }),
  useAddUserBan: () => ({ mutate: vi.fn(), isPending: false }),
  useRemoveUserBan: () => ({ mutate: vi.fn(), isPending: false }),
  useAddIpBan: () => ({ mutate: vi.fn(), isPending: false }),
  useRemoveIpBan: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { Route as BansRoute } from "./bans";

const BansRouteComponent = BansRoute.options.component as ComponentType;

describe("bans route", () => {
  it("renders the page header and both ban cards", () => {
    renderWithProviders(<BansRouteComponent />);
    expect(screen.getByText("Bans")).toBeInTheDocument();
    expect(screen.getByTestId("bans-page")).toBeInTheDocument();
    expect(screen.getByTestId("user-bans-card")).toBeInTheDocument();
    expect(screen.getByTestId("ip-bans-card")).toBeInTheDocument();
  });

  it("registers the route at /bans", () => {
    expect((BansRoute.options as unknown as { path: string }).path).toBe("/bans");
  });
});
