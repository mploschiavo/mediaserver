import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";
import { EnforceButton } from "./EnforceButton";

const mutate = vi.hoisted(() => vi.fn());
const enforceState = vi.hoisted(() => ({ isPending: false }));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    useEnforceConfig: () => ({
      mutate,
      isPending: enforceState.isPending,
      error: null,
    }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

describe("EnforceButton", () => {
  beforeEach(() => {
    mutate.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    enforceState.isPending = false;
  });
  afterEach(() => {
    mutate.mockReset();
  });

  it("renders the 'Enforce config' label", () => {
    renderWithProviders(<EnforceButton />);
    expect(
      screen.getByRole("button", { name: /Enforce config/ }),
    ).toBeInTheDocument();
  });

  it("fires the mutation on click", async () => {
    renderWithProviders(<EnforceButton />);
    await userEvent.click(screen.getByTestId("enforce-button"));
    expect(mutate).toHaveBeenCalledOnce();
  });

  it("respects the disabled prop", async () => {
    renderWithProviders(<EnforceButton disabled />);
    expect(screen.getByTestId("enforce-button")).toBeDisabled();
    await userEvent.click(screen.getByTestId("enforce-button"));
    expect(mutate).not.toHaveBeenCalled();
  });

  it("toasts the change count on success", async () => {
    mutate.mockImplementation(
      (
        _v: undefined,
        opts: { onSuccess: (out: Record<string, unknown>) => void },
      ) => {
        opts.onSuccess({ changes: 3 });
      },
    );
    renderWithProviders(<EnforceButton />);
    await userEvent.click(screen.getByTestId("enforce-button"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(toastSuccess.mock.calls[0]?.[0]).toMatch(/3 fields flipped/);
  });

  it("toasts the singular wording when exactly one change", async () => {
    mutate.mockImplementation(
      (
        _v: undefined,
        opts: { onSuccess: (out: Record<string, unknown>) => void },
      ) => {
        opts.onSuccess({ changes: 1 });
      },
    );
    renderWithProviders(<EnforceButton />);
    await userEvent.click(screen.getByTestId("enforce-button"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(toastSuccess.mock.calls[0]?.[0]).toMatch(/1 field flipped/);
  });

  it("toasts 'Everything compliant' when no changes", async () => {
    mutate.mockImplementation(
      (
        _v: undefined,
        opts: { onSuccess: (out: Record<string, unknown>) => void },
      ) => {
        opts.onSuccess({ changes: 0 });
      },
    );
    renderWithProviders(<EnforceButton />);
    await userEvent.click(screen.getByTestId("enforce-button"));
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith("Everything compliant"),
    );
  });

  it("toasts the error on failure", async () => {
    mutate.mockImplementation(
      (_v: undefined, opts: { onError: (e: Error) => void }) => {
        opts.onError(new Error("offline"));
      },
    );
    renderWithProviders(<EnforceButton />);
    await userEvent.click(screen.getByTestId("enforce-button"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("offline"));
  });
});
