import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const meState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const sessionsState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const revokeOneMutate = vi.hoisted(() => vi.fn());
const revokeOneState = vi.hoisted(() => ({ isPending: false }));
const revokeOthersMutate = vi.hoisted(() => vi.fn());
const revokeOthersState = vi.hoisted(() => ({ isPending: false }));
const thisWasntMeMutate = vi.hoisted(() => vi.fn());
const thisWasntMeState = vi.hoisted(() => ({ isPending: false }));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useMe: () => meState,
    useMeSessions: () => sessionsState,
    useRevokeMySession: () => ({
      mutate: revokeOneMutate,
      isPending: revokeOneState.isPending,
    }),
    useRevokeOthers: () => ({
      mutate: revokeOthersMutate,
      isPending: revokeOthersState.isPending,
    }),
    useThisWasntMe: () => ({
      mutate: thisWasntMeMutate,
      isPending: thisWasntMeState.isPending,
    }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { SessionsCard } from "./SessionsCard";

function resetAll() {
  meState.data = { id: "u1" };
  meState.isLoading = false;
  meState.error = null;
  sessionsState.data = undefined;
  sessionsState.isLoading = false;
  sessionsState.error = null;
  revokeOneMutate.mockReset();
  revokeOthersMutate.mockReset();
  thisWasntMeMutate.mockReset();
  revokeOneState.isPending = false;
  revokeOthersState.isPending = false;
  thisWasntMeState.isPending = false;
  toastSuccess.mockReset();
  toastError.mockReset();
}

const populatedSessions = {
  current_session_id: "s1",
  sessions: [
    {
      session_id: "s1",
      device: "Chrome on macOS",
      client_ip: "10.0.0.1",
      last_activity: new Date(Date.now() - 5 * 60_000).toISOString(),
      provider: "controller",
    },
    {
      session_id: "s2",
      device: "Firefox on Linux",
      client_ip: "10.0.0.2",
      last_activity: new Date(Date.now() - 2 * 3600_000).toISOString(),
      provider: "controller",
    },
  ],
};

describe("SessionsCard", () => {
  beforeEach(resetAll);

  it("renders loading skeletons", () => {
    sessionsState.isLoading = true;
    renderWithProviders(<SessionsCard />);
    expect(screen.getByTestId("sessions-card-loading")).toBeInTheDocument();
  });

  it("renders the error banner on failure", () => {
    sessionsState.error = new Error("boom");
    renderWithProviders(<SessionsCard />);
    expect(screen.getByTestId("sessions-card-error")).toHaveTextContent(
      "boom",
    );
  });

  it("renders the empty state when there are no sessions", () => {
    sessionsState.data = { sessions: [] };
    renderWithProviders(<SessionsCard />);
    expect(screen.getByTestId("sessions-card-empty")).toBeInTheDocument();
  });

  it("renders a row per session and flags the current one", () => {
    sessionsState.data = populatedSessions;
    renderWithProviders(<SessionsCard />);
    expect(screen.getByTestId("session-row-s1")).toHaveTextContent(
      "this session",
    );
    expect(screen.getByTestId("session-row-s2")).toBeInTheDocument();
  });

  it("disables the Sign out button on the current session", () => {
    sessionsState.data = populatedSessions;
    renderWithProviders(<SessionsCard />);
    expect(screen.getByTestId("session-signout-s1")).toBeDisabled();
    expect(screen.getByTestId("session-signout-s2")).not.toBeDisabled();
  });

  it("fires useRevokeMySession when Sign out is clicked", async () => {
    sessionsState.data = populatedSessions;
    renderWithProviders(<SessionsCard />);
    await userEvent.click(screen.getByTestId("session-signout-s2"));
    expect(revokeOneMutate).toHaveBeenCalledOnce();
    expect(revokeOneMutate.mock.calls[0]?.[0]).toEqual({
      userId: "u1",
      sessionId: "s2",
    });
  });

  it("toasts on successful sign-out", async () => {
    sessionsState.data = populatedSessions;
    revokeOneMutate.mockImplementation(
      (_vars: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(<SessionsCard />);
    await userEvent.click(screen.getByTestId("session-signout-s2"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
  });

  it("fires useRevokeOthers from the footer button", async () => {
    sessionsState.data = populatedSessions;
    renderWithProviders(<SessionsCard />);
    await userEvent.click(screen.getByTestId("signout-everywhere"));
    expect(revokeOthersMutate).toHaveBeenCalledOnce();
  });

  it("fires useThisWasntMe with the session id", async () => {
    sessionsState.data = populatedSessions;
    renderWithProviders(<SessionsCard />);
    await userEvent.click(screen.getByTestId("session-wasnt-me-s2"));
    expect(thisWasntMeMutate).toHaveBeenCalledOnce();
    expect(thisWasntMeMutate.mock.calls[0]?.[0]).toMatchObject({
      session_id: "s2",
    });
  });

  it("toasts the error when sign-out fails", async () => {
    sessionsState.data = populatedSessions;
    revokeOneMutate.mockImplementation(
      (_vars: unknown, opts: { onError: (e: Error) => void }) =>
        opts.onError(new Error("rate limit")),
    );
    renderWithProviders(<SessionsCard />);
    await userEvent.click(screen.getByTestId("session-signout-s2"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("rate limit"));
  });
});
