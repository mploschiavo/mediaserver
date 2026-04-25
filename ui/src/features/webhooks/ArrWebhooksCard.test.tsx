import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const arrState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", () => ({
  useArrWebhooks: () => arrState,
}));

import { ArrWebhooksCard } from "./ArrWebhooksCard";

describe("ArrWebhooksCard", () => {
  beforeEach(() => {
    arrState.data = undefined;
    arrState.isLoading = false;
    arrState.error = null;
  });
  afterEach(() => {
    arrState.data = undefined;
  });

  it("renders skeletons while loading", () => {
    arrState.isLoading = true;
    renderWithProviders(<ArrWebhooksCard />);
    expect(screen.getByTestId("arr-webhooks-loading")).toBeInTheDocument();
  });

  it("renders an error banner when the query fails", () => {
    arrState.error = new Error("offline");
    renderWithProviders(<ArrWebhooksCard />);
    expect(screen.getByTestId("arr-webhooks-error")).toHaveTextContent(
      "offline",
    );
  });

  it("renders an empty state when no services are returned", () => {
    arrState.data = { services: [] };
    renderWithProviders(<ArrWebhooksCard />);
    expect(
      screen.getByText(/No \*arr services discovered/i),
    ).toBeInTheDocument();
  });

  it("renders a row per discovered service with status badge + URL", () => {
    arrState.data = {
      services: [
        {
          service: "sonarr",
          configured: true,
          url: "http://controller:9100/webhooks/arr",
          last_delivery: new Date().toISOString(),
        },
        { service: "radarr", configured: false },
      ],
    };
    renderWithProviders(<ArrWebhooksCard />);
    expect(screen.getAllByText(/sonarr/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/radarr/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/configured/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/missing/i).length).toBeGreaterThan(0);
  });

  it("normalises a service-keyed object response", () => {
    arrState.data = {
      sonarr: { configured: true, url: "http://x/y" },
      radarr: { configured: false },
    };
    renderWithProviders(<ArrWebhooksCard />);
    expect(screen.getAllByText(/sonarr/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/radarr/i).length).toBeGreaterThan(0);
  });
});
