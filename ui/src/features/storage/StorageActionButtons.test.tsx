import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const cleanupMutate = vi.hoisted(() => vi.fn());
const engageMutate = vi.hoisted(() => vi.fn());
const releaseMutate = vi.hoisted(() => vi.fn());
const pauseMutate = vi.hoisted(() => vi.fn());
const evaluateMutate = vi.hoisted(() => vi.fn());

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useRunCleanup: () => ({
      mutate: cleanupMutate,
      isPending: false,
    }),
    useEngageLockdown: () => ({
      mutate: engageMutate,
      isPending: false,
    }),
    useReleaseLockdown: () => ({
      mutate: releaseMutate,
      isPending: false,
    }),
    usePauseGuardrails: () => ({
      mutate: pauseMutate,
      isPending: false,
    }),
    useForceEvaluate: () => ({
      mutate: evaluateMutate,
      isPending: false,
    }),
  };
});

const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { StorageActionButtons } from "./StorageActionButtons";

beforeEach(() => {
  cleanupMutate.mockReset();
  engageMutate.mockReset();
  releaseMutate.mockReset();
  pauseMutate.mockReset();
  evaluateMutate.mockReset();
  toastSuccess.mockReset();
  toastError.mockReset();
});

describe("StorageActionButtons", () => {
  it("disables Engage in MANUAL_LOCKDOWN", () => {
    renderWithProviders(<StorageActionButtons state="MANUAL_LOCKDOWN" />);
    expect(screen.getByTestId("storage-action-engage")).toBeDisabled();
    expect(screen.getByTestId("storage-action-release")).not.toBeDisabled();
  });

  it("disables Release in NORMAL", () => {
    renderWithProviders(<StorageActionButtons state="NORMAL" />);
    expect(screen.getByTestId("storage-action-release")).toBeDisabled();
    expect(screen.getByTestId("storage-action-engage")).not.toBeDisabled();
  });

  it("opens confirmation dialog and POSTs on confirm (engage)", async () => {
    engageMutate.mockImplementation(
      (_v: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(<StorageActionButtons state="NORMAL" />);
    await userEvent.click(screen.getByTestId("storage-action-engage"));
    expect(
      await screen.findByTestId("storage-confirm-dialog"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("storage-confirm-submit"));
    await waitFor(() => expect(engageMutate).toHaveBeenCalledOnce());
    expect(toastSuccess).toHaveBeenCalledWith("Lockdown engaged");
  });

  it("confirms release and POSTs", async () => {
    releaseMutate.mockImplementation(
      (_v: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(<StorageActionButtons state="MANUAL_LOCKDOWN" />);
    await userEvent.click(screen.getByTestId("storage-action-release"));
    await screen.findByTestId("storage-confirm-dialog");
    await userEvent.click(screen.getByTestId("storage-confirm-submit"));
    await waitFor(() => expect(releaseMutate).toHaveBeenCalledOnce());
  });

  it("confirms cleanup and shows freed-gb in the toast", async () => {
    cleanupMutate.mockImplementation(
      (
        _v: unknown,
        opts: { onSuccess: (r: { deleted: number; freed_gb: number }) => void },
      ) => opts.onSuccess({ deleted: 14, freed_gb: 32.5 }),
    );
    renderWithProviders(<StorageActionButtons state="NORMAL" />);
    await userEvent.click(screen.getByTestId("storage-action-cleanup"));
    await screen.findByTestId("storage-confirm-dialog");
    await userEvent.click(screen.getByTestId("storage-confirm-submit"));
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith(
        expect.stringContaining("deleted 14"),
      ),
    );
  });

  it("opens the pause picker and POSTs the chosen hours", async () => {
    pauseMutate.mockImplementation(
      (_v: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(<StorageActionButtons state="NORMAL" />);
    await userEvent.click(screen.getByTestId("storage-action-pause"));
    await screen.findByTestId("storage-pause-dialog");
    await userEvent.click(screen.getByTestId("storage-pause-submit"));
    await waitFor(() => expect(pauseMutate).toHaveBeenCalledOnce());
    const args = pauseMutate.mock.calls[0]?.[0] as { hours: number };
    expect(args.hours).toBeGreaterThanOrEqual(1);
    expect(args.hours).toBeLessThanOrEqual(24);
  });

  it("Force evaluate fires immediately without confirmation", async () => {
    evaluateMutate.mockImplementation(
      (_v: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(<StorageActionButtons state="NORMAL" />);
    await userEvent.click(screen.getByTestId("storage-action-evaluate"));
    expect(evaluateMutate).toHaveBeenCalledOnce();
    expect(toastSuccess).toHaveBeenCalledWith("Evaluation complete");
  });

  it("disables every mutating button when read-only", () => {
    renderWithProviders(
      <StorageActionButtons state="NORMAL" readOnly />,
    );
    for (const id of [
      "storage-action-cleanup",
      "storage-action-engage",
      "storage-action-pause",
      "storage-action-evaluate",
    ]) {
      expect(screen.getByTestId(id)).toBeDisabled();
    }
  });
});
