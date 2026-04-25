import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const queryState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

const mutateCreate = vi.hoisted(() => vi.fn());
const mutateRevoke = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useInvites: () => queryState,
  useCreateInvite: () => ({ mutate: mutateCreate, isPending: false }),
  useRevokeInvite: () => ({ mutate: mutateRevoke, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { InvitesCard } from "./InvitesCard";

beforeEach(() => {
  queryState.data = undefined;
  queryState.isLoading = false;
  queryState.error = null;
  mutateCreate.mockReset();
  mutateRevoke.mockReset();
});

describe("InvitesCard", () => {
  it("renders skeletons while loading", () => {
    queryState.isLoading = true;
    renderWithProviders(<InvitesCard />);
    expect(screen.getByTestId("invites-loading")).toBeInTheDocument();
  });

  it("renders an error banner", () => {
    queryState.error = new Error("oops");
    renderWithProviders(<InvitesCard />);
    expect(screen.getByTestId("invites-error")).toHaveTextContent("oops");
  });

  it("renders the empty state", () => {
    queryState.data = { invites: [] };
    renderWithProviders(<InvitesCard />);
    expect(screen.getByText(/No pending invites/i)).toBeInTheDocument();
  });

  it("renders one row per invite with revoke button", () => {
    queryState.data = {
      invites: [
        {
          id: "i1",
          email: "alice@x.test",
          role_slug: "viewer",
          token: "abc",
          status: "active",
        },
      ],
    };
    renderWithProviders(<InvitesCard />);
    expect(screen.getByTestId("invite-row-i1")).toBeInTheDocument();
    expect(screen.getByTestId("invite-revoke-i1")).toBeInTheDocument();
  });

  it("dispatches the revoke mutation on click", async () => {
    queryState.data = {
      invites: [{ id: "i1", email: "alice@x.test", token: "abc" }],
    };
    renderWithProviders(<InvitesCard />);
    await userEvent.click(screen.getByTestId("invite-revoke-i1"));
    expect(mutateRevoke).toHaveBeenCalledWith(
      expect.objectContaining({ invite_id: "i1" }),
      expect.any(Object),
    );
  });

  it("creates an invite when the form is submitted", async () => {
    queryState.data = { invites: [] };
    renderWithProviders(<InvitesCard />);
    await userEvent.click(screen.getByTestId("invite-create-trigger"));
    const dialog = await screen.findByTestId("invite-dialog");
    expect(dialog).toBeInTheDocument();
    await userEvent.type(screen.getByTestId("invite-email"), "x@y");
    await userEvent.click(screen.getByTestId("invite-submit"));
    expect(mutateCreate).toHaveBeenCalledOnce();
    const [body] = mutateCreate.mock.calls[0]!;
    expect(body).toMatchObject({ email: "x@y", role_slug: "viewer" });
  });
});
