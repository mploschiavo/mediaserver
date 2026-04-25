import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const providersState = vi.hoisted(() => ({
  data: undefined as { providers?: readonly unknown[] } | undefined,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", () => ({
  useEpgProviders: () => providersState,
}));

import { EpgProvidersCard } from "./EpgProvidersCard";

beforeEach(() => {
  providersState.data = { providers: [] };
  providersState.isLoading = false;
  providersState.error = null;
});

describe("EpgProvidersCard", () => {
  it("renders the empty state when no providers are returned", () => {
    renderWithProviders(<EpgProvidersCard />);
    expect(screen.getByText(/No EPG providers/i)).toBeInTheDocument();
  });

  it("renders a loading skeleton while the query resolves", () => {
    providersState.isLoading = true;
    providersState.data = undefined;
    renderWithProviders(<EpgProvidersCard />);
    expect(screen.getByTestId("epg-providers-loading")).toBeInTheDocument();
  });

  it("renders an error message when the query fails", () => {
    providersState.error = new Error("providers gone");
    providersState.data = undefined;
    renderWithProviders(<EpgProvidersCard />);
    expect(screen.getByTestId("epg-providers-error")).toHaveTextContent(
      "providers gone",
    );
  });

  it("renders provider rows with name + base URL + auth badge", () => {
    providersState.data = {
      providers: [
        {
          id: "schedules-direct",
          name: "Schedules Direct",
          base_url: "https://json.schedulesdirect.org",
          requires_auth: true,
        },
        {
          id: "iptv-org",
          name: "iptv-org",
          base_url: "https://iptv-org.github.io",
          requires_auth: false,
        },
      ],
    };
    renderWithProviders(<EpgProvidersCard />);
    expect(screen.getAllByText("Schedules Direct").length).toBeGreaterThan(0);
    expect(screen.getAllByText("iptv-org").length).toBeGreaterThan(0);
    expect(
      screen.getByTestId("epg-auth-schedules-direct"),
    ).toBeInTheDocument();
  });
});
