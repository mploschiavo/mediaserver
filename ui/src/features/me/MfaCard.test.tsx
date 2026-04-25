import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const mfaState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useMeMfaState: () => mfaState,
  };
});

import { MfaCard } from "./MfaCard";

describe("MfaCard", () => {
  beforeEach(() => {
    mfaState.data = undefined;
    mfaState.isLoading = false;
    mfaState.error = null;
  });

  it("renders loading skeletons", () => {
    mfaState.isLoading = true;
    renderWithProviders(<MfaCard />);
    expect(screen.getByTestId("mfa-card-loading")).toBeInTheDocument();
  });

  it("renders the error banner on failure", () => {
    mfaState.error = new Error("cannot read mfa");
    renderWithProviders(<MfaCard />);
    expect(screen.getByTestId("mfa-card-error")).toHaveTextContent(
      "cannot read mfa",
    );
  });

  it("shows Enabled badge when enabled", () => {
    mfaState.data = { enabled: true, factors: [{ type: "totp" }] };
    renderWithProviders(<MfaCard />);
    expect(screen.getByTestId("mfa-card-badge")).toHaveTextContent("Enabled");
    expect(screen.getByText(/TOTP/)).toBeInTheDocument();
  });

  it("shows Disabled badge when not enabled", () => {
    mfaState.data = { enabled: false };
    renderWithProviders(<MfaCard />);
    expect(screen.getByTestId("mfa-card-badge")).toHaveTextContent("Disabled");
    expect(screen.getByText(/Not enabled/)).toBeInTheDocument();
  });

  it("uses `enrolled` as a fallback for the enabled flag", () => {
    mfaState.data = { enrolled: true, enrolled_methods: ["webauthn"] };
    renderWithProviders(<MfaCard />);
    expect(screen.getByTestId("mfa-card-badge")).toHaveTextContent("Enabled");
    expect(screen.getByText(/WEBAUTHN/)).toBeInTheDocument();
  });

  it("links the Manage button to the Authelia settings route", () => {
    mfaState.data = { enabled: true };
    renderWithProviders(<MfaCard />);
    const manage = screen.getByTestId("mfa-manage");
    expect(manage).toHaveAttribute("href", "/app/authelia/settings");
  });
});
