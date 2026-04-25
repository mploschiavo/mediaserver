import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const sessionsState = vi.hoisted(() => ({
  data: undefined as { sessions: unknown[] } | undefined,
  isLoading: false,
  error: null as Error | null,
  refetch: vi.fn(),
}));

const revokeMutate = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useActiveSessions: () => sessionsState,
  useRevokeSession: () => ({
    mutate: revokeMutate,
    isPending: false,
  }),
}));

import { SessionsTable } from "./SessionsTable";

describe("SessionsTable", () => {
  beforeEach(() => {
    sessionsState.data = undefined;
    sessionsState.isLoading = false;
    sessionsState.error = null;
    sessionsState.refetch.mockReset();
    revokeMutate.mockReset();
  });

  it("renders skeleton while loading", () => {
    sessionsState.isLoading = true;
    renderWithProviders(<SessionsTable />);
    expect(screen.getByTestId("sessions-loading")).toBeInTheDocument();
  });

  it("renders the empty state when sessions=[]", () => {
    sessionsState.data = { sessions: [] };
    renderWithProviders(<SessionsTable />);
    // v1.3.2 honesty rewrite: empty-state title now reflects the
    // backend gap rather than asserting nobody is signed in.
    expect(
      screen.getByText(/No live sessions surfaced/i),
    ).toBeInTheDocument();
  });

  it("renders an error card with retry when the query fails", () => {
    sessionsState.error = new Error("auth gone");
    renderWithProviders(<SessionsTable />);
    expect(screen.getByTestId("sessions-error")).toHaveTextContent("auth gone");
    fireEvent.click(screen.getByTestId("sessions-retry"));
    expect(sessionsState.refetch).toHaveBeenCalled();
  });

  it("renders one row per session with username + provider badge", () => {
    sessionsState.data = {
      sessions: [
        {
          session_id: "s1",
          username: "matt",
          provider: "authelia",
          client_ip: "10.0.0.1",
          user_agent: "Mozilla/5.0 (X11; Linux x86_64)",
          connected_since: new Date(Date.now() - 3600_000).toISOString(),
          last_activity: new Date(Date.now() - 60_000).toISOString(),
          revokable: true,
        },
        {
          session_id: "s2",
          username: "alice",
          provider: "jellyfin",
          client_ip: "10.0.0.2",
          revokable: false,
        },
      ],
    };
    renderWithProviders(<SessionsTable />);
    // Both desktop + mobile branches mount, so each entry appears at least once.
    expect(screen.getAllByText("matt").length).toBeGreaterThan(0);
    expect(screen.getAllByText("alice").length).toBeGreaterThan(0);
    expect(screen.getAllByText("authelia").length).toBeGreaterThan(0);
    expect(screen.getAllByText("jellyfin").length).toBeGreaterThan(0);
    // Non-revokable session should surface a read-only label.
    expect(screen.getAllByText("read-only").length).toBeGreaterThan(0);
  });

  it("opens the confirm dialog when the Revoke button is clicked", async () => {
    sessionsState.data = {
      sessions: [
        {
          session_id: "s1",
          username: "matt",
          provider: "authelia",
          revokable: true,
        },
      ],
    };
    renderWithProviders(<SessionsTable />);
    fireEvent.click(screen.getByTestId("revoke-s1"));
    expect(await screen.findByTestId("revoke-dialog")).toBeInTheDocument();
    expect(screen.getByText(/Revoke session\?/)).toBeInTheDocument();
  });

  it("calls the revoke mutation when confirm is clicked", async () => {
    sessionsState.data = {
      sessions: [
        {
          session_id: "s1",
          username: "matt",
          provider: "authelia",
          revokable: true,
        },
      ],
    };
    renderWithProviders(<SessionsTable />);
    fireEvent.click(screen.getByTestId("revoke-s1"));
    const confirm = await screen.findByTestId("revoke-confirm");
    fireEvent.click(confirm);
    expect(revokeMutate).toHaveBeenCalledTimes(1);
    const call = revokeMutate.mock.calls[0]?.[0] as
      | { user_id: string; session_id: string; provider: string }
      | undefined;
    expect(call?.user_id).toBe("matt");
    expect(call?.session_id).toBe("s1");
    expect(call?.provider).toBe("authelia");
  });
});
