import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const mountsState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", () => ({
  useMounts: () => mountsState,
}));

import { MountsCard } from "./MountsCard";

describe("MountsCard", () => {
  beforeEach(() => {
    mountsState.data = undefined;
    mountsState.isLoading = false;
    mountsState.error = null;
  });

  it("renders skeletons while loading", () => {
    mountsState.isLoading = true;
    renderWithProviders(<MountsCard />);
    expect(screen.getByTestId("mounts-loading")).toBeInTheDocument();
  });

  it("renders empty state when no mounts are reported", () => {
    mountsState.data = { mounts: [] };
    renderWithProviders(<MountsCard />);
    expect(screen.getByText("No mounts detected")).toBeInTheDocument();
  });

  it("renders the error banner when the query fails", () => {
    mountsState.error = new Error("nope");
    renderWithProviders(<MountsCard />);
    expect(screen.getByTestId("mounts-error")).toHaveTextContent("nope");
  });

  it("renders rows from the v1.3.0 path field", () => {
    mountsState.data = {
      mounts: [
        {
          path: "/srv-stack/media",
          fstype: "nfs4",
          size: 4_000_000_000_000,
          used: 2_000_000_000_000,
          available: 2_000_000_000_000,
        },
      ],
    };
    renderWithProviders(<MountsCard />);
    expect(screen.getAllByText("/srv-stack/media").length).toBeGreaterThan(0);
    // Usage bar renders with a progressbar role.
    expect(screen.getAllByRole("progressbar").length).toBeGreaterThan(0);
  });

  it("falls back to the OpenAPI mountpoint field", () => {
    mountsState.data = {
      mounts: [
        { device: "/dev/sda1", mountpoint: "/srv-config", fstype: "ext4" },
      ],
    };
    renderWithProviders(<MountsCard />);
    expect(screen.getAllByText("/srv-config").length).toBeGreaterThan(0);
    // No usage when size/used are missing.
    expect(screen.queryAllByRole("progressbar").length).toBe(0);
  });

  it("tolerates a non-array mounts payload via asArray()", () => {
    mountsState.data = { mounts: { foo: "bar" } as unknown };
    renderWithProviders(<MountsCard />);
    expect(screen.getByText("No mounts detected")).toBeInTheDocument();
  });
});
