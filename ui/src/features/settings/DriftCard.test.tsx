import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const driftState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useConfigDrift: () => driftState,
  };
});

import { DriftCard } from "./DriftCard";

function reset() {
  driftState.data = undefined;
  driftState.isLoading = false;
  driftState.error = null;
}

describe("DriftCard", () => {
  beforeEach(reset);

  it("renders the loading skeletons", () => {
    driftState.isLoading = true;
    renderWithProviders(<DriftCard />);
    expect(screen.getByTestId("drift-card-loading")).toBeInTheDocument();
  });

  it("renders the error banner", () => {
    driftState.error = new Error("api down");
    renderWithProviders(<DriftCard />);
    // DriftCard now delegates to the shared ApiErrorTile; the generic
    // (non-ApiError) variant renders under api-error-tile-generic.
    expect(screen.getByTestId("api-error-tile-generic")).toHaveTextContent(
      "api down",
    );
  });

  it("renders an empty state when there is no drift", () => {
    driftState.data = { drift: [] };
    renderWithProviders(<DriftCard />);
    expect(screen.getByTestId("drift-card-empty")).toBeInTheDocument();
  });

  it("renders a row per drift entry with severity badge", () => {
    driftState.data = {
      drift: [
        {
          key: "tls.cert_path",
          profile_value: "/etc/ssl/a.pem",
          live_value: "/etc/ssl/b.pem",
          severity: "warn",
        },
        {
          key: "log.level",
          profile_value: "info",
          live_value: "debug",
          severity: "error",
        },
      ],
    };
    renderWithProviders(<DriftCard />);
    expect(
      screen.getByTestId("drift-row-tls.cert_path"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("drift-row-log.level")).toHaveTextContent(
      "error",
    );
  });

  it("filters drift entries via the DataTable key filter", async () => {
    driftState.data = {
      drift: [
        {
          key: "tls.cert_path",
          profile_value: "/etc/ssl/a.pem",
          live_value: "/etc/ssl/b.pem",
          severity: "warn",
        },
        {
          key: "log.level",
          profile_value: "info",
          live_value: "debug",
          severity: "error",
        },
      ],
    };
    renderWithProviders(<DriftCard />);
    expect(screen.getByTestId("drift-row-tls.cert_path")).toBeInTheDocument();
    expect(screen.getByTestId("drift-row-log.level")).toBeInTheDocument();
    await userEvent.type(screen.getByTestId("drift-filter-key"), "log");
    expect(screen.queryByTestId("drift-row-tls.cert_path")).toBeNull();
    expect(screen.getByTestId("drift-row-log.level")).toBeInTheDocument();
  });

  it("links Reconcile to /ops", () => {
    driftState.data = { drift: [] };
    renderWithProviders(<DriftCard />);
    const link = screen.getByTestId(
      "drift-reconcile-link",
    ) as HTMLAnchorElement;
    expect(link).toHaveAttribute("href", "/ops");
  });

  it("accepts the alternate `entries` payload shape", () => {
    driftState.data = {
      entries: [
        {
          key: "k",
          profile_value: 1,
          live_value: 2,
          severity: "info",
        },
      ],
    };
    renderWithProviders(<DriftCard />);
    expect(screen.getByTestId("drift-row-k")).toBeInTheDocument();
  });
});
