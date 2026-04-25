import type { ComponentType } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

// Stub each feature card so this test asserts composition only —
// the cards' own tests live next to the components.
vi.mock("@/features/me", () => ({
  ProfileCard: () => <div data-testid="mock-profile-card" />,
  SessionsCard: () => <div data-testid="mock-sessions-card" />,
  TokensCard: () => <div data-testid="mock-tokens-card" />,
  MfaCard: () => <div data-testid="mock-mfa-card" />,
  LoginHistoryCard: () => <div data-testid="mock-login-history-card" />,
}));

import { Route as MeRoute } from "./me";

const MePage = MeRoute.options.component as ComponentType;

describe("me route", () => {
  it("renders the page shell with a title", () => {
    renderWithProviders(<MePage />);
    expect(screen.getByTestId("me-page")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /My profile/ }),
    ).toBeInTheDocument();
  });

  it("renders every feature card", () => {
    renderWithProviders(<MePage />);
    expect(screen.getByTestId("mock-profile-card")).toBeInTheDocument();
    expect(screen.getByTestId("mock-sessions-card")).toBeInTheDocument();
    expect(screen.getByTestId("mock-tokens-card")).toBeInTheDocument();
    expect(screen.getByTestId("mock-mfa-card")).toBeInTheDocument();
    expect(screen.getByTestId("mock-login-history-card")).toBeInTheDocument();
  });

  it("keeps the Tanstack route binding on /me", () => {
    expect((MeRoute.options as unknown as { path: string }).path).toBe("/me");
  });
});
