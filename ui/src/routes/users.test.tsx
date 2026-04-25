import type { ComponentType } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const usersState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
  refetch: vi.fn(),
}));

const adminState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

const rolesState = vi.hoisted(() => ({
  data: { roles: [] },
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    useUsers: () => usersState,
  };
});

vi.mock("@/features/users-admin/hooks", () => {
  // Stand in for every hook the wired-up components consume so the
  // route can mount without making any network requests. Each hook
  // returns a minimal "idle, no data" shape — individual feature
  // tests cover the real query + mutation paths.
  const idleQuery = { data: undefined, isLoading: false, error: null };
  const idleMutation = {
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
    error: null,
  };
  return {
    usersAdminKeys: {
      list: ["users-admin", "list"],
      user: (id: string) => ["users-admin", "user", id],
      sessions: (id: string) => ["users-admin", "sessions", id],
      loginHistory: (id: string) => ["users-admin", "login-history", id],
      roles: ["users-admin", "roles"],
      invites: ["users-admin", "invites"],
      passwordPolicy: ["users-admin", "password-policy"],
      reconcile: ["users-admin", "reconcile"],
      providers: ["users-admin", "user-providers"],
    },
    useUsersAdmin: () => adminState,
    useAddUser: () => idleMutation,
    usePatchUser: () => idleMutation,
    useDeleteUser: () => idleMutation,
    useResetUserPassword: () => idleMutation,
    useSetUserRole: () => idleMutation,
    useSetUserState: () => idleMutation,
    useRevokeUserSessions: () => idleMutation,
    useRevokeUserSession: () => idleMutation,
    useUserSessions: () => idleQuery,
    useUserLoginHistory: () => idleQuery,
    useBulkImportUsers: () => idleMutation,
    useRoles: () => rolesState,
    useUpdateRole: () => idleMutation,
    useInvites: () => ({ ...idleQuery, data: { invites: [] } }),
    useCreateInvite: () => idleMutation,
    useRevokeInvite: () => idleMutation,
    usePasswordPolicy: () => ({ ...idleQuery, data: {} }),
    useUpdatePasswordPolicy: () => idleMutation,
    useUsersReconcile: () => ({ ...idleQuery, data: { diffs: [] } }),
    useUserProviders: () => ({ ...idleQuery, data: { providers: [] } }),
    useImportOrphanUser: () => idleMutation,
    useUnlinkGhostUser: () => idleMutation,
  };
});

import { Route as UsersRoute } from "./users";

const UsersPage = UsersRoute.options.component as ComponentType;

describe("users route", () => {
  beforeEach(() => {
    usersState.data = undefined;
    usersState.isLoading = false;
    usersState.error = null;
    adminState.data = undefined;
    adminState.isLoading = false;
    adminState.error = null;
    rolesState.data = { roles: [] };
  });

  it("shows the directory stats skeleton while loading", () => {
    usersState.isLoading = true;
    renderWithProviders(<UsersPage />);
    // The stats card shows a skeleton; the members table also has
    // its own loading branch so use the stats card as the anchor.
    expect(screen.getByTestId("users-stats")).toBeInTheDocument();
  });

  it("renders the directory stats", () => {
    usersState.data = {
      users: [{ id: "1", username: "matt", role: "admin", status: "active" }],
      admins: 1,
      pending_invites: 0,
    };
    renderWithProviders(<UsersPage />);
    const stats = screen.getByTestId("users-stats");
    expect(stats).toHaveTextContent("1");
    expect(stats).toHaveTextContent(/users/);
    expect(stats).toHaveTextContent(/admins/);
  });

  it("renders the members empty state when admin list is empty", () => {
    usersState.data = { users: [], admins: 0, pending_invites: 0 };
    adminState.data = { users: [] };
    renderWithProviders(<UsersPage />);
    expect(screen.getByText(/No users/i)).toBeInTheDocument();
  });

  it("renders user rows from the admin list", () => {
    usersState.data = { users: [], admins: 0, pending_invites: 0 };
    adminState.data = {
      users: [
        {
          id: "u1",
          username: "alice",
          role: "operator",
          status: "active",
          last_login_at: new Date(Date.now() - 30 * 60_000).toISOString(),
        },
      ],
    };
    renderWithProviders(<UsersPage />);
    expect(screen.getAllByText("alice").length).toBeGreaterThan(0);
    expect(
      screen.getAllByTestId("user-actions-u1").length,
    ).toBeGreaterThan(0);
  });

  it("renders an error banner when the directory query fails", () => {
    usersState.error = new Error("auth gone");
    renderWithProviders(<UsersPage />);
    expect(screen.getByTestId("users-error")).toHaveTextContent("auth gone");
  });

  it("mounts the EmergencyRevokeCard at the bottom of the route", () => {
    usersState.data = { users: [], admins: 0, pending_invites: 0 };
    renderWithProviders(<UsersPage />);
    expect(screen.getByTestId("emergency-revoke-card")).toBeInTheDocument();
  });

  it("renders the tablist for roles / invites / policy / providers", () => {
    usersState.data = { users: [], admins: 0, pending_invites: 0 };
    renderWithProviders(<UsersPage />);
    expect(screen.getByTestId("users-tablist")).toBeInTheDocument();
    expect(screen.getByTestId("users-tab-roles")).toBeInTheDocument();
    expect(screen.getByTestId("users-tab-invites")).toBeInTheDocument();
    expect(screen.getByTestId("users-tab-policy")).toBeInTheDocument();
    expect(screen.getByTestId("users-tab-providers")).toBeInTheDocument();
  });
});
