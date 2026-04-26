import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const providersState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const reconcileState = vi.hoisted(() => ({
  data: { diffs: [] } as unknown,
  isLoading: false,
  error: null as Error | null,
}));

const mutateImport = vi.hoisted(() => vi.fn());
const mutateUnlink = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useUserProviders: () => providersState,
  useUsersReconcile: () => reconcileState,
  useImportOrphanUser: () => ({ mutate: mutateImport, isPending: false }),
  useUnlinkGhostUser: () => ({ mutate: mutateUnlink, isPending: false }),
  usersAdminKeys: {
    reconcile: ["users-admin", "reconcile"],
    providers: ["users-admin", "user-providers"],
  },
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { ProviderReconcileCard } from "./ProviderReconcileCard";

beforeEach(() => {
  providersState.data = undefined;
  providersState.isLoading = false;
  providersState.error = null;
  reconcileState.data = { diffs: [] };
  reconcileState.isLoading = false;
  reconcileState.error = null;
  mutateImport.mockReset();
  mutateUnlink.mockReset();
});

describe("ProviderReconcileCard", () => {
  it("renders skeletons while loading", () => {
    providersState.isLoading = true;
    renderWithProviders(<ProviderReconcileCard />);
    expect(
      screen.getByTestId("provider-reconcile-loading"),
    ).toBeInTheDocument();
  });

  it("renders an error banner", () => {
    providersState.error = new Error("offline");
    renderWithProviders(<ProviderReconcileCard />);
    expect(
      screen.getByTestId("provider-reconcile-error"),
    ).toHaveTextContent("offline");
  });

  it("renders the empty state when no providers are bound", () => {
    providersState.data = { providers: [] };
    renderWithProviders(<ProviderReconcileCard />);
    expect(screen.getByText(/No provider bindings/i)).toBeInTheDocument();
  });

  it("renders one row per user with a per-provider linked badge", () => {
    providersState.data = {
      providers: [
        {
          user_id: "u1",
          username: "alice",
          providers: { authelia: { external_id: "ext-1" } },
        },
      ],
    };
    renderWithProviders(<ProviderReconcileCard />);
    expect(
      screen.getByTestId("provider-reconcile-table"),
    ).toBeInTheDocument();
    expect(screen.getByText("ext-1")).toBeInTheDocument();
  });

  it("dispatches unlink on click", async () => {
    providersState.data = {
      providers: [
        {
          user_id: "u1",
          username: "alice",
          providers: { authelia: { external_id: "ext-1" } },
        },
      ],
    };
    renderWithProviders(<ProviderReconcileCard />);
    await userEvent.click(
      screen.getByTestId("provider-unlink-u1-authelia"),
    );
    expect(mutateUnlink).toHaveBeenCalledWith(
      expect.objectContaining({
        user_id: "u1",
        provider_name: "authelia",
      }),
      expect.any(Object),
    );
  });

  it("filters provider rows via the DataTable user filter", async () => {
    providersState.data = {
      providers: [
        {
          user_id: "u1",
          username: "alice",
          providers: { authelia: { external_id: "ext-1" } },
        },
        {
          user_id: "u2",
          username: "bob",
          providers: { authelia: { external_id: "ext-2" } },
        },
      ],
    };
    renderWithProviders(<ProviderReconcileCard />);
    expect(screen.getByTestId("provider-row-u1")).toBeInTheDocument();
    expect(screen.getByTestId("provider-row-u2")).toBeInTheDocument();
    await userEvent.type(screen.getByTestId("provider-filter-user"), "bob");
    expect(screen.queryByTestId("provider-row-u1")).toBeNull();
    expect(screen.getByTestId("provider-row-u2")).toBeInTheDocument();
  });

  it("renders pending diffs and offers a Link button for orphans", async () => {
    providersState.data = { providers: [{ user_id: "u1", username: "alice" }] };
    reconcileState.data = {
      diffs: [
        {
          provider_name: "authelia",
          external_id: "ext-9",
          username: "ghost-alice",
          kind: "orphan",
        },
      ],
    };
    renderWithProviders(<ProviderReconcileCard />);
    expect(screen.getByTestId("reconcile-diffs")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("reconcile-link-0"));
    expect(mutateImport).toHaveBeenCalledOnce();
    const [body] = mutateImport.mock.calls[0]!;
    expect(body).toMatchObject({
      provider_name: "authelia",
      external_id: "ext-9",
    });
  });
});
