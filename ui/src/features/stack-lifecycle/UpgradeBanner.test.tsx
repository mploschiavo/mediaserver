import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const updateState = vi.hoisted(() => ({
  data: undefined as
    | { available: boolean; current_version?: string; latest_version?: string; release_notes?: string }
    | undefined,
  error: null as Error | null,
}));
const upgradeState = vi.hoisted(() => ({ isPending: false }));
const upgradeMutate = vi.hoisted(() => vi.fn());
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useStackUpdate: () => ({
    data: updateState.data,
    error: updateState.error,
    isLoading: false,
  }),
  useStackUpgrade: () => ({
    mutate: upgradeMutate,
    isPending: upgradeState.isPending,
    error: null,
  }),
  // The progress dialog is mounted by the banner once a task_id is
  // captured; stub the hook so the dialog mounts without firing a
  // real fetch.
  useStackUpgradeProgress: () => ({
    data: { state: "running", log_tail: [] },
    isLoading: false,
    error: null,
  }),
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { UpgradeBanner } from "./UpgradeBanner";

describe("UpgradeBanner", () => {
  beforeEach(() => {
    updateState.data = {
      available: true,
      current_version: "1.4.0",
      latest_version: "1.5.0",
      release_notes: "## Whats new\n- thing",
    };
    updateState.error = null;
    upgradeState.isPending = false;
    upgradeMutate.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
  });
  afterEach(() => {
    upgradeMutate.mockReset();
  });

  it("renders nothing when the probe errors", () => {
    updateState.data = undefined;
    updateState.error = new Error("offline");
    const { container } = renderWithProviders(<UpgradeBanner />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when no update is available", () => {
    updateState.data = { available: false };
    const { container } = renderWithProviders(<UpgradeBanner />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the banner when an update is available", () => {
    renderWithProviders(<UpgradeBanner />);
    expect(screen.getByTestId("upgrade-banner")).toBeInTheDocument();
    expect(screen.getByTestId("upgrade-banner-current")).toHaveTextContent(
      "1.4.0",
    );
    expect(screen.getByTestId("upgrade-banner-latest")).toHaveTextContent(
      "1.5.0",
    );
  });

  it("opens the dialog with the release notes preview", async () => {
    renderWithProviders(<UpgradeBanner />);
    await userEvent.click(screen.getByTestId("upgrade-banner-trigger"));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByTestId("upgrade-banner-release-notes"),
    ).toHaveTextContent(/Whats new/);
  });

  it("keeps Confirm disabled until the operator types UPGRADE exactly", async () => {
    renderWithProviders(<UpgradeBanner />);
    await userEvent.click(screen.getByTestId("upgrade-banner-trigger"));
    const confirm = screen.getByTestId("upgrade-banner-confirm");
    const input = screen.getByTestId("upgrade-banner-confirm-input");

    // empty
    expect(confirm).toBeDisabled();

    // wrong case must NOT enable (no toLowerCase)
    await userEvent.type(input, "upgrade");
    expect(confirm).toBeDisabled();

    // partial must NOT enable
    await userEvent.clear(input);
    await userEvent.type(input, "UPGR");
    expect(confirm).toBeDisabled();

    // surrounding whitespace must NOT enable (no trim)
    await userEvent.clear(input);
    await userEvent.type(input, " UPGRADE");
    expect(confirm).toBeDisabled();

    await userEvent.clear(input);
    await userEvent.type(input, "UPGRADE ");
    expect(confirm).toBeDisabled();

    // exact match enables
    await userEvent.clear(input);
    await userEvent.type(input, "UPGRADE");
    expect(confirm).toBeEnabled();
  });

  it("fires the upgrade mutation on confirm and switches to progress view", async () => {
    upgradeMutate.mockImplementation(
      (
        _v: undefined,
        opts: { onSuccess: (out: { task_id: string }) => void },
      ) => {
        opts.onSuccess({ task_id: "task-1" });
      },
    );
    renderWithProviders(<UpgradeBanner />);
    await userEvent.click(screen.getByTestId("upgrade-banner-trigger"));
    await userEvent.type(
      screen.getByTestId("upgrade-banner-confirm-input"),
      "UPGRADE",
    );
    await userEvent.click(screen.getByTestId("upgrade-banner-confirm"));
    expect(upgradeMutate).toHaveBeenCalledOnce();
    await waitFor(() =>
      expect(screen.getByTestId("upgrade-progress-dialog")).toBeInTheDocument(),
    );
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("toasts the error when the mutation rejects", async () => {
    upgradeMutate.mockImplementation(
      (_v: undefined, opts: { onError: (e: Error) => void }) => {
        opts.onError(new Error("rate limited"));
      },
    );
    renderWithProviders(<UpgradeBanner />);
    await userEvent.click(screen.getByTestId("upgrade-banner-trigger"));
    await userEvent.type(
      screen.getByTestId("upgrade-banner-confirm-input"),
      "UPGRADE",
    );
    await userEvent.click(screen.getByTestId("upgrade-banner-confirm"));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("rate limited"),
    );
  });
});
