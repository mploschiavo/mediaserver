import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const meState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useMe: () => meState,
  };
});

import { ProfileCard } from "./ProfileCard";

describe("ProfileCard", () => {
  beforeEach(() => {
    meState.data = undefined;
    meState.isLoading = false;
    meState.error = null;
  });

  it("renders the loading skeletons", () => {
    meState.isLoading = true;
    renderWithProviders(<ProfileCard />);
    expect(screen.getByTestId("profile-card-loading")).toBeInTheDocument();
  });

  it("renders the error banner with the message", () => {
    meState.error = new Error("auth expired");
    renderWithProviders(<ProfileCard />);
    const err = screen.getByTestId("profile-card-error");
    expect(err).toHaveTextContent("Failed to load your profile");
    expect(err).toHaveTextContent("auth expired");
  });

  it("renders display name, email, role, and last login", () => {
    meState.data = {
      id: "u1",
      username: "matt",
      display_name: "Matt Plo",
      email: "matt@example.test",
      role: "operator",
      last_login_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    };
    renderWithProviders(<ProfileCard />);
    expect(screen.getByText("Matt Plo")).toBeInTheDocument();
    expect(screen.getByText("matt@example.test")).toBeInTheDocument();
    expect(screen.getByTestId("profile-card-role")).toHaveTextContent(
      "Operator",
    );
    expect(screen.getByTestId("profile-card-last-login")).toHaveTextContent(
      /ago|just now/,
    );
  });

  it("falls back to the username when display_name is missing", () => {
    meState.data = { username: "matt", email: "" };
    renderWithProviders(<ProfileCard />);
    expect(screen.getByText("matt")).toBeInTheDocument();
  });

  it("omits the role badge when no role is supplied", () => {
    meState.data = { display_name: "Matt", email: "matt@example.test" };
    renderWithProviders(<ProfileCard />);
    expect(screen.queryByTestId("profile-card-role")).toBeNull();
  });
});
