import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const idleQuery = { data: undefined, isLoading: false, error: null };
const idleMutation = {
  mutate: vi.fn(),
  mutateAsync: vi.fn(),
  isPending: false,
  error: null,
};

vi.mock("./hooks", () => ({
  authAdminKeys: {
    config: ["auth-admin", "config"],
    modes: ["auth-admin", "modes"],
    oidcProviders: ["auth-admin", "oidc-providers"],
    servicePolicies: ["auth-admin", "service-policies"],
  },
  useAuthConfig: () => ({ ...idleQuery, data: { mode: "basic" } }),
  useUpdateAuthConfig: () => idleMutation,
  useAuthModes: () => ({
    ...idleQuery,
    data: { modes: ["authelia", "basic", "none"] },
  }),
  useOidcProviders: () => ({ ...idleQuery, data: { providers: [] } }),
  useParseOidc: () => idleMutation,
  useServicePolicies: () => ({ ...idleQuery, data: { services: {} } }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { AuthAdminPage } from "./AuthAdminPage";

describe("AuthAdminPage", () => {
  // The page header (title + description) lives in the /auth route
  // wrapper now — see `src/routes/auth.tsx`. This test file asserts
  // only the in-column card composition, which is what this
  // component owns.
  it("composes the three auth-admin cards", () => {
    renderWithProviders(<AuthAdminPage />);
    expect(screen.getByTestId("auth-admin-page")).toBeInTheDocument();
    expect(screen.getByTestId("auth-mode-card")).toBeInTheDocument();
    expect(screen.getByTestId("oidc-providers-card")).toBeInTheDocument();
    expect(screen.getByTestId("service-policies-card")).toBeInTheDocument();
  });
});
