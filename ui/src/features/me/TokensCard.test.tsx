import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const tokensState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const generateMutate = vi.hoisted(() => vi.fn());
const generateState = vi.hoisted(() => ({ isPending: false }));
const revokeMutate = vi.hoisted(() => vi.fn());
const revokeState = vi.hoisted(() => ({ isPending: false }));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());
const clipboardWrite = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useMeTokens: () => tokensState,
    useGenerateToken: () => ({
      mutate: generateMutate,
      isPending: generateState.isPending,
    }),
    useRevokeToken: () => ({
      mutate: revokeMutate,
      isPending: revokeState.isPending,
    }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { TokensCard } from "./TokensCard";

function resetAll() {
  tokensState.data = undefined;
  tokensState.isLoading = false;
  tokensState.error = null;
  generateMutate.mockReset();
  revokeMutate.mockReset();
  generateState.isPending = false;
  revokeState.isPending = false;
  toastSuccess.mockReset();
  toastError.mockReset();
  clipboardWrite.mockReset();
  clipboardWrite.mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: clipboardWrite },
    configurable: true,
    writable: true,
  });
}

const populatedTokens = {
  tokens: [
    {
      id: "t1",
      name: "CI deploy",
      scopes: ["read", "write"],
      created_at: new Date(Date.now() - 86_400_000).toISOString(),
      last_used_at: new Date(Date.now() - 3600_000).toISOString(),
      expires_at: "2099-01-01",
    },
  ],
};

describe("TokensCard", () => {
  beforeEach(resetAll);

  it("renders loading skeletons", () => {
    tokensState.isLoading = true;
    renderWithProviders(<TokensCard />);
    expect(screen.getByTestId("tokens-card-loading")).toBeInTheDocument();
  });

  it("renders the error banner on failure", () => {
    tokensState.error = new Error("server err");
    renderWithProviders(<TokensCard />);
    expect(screen.getByTestId("tokens-card-error")).toHaveTextContent(
      "server err",
    );
  });

  it("renders the empty state when no tokens exist", () => {
    tokensState.data = { tokens: [] };
    renderWithProviders(<TokensCard />);
    expect(screen.getByTestId("tokens-card-empty")).toBeInTheDocument();
  });

  it("renders a row per token", () => {
    tokensState.data = populatedTokens;
    renderWithProviders(<TokensCard />);
    expect(screen.getByTestId("token-row-t1")).toHaveTextContent("CI deploy");
    expect(screen.getByTestId("token-row-t1")).toHaveTextContent("read");
  });

  it("revokes a token when Revoke is clicked", async () => {
    tokensState.data = populatedTokens;
    renderWithProviders(<TokensCard />);
    await userEvent.click(screen.getByTestId("token-revoke-t1"));
    expect(revokeMutate).toHaveBeenCalledWith("t1", expect.anything());
  });

  it("opens the generate dialog", async () => {
    tokensState.data = { tokens: [] };
    renderWithProviders(<TokensCard />);
    await userEvent.click(screen.getByTestId("generate-token"));
    expect(
      await screen.findByTestId("generate-token-dialog"),
    ).toBeInTheDocument();
  });

  it("disables the submit button until a name is provided", async () => {
    tokensState.data = { tokens: [] };
    renderWithProviders(<TokensCard />);
    await userEvent.click(screen.getByTestId("generate-token"));
    const submit = await screen.findByTestId("generate-token-submit");
    expect(submit).toBeDisabled();
    await userEvent.type(
      screen.getByTestId("generate-token-name"),
      "release-bot",
    );
    expect(submit).not.toBeDisabled();
  });

  it("reveals the raw token on success and hides it again on dismiss", async () => {
    tokensState.data = { tokens: [] };
    generateMutate.mockImplementation(
      (
        _vars: unknown,
        opts: { onSuccess: (res: unknown) => void },
      ) => {
        opts.onSuccess({ id: "new1", token: "msk_secret_xyz" });
      },
    );
    renderWithProviders(<TokensCard />);
    await userEvent.click(screen.getByTestId("generate-token"));
    await userEvent.type(
      screen.getByTestId("generate-token-name"),
      "release-bot",
    );
    await userEvent.type(
      screen.getByTestId("generate-token-scopes"),
      "read write",
    );
    await userEvent.click(screen.getByTestId("generate-token-submit"));
    const raw = await screen.findByTestId("generate-token-raw");
    expect(raw).toHaveTextContent("msk_secret_xyz");
    expect(generateMutate.mock.calls[0]?.[0]).toEqual({
      name: "release-bot",
      scopes: ["read", "write"],
    });

    // "I've stored it" dismiss — dialog closes and the raw is forgotten.
    await userEvent.click(screen.getByTestId("generate-token-dismiss"));
    await waitFor(() =>
      expect(screen.queryByTestId("generate-token-raw")).toBeNull(),
    );

    // Re-open the dialog; the new-token reveal must NOT re-appear.
    await userEvent.click(screen.getByTestId("generate-token"));
    expect(screen.queryByTestId("generate-token-raw")).toBeNull();
    expect(screen.getByTestId("generate-token-name")).toHaveValue("");
  });

  it("copies the raw token to the clipboard", async () => {
    tokensState.data = { tokens: [] };
    generateMutate.mockImplementation(
      (_vars: unknown, opts: { onSuccess: (res: unknown) => void }) =>
        opts.onSuccess({ id: "new1", token: "msk_secret_xyz" }),
    );
    renderWithProviders(<TokensCard />);
    await userEvent.click(screen.getByTestId("generate-token"));
    await userEvent.type(
      screen.getByTestId("generate-token-name"),
      "release-bot",
    );
    await userEvent.click(screen.getByTestId("generate-token-submit"));
    await screen.findByTestId("generate-token-raw");
    await userEvent.click(screen.getByTestId("generate-token-copy"));
    await waitFor(() =>
      expect(clipboardWrite).toHaveBeenCalledWith("msk_secret_xyz"),
    );
  });

  it("toasts an error when generation fails", async () => {
    tokensState.data = { tokens: [] };
    generateMutate.mockImplementation(
      (_vars: unknown, opts: { onError: (e: Error) => void }) =>
        opts.onError(new Error("bad scope")),
    );
    renderWithProviders(<TokensCard />);
    await userEvent.click(screen.getByTestId("generate-token"));
    await userEvent.type(
      screen.getByTestId("generate-token-name"),
      "release-bot",
    );
    await userEvent.click(screen.getByTestId("generate-token-submit"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("bad scope"));
  });
});
