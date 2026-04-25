import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const queryState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

const mutateSetRole = vi.hoisted(() => vi.fn());
const mutateSetState = vi.hoisted(() => vi.fn());
const mutateReset = vi.hoisted(() => vi.fn());
const mutateRevoke = vi.hoisted(() => vi.fn());
const mutateDelete = vi.hoisted(() => vi.fn());

vi.mock("./hooks", async () => {
  const idleQuery = { data: undefined, isLoading: false, error: null };
  return {
    useUsersAdmin: () => queryState,
    useSetUserRole: () => ({ mutate: mutateSetRole, isPending: false }),
    useSetUserState: () => ({ mutate: mutateSetState, isPending: false }),
    useResetUserPassword: () => ({ mutate: mutateReset, isPending: false }),
    useRevokeUserSessions: () => ({ mutate: mutateRevoke, isPending: false }),
    useDeleteUser: () => ({ mutate: mutateDelete, isPending: false }),
    usePatchUser: () => ({ mutate: vi.fn(), isPending: false }),
    useRevokeUserSession: () => ({ mutate: vi.fn(), isPending: false }),
    useUserSessions: () => idleQuery,
    useUserLoginHistory: () => idleQuery,
  };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { UsersTable } from "./UsersTable";

beforeEach(() => {
  queryState.data = undefined;
  queryState.isLoading = false;
  queryState.error = null;
  mutateSetRole.mockReset();
  mutateSetState.mockReset();
  mutateReset.mockReset();
  mutateRevoke.mockReset();
  mutateDelete.mockReset();
});

describe("UsersTable", () => {
  it("renders skeletons while loading", () => {
    queryState.isLoading = true;
    renderWithProviders(<UsersTable />);
    expect(screen.getByTestId("users-table-loading")).toBeInTheDocument();
  });

  it("renders the empty state when the list is empty", () => {
    queryState.data = { users: [] };
    renderWithProviders(<UsersTable />);
    expect(screen.getByText(/No users/i)).toBeInTheDocument();
  });

  it("renders an error banner when the query fails", () => {
    queryState.error = new Error("offline");
    renderWithProviders(<UsersTable />);
    expect(screen.getByTestId("users-table-error")).toHaveTextContent(
      "offline",
    );
  });

  it("renders one row per user with kebab actions", () => {
    queryState.data = {
      users: [
        {
          id: "u1",
          username: "alice",
          email: "a@x",
          role_slug: "admin",
          status: "active",
        },
      ],
    };
    renderWithProviders(<UsersTable />);
    expect(screen.getAllByText("alice").length).toBeGreaterThan(0);
    expect(
      screen.getAllByTestId("user-actions-u1").length,
    ).toBeGreaterThan(0);
  });

  it("dispatches a reset-password mutation from the kebab", async () => {
    queryState.data = {
      users: [
        { id: "u1", username: "alice", role_slug: "admin", status: "active" },
      ],
    };
    renderWithProviders(<UsersTable />);
    const trigger = screen.getAllByTestId("user-actions-u1")[0]!;
    await userEvent.click(trigger);
    const reset = await screen.findByTestId("user-action-reset-u1");
    await userEvent.click(reset);
    expect(mutateReset).toHaveBeenCalledWith(
      expect.objectContaining({ user_id: "u1" }),
      expect.any(Object),
    );
  });

  it("dispatches revoke-sessions from the kebab", async () => {
    queryState.data = {
      users: [
        { id: "u1", username: "alice", role_slug: "admin", status: "active" },
      ],
    };
    renderWithProviders(<UsersTable />);
    await userEvent.click(screen.getAllByTestId("user-actions-u1")[0]!);
    await userEvent.click(await screen.findByTestId("user-action-revoke-u1"));
    expect(mutateRevoke).toHaveBeenCalledWith(
      expect.objectContaining({ user_id: "u1" }),
      expect.any(Object),
    );
  });

  it("dispatches delete after window.confirm true", async () => {
    queryState.data = {
      users: [
        { id: "u1", username: "alice", role_slug: "admin", status: "active" },
      ],
    };
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithProviders(<UsersTable />);
    await userEvent.click(screen.getAllByTestId("user-actions-u1")[0]!);
    await userEvent.click(await screen.findByTestId("user-action-delete-u1"));
    expect(mutateDelete).toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});
