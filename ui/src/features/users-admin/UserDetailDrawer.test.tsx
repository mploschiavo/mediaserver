import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const sessionsState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const historyState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

const mutatePatch = vi.hoisted(() => vi.fn());
const mutateRole = vi.hoisted(() => vi.fn());
const mutateReset = vi.hoisted(() => vi.fn());
const mutateRevokeAll = vi.hoisted(() => vi.fn());
const mutateRevokeOne = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useUserSessions: () => sessionsState,
  useUserLoginHistory: () => historyState,
  usePatchUser: () => ({ mutate: mutatePatch, isPending: false }),
  useSetUserRole: () => ({ mutate: mutateRole, isPending: false }),
  useResetUserPassword: () => ({ mutate: mutateReset, isPending: false }),
  useRevokeUserSessions: () => ({ mutate: mutateRevokeAll, isPending: false }),
  useRevokeUserSession: () => ({ mutate: mutateRevokeOne, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { UserDetailDrawer } from "./UserDetailDrawer";

const USER = {
  id: "u1",
  username: "alice",
  email: "a@x.test",
  display_name: "Alice",
  role_slug: "operator",
};

beforeEach(() => {
  sessionsState.data = undefined;
  sessionsState.isLoading = false;
  sessionsState.error = null;
  historyState.data = undefined;
  historyState.isLoading = false;
  historyState.error = null;
  mutatePatch.mockReset();
  mutateRole.mockReset();
  mutateReset.mockReset();
  mutateRevokeAll.mockReset();
  mutateRevokeOne.mockReset();
});

describe("UserDetailDrawer", () => {
  it("renders nothing when user is null", () => {
    renderWithProviders(<UserDetailDrawer user={null} onClose={() => {}} />);
    expect(screen.queryByTestId("user-detail-drawer")).not.toBeInTheDocument();
  });

  it("renders the profile form when a user is supplied", async () => {
    renderWithProviders(
      <UserDetailDrawer user={USER} onClose={() => {}} />,
    );
    expect(await screen.findByTestId("user-profile-form")).toBeInTheDocument();
    expect(screen.getByTestId("user-email-input")).toHaveValue("a@x.test");
  });

  it("renders an audit-log link with the actor query", async () => {
    renderWithProviders(
      <UserDetailDrawer user={USER} onClose={() => {}} />,
    );
    const link = await screen.findByTestId("user-audit-history-link");
    expect(link).toHaveAttribute("href", "/audit-log?actor=alice");
  });

  it("submits the profile form with the patched values", async () => {
    renderWithProviders(
      <UserDetailDrawer user={USER} onClose={() => {}} />,
    );
    const email = await screen.findByTestId("user-email-input");
    await userEvent.clear(email);
    await userEvent.type(email, "new@x.test");
    await userEvent.click(screen.getByTestId("user-profile-save"));
    expect(mutatePatch).toHaveBeenCalled();
    const [body] = mutatePatch.mock.calls[0]!;
    expect(body).toMatchObject({
      user_id: "u1",
      body: expect.objectContaining({ email: "new@x.test" }),
    });
  });

  it("renders the sessions empty state", async () => {
    sessionsState.data = { sessions: [] };
    renderWithProviders(
      <UserDetailDrawer
        user={USER}
        initialTab="sessions"
        onClose={() => {}}
      />,
    );
    expect(
      await screen.findByTestId("user-sessions-empty"),
    ).toBeInTheDocument();
  });

  it("dispatches revoke-all from the sessions panel", async () => {
    sessionsState.data = {
      sessions: [{ id: "s1", ip: "1.2.3.4" }],
    };
    renderWithProviders(
      <UserDetailDrawer
        user={USER}
        initialTab="sessions"
        onClose={() => {}}
      />,
    );
    const btn = await screen.findByTestId("user-sessions-revoke-all");
    await userEvent.click(btn);
    expect(mutateRevokeAll).toHaveBeenCalled();
  });

  it("dispatches single-session revoke from the row", async () => {
    sessionsState.data = {
      sessions: [{ id: "s1", ip: "1.2.3.4" }],
    };
    renderWithProviders(
      <UserDetailDrawer
        user={USER}
        initialTab="sessions"
        onClose={() => {}}
      />,
    );
    const btn = await screen.findByTestId("user-session-revoke-s1");
    await userEvent.click(btn);
    expect(mutateRevokeOne).toHaveBeenCalledWith(
      expect.objectContaining({ user_id: "u1", session_id: "s1" }),
      expect.any(Object),
    );
  });

  it("renders the login history list", async () => {
    historyState.data = {
      entries: [{ ts: new Date().toISOString(), ip: "1.2.3.4" }],
    };
    renderWithProviders(
      <UserDetailDrawer
        user={USER}
        initialTab="login-history"
        onClose={() => {}}
      />,
    );
    expect(
      await screen.findByTestId("user-login-history"),
    ).toBeInTheDocument();
  });
});
