import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";
import { ReconcileButton } from "./ReconcileButton";

const mutate = vi.hoisted(() => vi.fn());
const reconcileState = vi.hoisted(
  () =>
    ({ isPending: false, data: undefined as unknown }) as {
      isPending: boolean;
      data: unknown;
    },
);
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    useReconcile: () => ({
      mutate,
      isPending: reconcileState.isPending,
      data: reconcileState.data,
      error: null,
    }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

describe("ReconcileButton", () => {
  beforeEach(() => {
    mutate.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    reconcileState.isPending = false;
    reconcileState.data = undefined;
  });
  afterEach(() => {
    mutate.mockReset();
  });

  it("renders the 'Reconcile now' label by default", () => {
    renderWithProviders(<ReconcileButton />);
    expect(
      screen.getByRole("button", { name: /Reconcile now/ }),
    ).toBeInTheDocument();
  });

  it("morphs the label to 'Dry-run reconcile' when the checkbox is on", async () => {
    renderWithProviders(<ReconcileButton />);
    await userEvent.click(screen.getByTestId("reconcile-dry-run"));
    expect(
      await screen.findByRole("button", { name: /Dry-run reconcile/ }),
    ).toBeInTheDocument();
  });

  it("fires the mutation with dryRun=false on default click", async () => {
    renderWithProviders(<ReconcileButton />);
    await userEvent.click(screen.getByTestId("reconcile-button"));
    expect(mutate).toHaveBeenCalledOnce();
    expect(mutate.mock.calls[0]?.[0]).toEqual({ dryRun: false });
  });

  it("fires with dryRun=true after toggling the checkbox", async () => {
    renderWithProviders(<ReconcileButton />);
    await userEvent.click(screen.getByTestId("reconcile-dry-run"));
    await userEvent.click(screen.getByTestId("reconcile-button"));
    expect(mutate.mock.calls[0]?.[0]).toEqual({ dryRun: true });
  });

  it("respects the disabled prop", async () => {
    renderWithProviders(<ReconcileButton disabled />);
    expect(screen.getByTestId("reconcile-button")).toBeDisabled();
    await userEvent.click(screen.getByTestId("reconcile-button"));
    expect(mutate).not.toHaveBeenCalled();
  });

  it("toasts a success message with the freed bytes on success", async () => {
    mutate.mockImplementation(
      (
        _vars: { dryRun: boolean },
        opts: { onSuccess: (data: unknown) => void },
      ) => {
        const detail = { bytes_freed: 1024 * 1024 * 1024 };
        reconcileState.data = detail;
        // React Query forwards the mutation result into onSuccess
        // as the first argument; our component reads it from there
        // rather than from `mutation.data` (which only updates on
        // the next tick and would race the click).
        opts.onSuccess(detail);
      },
    );
    renderWithProviders(<ReconcileButton />);
    await userEvent.click(screen.getByTestId("reconcile-button"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(toastSuccess.mock.calls[0]?.[0]).toMatch(/Reconcile complete/);
    expect(toastSuccess.mock.calls[0]?.[0]).toMatch(/GB/);
  });

  it("uses the dry-run wording in the success toast when dryRun is on", async () => {
    mutate.mockImplementation(
      (_vars: { dryRun: boolean }, opts: { onSuccess: () => void }) => {
        reconcileState.data = { bytes_freed: 0 };
        opts.onSuccess();
      },
    );
    renderWithProviders(<ReconcileButton />);
    await userEvent.click(screen.getByTestId("reconcile-dry-run"));
    await userEvent.click(screen.getByTestId("reconcile-button"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(toastSuccess.mock.calls[0]?.[0]).toMatch(/Dry-run preview/);
  });

  it("toasts the error on failure", async () => {
    mutate.mockImplementation(
      (_vars: { dryRun: boolean }, opts: { onError: (e: Error) => void }) => {
        opts.onError(new Error("rate limit"));
      },
    );
    renderWithProviders(<ReconcileButton />);
    await userEvent.click(screen.getByTestId("reconcile-button"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("rate limit"));
  });
});
