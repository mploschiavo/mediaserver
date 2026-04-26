import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const userBansState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

const addMutate = vi.hoisted(() => vi.fn());
const removeMutate = vi.hoisted(() => vi.fn());
const addState = vi.hoisted(() => ({ isPending: false }));
const removeState = vi.hoisted(() => ({ isPending: false }));

const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useUserBans: () => userBansState,
  useAddUserBan: () => ({ mutate: addMutate, ...addState }),
  useRemoveUserBan: () => ({ mutate: removeMutate, ...removeState }),
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { UserBansCard } from "./UserBansCard";

describe("UserBansCard", () => {
  beforeEach(() => {
    userBansState.data = undefined;
    userBansState.isLoading = false;
    userBansState.error = null;
    addState.isPending = false;
    removeState.isPending = false;
    addMutate.mockReset();
    removeMutate.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the loading skeleton while fetching", () => {
    userBansState.isLoading = true;
    renderWithProviders(<UserBansCard />);
    expect(screen.getByTestId("user-bans-loading")).toBeInTheDocument();
  });

  it("renders the empty state when no bans exist", () => {
    userBansState.data = [];
    renderWithProviders(<UserBansCard />);
    expect(screen.getByText(/No user bans/i)).toBeInTheDocument();
  });

  it("renders one row per ban", () => {
    userBansState.data = [
      {
        username: "alice",
        reason: "policy violation",
        banned_at: "2026-04-01T12:00:00Z",
      },
      {
        username: "bob",
        reason: "credential stuffing",
      },
    ];
    renderWithProviders(<UserBansCard />);
    expect(screen.getByTestId("user-ban-row-alice")).toBeInTheDocument();
    expect(screen.getByTestId("user-ban-row-bob")).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("policy violation")).toBeInTheDocument();
  });

  it("filters bans via the DataTable username filter", async () => {
    userBansState.data = [
      { username: "alice", reason: "policy" },
      { username: "bob", reason: "credential stuffing" },
    ];
    renderWithProviders(<UserBansCard />);
    expect(screen.getByTestId("user-ban-row-alice")).toBeInTheDocument();
    expect(screen.getByTestId("user-ban-row-bob")).toBeInTheDocument();
    await userEvent.type(
      screen.getByTestId("user-ban-filter-username"),
      "bob",
    );
    expect(screen.queryByTestId("user-ban-row-alice")).toBeNull();
    expect(screen.getByTestId("user-ban-row-bob")).toBeInTheDocument();
  });

  it("renders 'indefinite' when expires_at is missing", () => {
    userBansState.data = [{ username: "carol", reason: "hold" }];
    renderWithProviders(<UserBansCard />);
    expect(screen.getByText("indefinite")).toBeInTheDocument();
  });

  it("opens the dialog, types into the form, submits, and calls the mutation", async () => {
    userBansState.data = [];
    renderWithProviders(<UserBansCard />);
    await userEvent.click(screen.getByTestId("user-ban-add-trigger"));
    await waitFor(() =>
      expect(screen.getByTestId("user-ban-dialog")).toBeInTheDocument(),
    );
    await userEvent.type(
      screen.getByTestId("user-ban-username-input"),
      "evilfoo",
    );
    await userEvent.type(
      screen.getByTestId("user-ban-reason-input"),
      "spamming",
    );
    await userEvent.click(screen.getByTestId("user-ban-submit"));
    expect(addMutate).toHaveBeenCalledTimes(1);
    const body = addMutate.mock.calls[0]?.[0] as {
      username: string;
      reason: string;
    };
    expect(body).toMatchObject({ username: "evilfoo", reason: "spamming" });
  });

  it("disables the submit button until a username is typed", async () => {
    userBansState.data = [];
    renderWithProviders(<UserBansCard />);
    await userEvent.click(screen.getByTestId("user-ban-add-trigger"));
    const submit = await screen.findByTestId("user-ban-submit");
    expect(submit).toBeDisabled();
    await userEvent.type(
      screen.getByTestId("user-ban-username-input"),
      "carol",
    );
    expect(submit).not.toBeDisabled();
  });

  it("calls the lift-ban mutation after the user confirms", async () => {
    userBansState.data = [{ username: "alice", reason: "x" }];
    const confirmSpy = vi
      .spyOn(window, "confirm")
      .mockReturnValue(true);
    renderWithProviders(<UserBansCard />);
    await userEvent.click(screen.getByTestId("user-ban-lift-alice"));
    expect(confirmSpy).toHaveBeenCalled();
    expect(removeMutate).toHaveBeenCalledTimes(1);
    expect(removeMutate.mock.calls[0]?.[0]).toMatchObject({
      username: "alice",
    });
  });

  it("does not call the mutation when the confirm dialog is cancelled", async () => {
    userBansState.data = [{ username: "bob", reason: "x" }];
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithProviders(<UserBansCard />);
    await userEvent.click(screen.getByTestId("user-ban-lift-bob"));
    expect(removeMutate).not.toHaveBeenCalled();
  });

  it("renders the error banner when the query fails", () => {
    userBansState.error = new Error("forbidden");
    renderWithProviders(<UserBansCard />);
    expect(screen.getByTestId("user-bans-error")).toHaveTextContent(
      "forbidden",
    );
  });
});
