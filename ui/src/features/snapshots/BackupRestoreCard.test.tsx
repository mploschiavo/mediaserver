import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const downloadMutate = vi.hoisted(() => vi.fn());
const restoreMutate = vi.hoisted(() => vi.fn());
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useDownloadBackup: () => ({ mutate: downloadMutate, isPending: false }),
  useRestoreBackup: () => ({ mutate: restoreMutate, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { BackupRestoreCard } from "./BackupRestoreCard";

describe("BackupRestoreCard", () => {
  beforeEach(() => {
    downloadMutate.mockReset();
    restoreMutate.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
  });
  afterEach(() => {
    downloadMutate.mockReset();
    restoreMutate.mockReset();
  });

  it("renders the download and restore buttons", () => {
    renderWithProviders(<BackupRestoreCard />);
    expect(screen.getByTestId("backup-download")).toBeInTheDocument();
    expect(screen.getByTestId("backup-restore-trigger")).toBeInTheDocument();
  });

  it("fires the download mutation when Download backup is clicked", async () => {
    renderWithProviders(<BackupRestoreCard />);
    await userEvent.click(screen.getByTestId("backup-download"));
    expect(downloadMutate).toHaveBeenCalledOnce();
  });

  it("opens the restore dialog when Restore is clicked", async () => {
    renderWithProviders(<BackupRestoreCard />);
    await userEvent.click(screen.getByTestId("backup-restore-trigger"));
    expect(
      await screen.findByTestId("backup-restore-dialog"),
    ).toBeInTheDocument();
  });

  it("keeps Confirm disabled until both file and exact phrase are present", async () => {
    renderWithProviders(<BackupRestoreCard />);
    await userEvent.click(screen.getByTestId("backup-restore-trigger"));
    const confirm = screen.getByTestId("backup-restore-confirm");
    const phraseInput = screen.getByTestId("backup-restore-confirm-input");
    const fileInput = screen.getByTestId(
      "backup-restore-file-input",
    ) as HTMLInputElement;

    // empty
    expect(confirm).toBeDisabled();

    // wrong case
    await userEvent.type(phraseInput, "restore");
    expect(confirm).toBeDisabled();

    // partial
    await userEvent.clear(phraseInput);
    await userEvent.type(phraseInput, "REST");
    expect(confirm).toBeDisabled();

    // surrounding whitespace
    await userEvent.clear(phraseInput);
    await userEvent.type(phraseInput, " RESTORE");
    expect(confirm).toBeDisabled();
    await userEvent.clear(phraseInput);
    await userEvent.type(phraseInput, "RESTORE ");
    expect(confirm).toBeDisabled();

    // exact phrase but no file: still disabled
    await userEvent.clear(phraseInput);
    await userEvent.type(phraseInput, "RESTORE");
    expect(confirm).toBeDisabled();

    // attach a file: now enabled
    const file = new File(
      [JSON.stringify({ service_configs: { "x.cfg": "1" } })],
      "backup.json",
      { type: "application/json" },
    );
    await userEvent.upload(fileInput, file);
    expect(confirm).toBeEnabled();
  });

  it("fires the restore mutation with the file when Confirm is clicked", async () => {
    renderWithProviders(<BackupRestoreCard />);
    await userEvent.click(screen.getByTestId("backup-restore-trigger"));
    const phraseInput = screen.getByTestId("backup-restore-confirm-input");
    const fileInput = screen.getByTestId(
      "backup-restore-file-input",
    ) as HTMLInputElement;
    const file = new File(
      [JSON.stringify({ service_configs: { "x.cfg": "1" } })],
      "backup.json",
      { type: "application/json" },
    );
    await userEvent.upload(fileInput, file);
    await userEvent.type(phraseInput, "RESTORE");
    await userEvent.click(screen.getByTestId("backup-restore-confirm"));
    expect(restoreMutate).toHaveBeenCalledOnce();
    const call = restoreMutate.mock.calls[0]?.[0] as { file: File };
    expect(call.file.name).toBe("backup.json");
  });

  it("toasts success when restore resolves", async () => {
    restoreMutate.mockImplementation(
      (
        _v: unknown,
        opts: {
          onSuccess: (out: {
            status: string;
            restored?: string[];
            errors?: string[];
          }) => void;
        },
      ) => {
        opts.onSuccess({ status: "ok", restored: ["x.cfg", "y.cfg"], errors: [] });
      },
    );
    renderWithProviders(<BackupRestoreCard />);
    await userEvent.click(screen.getByTestId("backup-restore-trigger"));
    const fileInput = screen.getByTestId(
      "backup-restore-file-input",
    ) as HTMLInputElement;
    const file = new File(
      [JSON.stringify({ service_configs: {} })],
      "backup.json",
      { type: "application/json" },
    );
    await userEvent.upload(fileInput, file);
    await userEvent.type(
      screen.getByTestId("backup-restore-confirm-input"),
      "RESTORE",
    );
    await userEvent.click(screen.getByTestId("backup-restore-confirm"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(toastSuccess.mock.calls[0]?.[0]).toMatch(/Restored 2 files/);
  });
});
