import type { ComponentType } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

// Stand in for every auth-admin hook the wired-up cards consume so
// the route can mount without making any network requests.
vi.mock("@/features/auth-admin/hooks", () => {
  const idleQuery = { data: undefined, isLoading: false, error: null };
  const idleMutation = {
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
    error: null,
  };
  return {
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
  };
});

import { AuthAdminRoute } from "./auth";

const AuthPage = AuthAdminRoute.options.component as ComponentType;

describe("auth route", () => {
  it("registers the route at /auth", () => {
    expect(
      (AuthAdminRoute.options as unknown as { path: string }).path,
    ).toBe("/auth");
  });

  it("mounts the AuthAdminPage with the three cards", () => {
    renderWithProviders(<AuthPage />);
    expect(screen.getByTestId("auth-admin-page")).toBeInTheDocument();
    expect(screen.getByTestId("auth-mode-card")).toBeInTheDocument();
    expect(screen.getByTestId("oidc-providers-card")).toBeInTheDocument();
    expect(screen.getByTestId("service-policies-card")).toBeInTheDocument();
  });
});
