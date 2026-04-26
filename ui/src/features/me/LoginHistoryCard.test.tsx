import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const meState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const historyState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const thisWasntMeMutate = vi.hoisted(() => vi.fn());
const thisWasntMeState = vi.hoisted(() => ({ isPending: false }));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useMe: () => meState,
    useMeLoginHistory: () => historyState,
    useThisWasntMe: () => ({
      mutate: thisWasntMeMutate,
      isPending: thisWasntMeState.isPending,
    }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { LoginHistoryCard } from "./LoginHistoryCard";

function resetAll() {
  meState.data = { id: "u1" };
  meState.isLoading = false;
  meState.error = null;
  historyState.data = undefined;
  historyState.isLoading = false;
  historyState.error = null;
  thisWasntMeMutate.mockReset();
  thisWasntMeState.isPending = false;
  toastSuccess.mockReset();
  toastError.mockReset();
  // The "This wasn't me" handler now gates on window.confirm so the
  // operator doesn't accidentally sign themselves out of every
  // device.  Default to "yes" in tests so the existing assertions
  // about the mutation firing keep working; the cancel-path test
  // overrides this per-test.
  vi.spyOn(window, "confirm").mockReturnValue(true);
}

const entries = {
  entries: [
    {
      id: "e1",
      timestamp: new Date(Date.now() - 10 * 60_000).toISOString(),
      action: "login_success",
      result: "success",
      ip: "10.0.0.1",
      user_agent: "Chrome/macOS",
      detail: { location: "San Francisco, US" },
    },
    {
      id: "e2",
      timestamp: new Date(Date.now() - 60 * 60_000).toISOString(),
      action: "login_failed",
      result: "failed",
      ip: "10.0.0.2",
      user_agent: "curl/7",
    },
  ],
};

describe("LoginHistoryCard", () => {
  beforeEach(resetAll);

  it("renders the loading skeletons", () => {
    historyState.isLoading = true;
    renderWithProviders(<LoginHistoryCard />);
    expect(
      screen.getByTestId("login-history-card-loading"),
    ).toBeInTheDocument();
  });

  it("renders the error banner on failure", () => {
    historyState.error = new Error("server error");
    renderWithProviders(<LoginHistoryCard />);
    expect(screen.getByTestId("login-history-card-error")).toHaveTextContent(
      "server error",
    );
  });

  it("renders the empty state when there are no entries", () => {
    historyState.data = { entries: [] };
    renderWithProviders(<LoginHistoryCard />);
    expect(
      screen.getByTestId("login-history-card-empty"),
    ).toBeInTheDocument();
  });

  it("renders a row per entry with success/fail badges", () => {
    historyState.data = entries;
    renderWithProviders(<LoginHistoryCard />);
    const row1 = screen.getByTestId("login-history-row-e1");
    const row2 = screen.getByTestId("login-history-row-e2");
    expect(row1).toHaveTextContent(/Success/);
    expect(row1).toHaveTextContent("10.0.0.1");
    expect(row1).toHaveTextContent("San Francisco, US");
    expect(row2).toHaveTextContent(/Failed/);
  });

  it("fires useThisWasntMe when the affordance is clicked", async () => {
    historyState.data = entries;
    renderWithProviders(<LoginHistoryCard />);
    await userEvent.click(
      screen.getByTestId("login-history-wasnt-me-e2"),
    );
    expect(thisWasntMeMutate).toHaveBeenCalledOnce();
    expect(thisWasntMeMutate.mock.calls[0]?.[0]).toMatchObject({
      audit_id: "e2",
      flagged_ip: "10.0.0.2",
    });
  });

  it("toasts on successful report", async () => {
    historyState.data = entries;
    thisWasntMeMutate.mockImplementation(
      (_vars: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(<LoginHistoryCard />);
    await userEvent.click(
      screen.getByTestId("login-history-wasnt-me-e1"),
    );
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
  });

  it("does NOT post when the operator cancels the confirm dialog", async () => {
    // The "This wasn't me" action revokes every session for the
    // caller (sign-out everywhere) and writes an audit-trail
    // anomaly entry.  That's intentional — but the consequence is
    // dramatic enough that the operator should be able to back out.
    // A dismissed confirm() must not fire the mutation.
    historyState.data = entries;
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithProviders(<LoginHistoryCard />);
    await userEvent.click(
      screen.getByTestId("login-history-wasnt-me-e1"),
    );
    expect(thisWasntMeMutate).not.toHaveBeenCalled();
  });
});
