import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const failedLoginsState = vi.hoisted(() => ({
  data: undefined as { clusters: unknown[] } | undefined,
  isLoading: false,
  error: null as Error | null,
  refetch: vi.fn(),
}));

vi.mock("./hooks", () => ({
  useFailedLogins: () => failedLoginsState,
}));

import { FailedLoginsCard } from "./FailedLoginsCard";

describe("FailedLoginsCard", () => {
  beforeEach(() => {
    failedLoginsState.data = undefined;
    failedLoginsState.isLoading = false;
    failedLoginsState.error = null;
    failedLoginsState.refetch.mockReset();
  });

  it("renders skeletons while loading", () => {
    failedLoginsState.isLoading = true;
    renderWithProviders(<FailedLoginsCard />);
    expect(screen.getByTestId("failed-logins-loading")).toBeInTheDocument();
  });

  it("renders the empty state when clusters=[]", () => {
    failedLoginsState.data = { clusters: [] };
    renderWithProviders(<FailedLoginsCard />);
    expect(
      screen.getByText("No failed-login clusters"),
    ).toBeInTheDocument();
  });

  it("renders an error banner when the query fails", () => {
    failedLoginsState.error = new Error("boom");
    renderWithProviders(<FailedLoginsCard />);
    expect(screen.getByTestId("failed-logins-error")).toHaveTextContent(
      "boom",
    );
  });

  it("renders one row per cluster with attempt count + identifier", () => {
    failedLoginsState.data = {
      clusters: [
        {
          ip_prefix: "192.168.1.0/24",
          attempt_count: 12,
          first_seen: new Date(Date.now() - 600_000).toISOString(),
          last_seen: new Date(Date.now() - 60_000).toISOString(),
          usernames: ["alice", "bob"],
        },
        {
          username: "carol",
          attempt_count: 3,
          first_seen: new Date(Date.now() - 120_000).toISOString(),
          last_seen: new Date(Date.now() - 30_000).toISOString(),
        },
      ],
    };
    renderWithProviders(<FailedLoginsCard />);
    expect(screen.getByTestId("failed-logins-table")).toBeInTheDocument();
    expect(screen.getByText("192.168.1.0/24")).toBeInTheDocument();
    expect(screen.getByText("carol")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("links the Investigate button to the audit-log when available", () => {
    failedLoginsState.data = {
      clusters: [
        {
          ip_prefix: "10.0.0.0/24",
          attempt_count: 6,
          first_seen: new Date().toISOString(),
          last_seen: new Date().toISOString(),
        },
      ],
    };
    renderWithProviders(<FailedLoginsCard />);
    const link = screen.getByTestId("failed-login-investigate-10.0.0.0/24");
    expect(link).toHaveAttribute(
      "href",
      "/audit-log?action=auth.login.failed&actor=10.0.0.0%2F24",
    );
  });

  it("filters clusters via the DataTable identifier filter", async () => {
    failedLoginsState.data = {
      clusters: [
        {
          ip_prefix: "192.168.1.0/24",
          attempt_count: 12,
          first_seen: new Date(Date.now() - 600_000).toISOString(),
          last_seen: new Date(Date.now() - 60_000).toISOString(),
        },
        {
          username: "carol",
          attempt_count: 3,
          first_seen: new Date(Date.now() - 120_000).toISOString(),
          last_seen: new Date(Date.now() - 30_000).toISOString(),
        },
      ],
    };
    renderWithProviders(<FailedLoginsCard />);
    expect(
      screen.getByTestId("failed-login-row-192.168.1.0/24"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("failed-login-row-carol")).toBeInTheDocument();
    await userEvent.type(
      screen.getByTestId("failed-login-filter-identifier"),
      "carol",
    );
    expect(screen.queryByTestId("failed-login-row-192.168.1.0/24")).toBeNull();
    expect(screen.getByTestId("failed-login-row-carol")).toBeInTheDocument();
  });

  it("opens the raw-details dialog when audit-log is unavailable", async () => {
    failedLoginsState.data = {
      clusters: [
        {
          ip_prefix: "10.0.0.0/24",
          attempt_count: 6,
          first_seen: new Date().toISOString(),
          last_seen: new Date().toISOString(),
        },
      ],
    };
    renderWithProviders(<FailedLoginsCard auditLogAvailable={false} />);
    const btn = screen.getByTestId("failed-login-investigate-10.0.0.0/24");
    fireEvent.click(btn);
    await waitFor(() => {
      expect(
        screen.getByTestId("failed-login-details-dialog"),
      ).toBeInTheDocument();
    });
    expect(screen.getByTestId("failed-login-details-pre")).toHaveTextContent(
      "10.0.0.0/24",
    );
  });
});
