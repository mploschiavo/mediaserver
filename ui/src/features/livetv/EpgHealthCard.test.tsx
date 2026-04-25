import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const healthState = vi.hoisted(() => ({
  data: undefined as
    | {
        ok?: boolean;
        status?: string;
        last_run?: string;
        errors?: number | readonly string[];
        missing_channels?: readonly string[];
      }
    | undefined,
  isLoading: false,
  error: null as Error | null,
}));

const writeText = vi.hoisted(() => vi.fn(() => Promise.resolve()));

vi.mock("./hooks", () => ({
  useEpgHealth: () => healthState,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

beforeEach(() => {
  healthState.data = undefined;
  healthState.isLoading = false;
  healthState.error = null;
  writeText.mockReset();
  writeText.mockImplementation(() => Promise.resolve());
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    writable: true,
    configurable: true,
  });
});

import { EpgHealthCard } from "./EpgHealthCard";

describe("EpgHealthCard", () => {
  it("renders a loading skeleton while the query resolves", () => {
    healthState.isLoading = true;
    renderWithProviders(<EpgHealthCard />);
    expect(screen.getByTestId("epg-health-loading")).toBeInTheDocument();
  });

  it("renders an error message when the query fails", () => {
    healthState.error = new Error("epg gone");
    renderWithProviders(<EpgHealthCard />);
    expect(screen.getByTestId("epg-health-error")).toHaveTextContent(
      "epg gone",
    );
  });

  it("renders the empty state when there's no data yet", () => {
    healthState.data = undefined;
    renderWithProviders(<EpgHealthCard />);
    expect(screen.getByTestId("epg-health-empty")).toBeInTheDocument();
  });

  it("renders the healthy badge with the relative last-run", () => {
    healthState.data = {
      ok: true,
      last_run: new Date(Date.now() - 60_000).toISOString(),
      errors: 0,
      missing_channels: [],
    };
    renderWithProviders(<EpgHealthCard />);
    expect(screen.getByTestId("epg-health-status")).toHaveTextContent(
      /healthy/i,
    );
    expect(screen.getByText(/last run/i)).toBeInTheDocument();
  });

  it("renders the failing badge with an error count", () => {
    healthState.data = {
      ok: false,
      last_run: new Date(Date.now() - 30_000).toISOString(),
      errors: 3,
      missing_channels: [],
    };
    renderWithProviders(<EpgHealthCard />);
    expect(screen.getByTestId("epg-health-status")).toHaveTextContent(
      /failing/i,
    );
    expect(screen.getByTestId("epg-health-errors")).toHaveTextContent("3");
  });

  it("treats `status: 'ok'` as healthy when `ok` is missing", () => {
    healthState.data = {
      status: "ok",
      last_run: new Date(Date.now() - 30_000).toISOString(),
    };
    renderWithProviders(<EpgHealthCard />);
    expect(screen.getByTestId("epg-health-status")).toHaveTextContent(
      /healthy/i,
    );
  });

  it("renders a list of missing channels when present", () => {
    healthState.data = {
      ok: false,
      last_run: new Date().toISOString(),
      missing_channels: ["BBC One", "ESPN"],
    };
    renderWithProviders(<EpgHealthCard />);
    const list = screen.getByTestId("epg-missing-channels");
    expect(list).toHaveTextContent("BBC One");
    expect(list).toHaveTextContent("ESPN");
  });

  it("copies a missing channel id when its copy button is clicked", async () => {
    healthState.data = {
      ok: false,
      last_run: new Date().toISOString(),
      missing_channels: ["BBC One"],
    };
    renderWithProviders(<EpgHealthCard />);
    await userEvent.click(screen.getByTestId("epg-copy-BBC One"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("BBC One"));
  });
});
