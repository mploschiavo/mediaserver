import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const profileState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const saveMutate = vi.hoisted(() => vi.fn());
const saveState = vi.hoisted(() => ({ isPending: false }));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useProfileYaml: () => profileState,
    useSaveProfile: () => ({
      mutate: saveMutate,
      isPending: saveState.isPending,
    }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { ProfileEditorCard } from "./ProfileEditorCard";

function reset() {
  profileState.data = undefined;
  profileState.isLoading = false;
  profileState.error = null;
  saveMutate.mockReset();
  saveState.isPending = false;
  toastSuccess.mockReset();
  toastError.mockReset();
}

describe("ProfileEditorCard", () => {
  beforeEach(reset);

  it("renders a skeleton while loading", () => {
    profileState.isLoading = true;
    renderWithProviders(<ProfileEditorCard />);
    expect(screen.getByTestId("profile-editor-loading")).toBeInTheDocument();
  });

  it("renders the error banner on failure", () => {
    profileState.error = new Error("read failed");
    renderWithProviders(<ProfileEditorCard />);
    // ProfileEditorCard now delegates to the shared ApiErrorTile; the
    // generic (non-ApiError) variant renders under api-error-tile-generic.
    expect(screen.getByTestId("api-error-tile-generic")).toHaveTextContent(
      "read failed",
    );
  });

  it("seeds the textarea from the server YAML", () => {
    profileState.data = { yaml: "version: 1\n", saved_at: "" };
    renderWithProviders(<ProfileEditorCard />);
    const ta = screen.getByTestId(
      "profile-editor-textarea",
    ) as HTMLTextAreaElement;
    expect(ta.value).toContain("version: 1");
  });

  it("disables Save when the textarea is unchanged", () => {
    profileState.data = { yaml: "version: 1\n" };
    renderWithProviders(<ProfileEditorCard />);
    expect(screen.getByTestId("profile-editor-save")).toBeDisabled();
  });

  it("enables Save once the user edits and fires the mutation", async () => {
    profileState.data = { yaml: "version: 1\n" };
    renderWithProviders(<ProfileEditorCard />);
    const ta = screen.getByTestId(
      "profile-editor-textarea",
    ) as HTMLTextAreaElement;
    await userEvent.type(ta, " ");
    expect(screen.getByTestId("profile-editor-save")).not.toBeDisabled();
    await userEvent.click(screen.getByTestId("profile-editor-save"));
    expect(saveMutate).toHaveBeenCalledOnce();
    expect(saveMutate.mock.calls[0]?.[0]).toMatchObject({
      yaml: expect.stringContaining("version: 1"),
    });
  });

  it("toasts success on save and resets the dirty flag", async () => {
    profileState.data = { yaml: "a: 1" };
    saveMutate.mockImplementation(
      (_vars: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(<ProfileEditorCard />);
    const ta = screen.getByTestId(
      "profile-editor-textarea",
    ) as HTMLTextAreaElement;
    await userEvent.type(ta, " ");
    await userEvent.click(screen.getByTestId("profile-editor-save"));
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith("Profile saved"),
    );
  });

  it("toasts error on failure and keeps the user's edits", async () => {
    profileState.data = { yaml: "a: 1" };
    saveMutate.mockImplementation(
      (_vars: unknown, opts: { onError: (e: Error) => void }) =>
        opts.onError(new Error("rate limit")),
    );
    renderWithProviders(<ProfileEditorCard />);
    const ta = screen.getByTestId(
      "profile-editor-textarea",
    ) as HTMLTextAreaElement;
    await userEvent.type(ta, " more");
    await userEvent.click(screen.getByTestId("profile-editor-save"));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("rate limit"),
    );
    expect(ta.value).toContain("more");
  });

  it("renders the saved-at timestamp when present", () => {
    profileState.data = {
      yaml: "a: 1",
      saved_at: new Date(Date.now() - 60_000).toISOString(),
    };
    renderWithProviders(<ProfileEditorCard />);
    expect(screen.getByTestId("profile-editor-saved-at")).toHaveTextContent(
      /Last saved/,
    );
  });
});
