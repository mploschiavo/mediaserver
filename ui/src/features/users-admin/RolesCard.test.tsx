import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const queryState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

const mutateUpdate = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useRoles: () => queryState,
  useUpdateRole: () => ({ mutate: mutateUpdate, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { RolesCard } from "./RolesCard";

beforeEach(() => {
  queryState.data = undefined;
  queryState.isLoading = false;
  queryState.error = null;
  mutateUpdate.mockReset();
});

describe("RolesCard", () => {
  it("renders skeletons while loading", () => {
    queryState.isLoading = true;
    renderWithProviders(<RolesCard />);
    expect(screen.getByTestId("roles-loading")).toBeInTheDocument();
  });

  it("renders an error banner", () => {
    queryState.error = new Error("offline");
    renderWithProviders(<RolesCard />);
    expect(screen.getByTestId("roles-error")).toHaveTextContent("offline");
  });

  it("renders the empty state", () => {
    queryState.data = { roles: [] };
    renderWithProviders(<RolesCard />);
    expect(screen.getByText(/No roles defined/i)).toBeInTheDocument();
  });

  it("renders one row per role with permission badges", () => {
    queryState.data = {
      roles: [
        {
          slug: "admin",
          name: "Admin",
          permissions: ["users:write", "audit:read"],
        },
      ],
    };
    renderWithProviders(<RolesCard />);
    expect(screen.getByTestId("role-row-admin")).toBeInTheDocument();
    expect(screen.getByText("users:write")).toBeInTheDocument();
  });

  it("opens the edit dialog and saves the toggled grants", async () => {
    queryState.data = {
      roles: [
        {
          slug: "admin",
          name: "Admin",
          permissions: ["users:read"],
        },
      ],
    };
    renderWithProviders(<RolesCard />);
    await userEvent.click(screen.getByTestId("role-edit-admin"));
    const dialog = await screen.findByTestId("role-edit-dialog-admin");
    expect(dialog).toBeInTheDocument();
    // Toggle one permission off, save.
    await userEvent.click(
      screen.getByTestId("role-perm-admin-users:read"),
    );
    await userEvent.click(screen.getByTestId("role-save-admin"));
    expect(mutateUpdate).toHaveBeenCalledOnce();
    const [body] = mutateUpdate.mock.calls[0]!;
    expect(body).toMatchObject({ role_slug: "admin" });
    expect(body.body.permissions).not.toContain("users:read");
  });
});
