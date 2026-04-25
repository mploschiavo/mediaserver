import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

// `/api/auth/config` is the source of truth for the configured OIDC
// provider — there is no separate "providers list" endpoint anymore.
// `oidc_provider` is the SINGULAR active key; `oidc_config` carries
// its parameters.
const configState = vi.hoisted(() => ({
  data: undefined as
    | {
        oidc_provider?: string;
        oidc_config?: Record<string, unknown>;
        [key: string]: unknown;
      }
    | undefined,
  isLoading: false,
  error: null as Error | null,
}));

const parseMutate = vi.hoisted(() => vi.fn());
const parsePending = vi.hoisted(() => ({ value: false }));
const updateMutate = vi.hoisted(() => vi.fn());
const updatePending = vi.hoisted(() => ({ value: false }));

vi.mock("./hooks", () => ({
  useAuthConfig: () => configState,
  useParseOidc: () => ({
    mutate: parseMutate,
    isPending: parsePending.value,
  }),
  useUpdateAuthConfig: () => ({
    mutate: updateMutate,
    isPending: updatePending.value,
  }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { OidcProvidersCard } from "./OidcProvidersCard";

beforeEach(() => {
  configState.data = {
    oidc_provider: "",
    oidc_config: {},
  };
  configState.isLoading = false;
  configState.error = null;
  parseMutate.mockReset();
  parsePending.value = false;
  updateMutate.mockReset();
  updatePending.value = false;
});

describe("OidcProvidersCard", () => {
  it("renders the empty state when no provider is configured", () => {
    renderWithProviders(<OidcProvidersCard />);
    expect(screen.getByText(/No OIDC provider/i)).toBeInTheDocument();
    expect(screen.getByTestId("oidc-add-trigger")).toBeInTheDocument();
  });

  it("renders a loading skeleton while the auth-config query resolves", () => {
    configState.isLoading = true;
    configState.data = undefined;
    renderWithProviders(<OidcProvidersCard />);
    expect(screen.getByTestId("oidc-loading")).toBeInTheDocument();
  });

  it("renders an error message when the auth-config query fails", () => {
    configState.error = new Error("auth gone");
    configState.data = undefined;
    renderWithProviders(<OidcProvidersCard />);
    expect(screen.getByTestId("oidc-error")).toHaveTextContent("auth gone");
  });

  // Regression test sourced from
  // ui/.ratchets/notes/API-RESPONSE-SHAPES-2026-04-25.txt — the live
  // /api/auth/config payload uses singular `oidc_provider` (not
  // `oidc_providers[]`) plus a parameter bag in `oidc_config`.
  it("surfaces the configured provider when oidc_provider is set", () => {
    configState.data = {
      mode: "authelia",
      internet_exposed: false,
      oidc_provider: "google",
      oidc_config: {
        client_id: "abc-123",
        issuer: "https://accounts.google.com",
        scopes: ["openid", "profile", "email"],
      },
      per_service: {},
    };
    renderWithProviders(<OidcProvidersCard />);
    expect(screen.getByTestId("oidc-current")).toBeInTheDocument();
    expect(screen.getByTestId("oidc-provider-key")).toHaveTextContent(
      "google",
    );
    expect(screen.getByTestId("oidc-issuer")).toHaveTextContent(
      "https://accounts.google.com",
    );
    expect(screen.getByText("openid")).toBeInTheDocument();
    expect(screen.getByText("profile")).toBeInTheDocument();
    expect(screen.getByText("email")).toBeInTheDocument();
  });

  it("opens the dialog with parse + save controls when configuring", async () => {
    renderWithProviders(<OidcProvidersCard />);
    await userEvent.click(screen.getByTestId("oidc-add-trigger"));
    expect(await screen.findByTestId("oidc-edit-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("oidc-discovery")).toBeInTheDocument();
    expect(screen.getByTestId("oidc-parse")).toBeInTheDocument();
    expect(screen.getByTestId("oidc-submit")).toBeInTheDocument();
  });

  it("dispatches parse with the discovery URL", async () => {
    renderWithProviders(<OidcProvidersCard />);
    await userEvent.click(screen.getByTestId("oidc-add-trigger"));
    await userEvent.type(
      await screen.findByTestId("oidc-discovery"),
      "https://issuer.example/.well-known/openid-configuration",
    );
    await userEvent.click(screen.getByTestId("oidc-parse"));
    expect(parseMutate).toHaveBeenCalledOnce();
    const [body] = parseMutate.mock.calls[0]!;
    expect(body).toMatchObject({
      discovery_url:
        "https://issuer.example/.well-known/openid-configuration",
    });
  });

  it("submits the merged oidc_provider/oidc_config when saving", async () => {
    configState.data = { oidc_provider: "google", oidc_config: {} };
    renderWithProviders(<OidcProvidersCard />);
    await userEvent.click(screen.getByTestId("oidc-edit-trigger"));
    await userEvent.type(
      await screen.findByTestId("oidc-client-id"),
      "abc-123",
    );
    await userEvent.click(screen.getByTestId("oidc-submit"));
    expect(updateMutate).toHaveBeenCalledOnce();
    const [body] = updateMutate.mock.calls[0]!;
    expect(body).toMatchObject({
      oidc_provider: "google",
    });
    const oidc = (body as { oidc_config: Record<string, unknown> })
      .oidc_config;
    expect(oidc.client_id).toBe("abc-123");
  });
});
