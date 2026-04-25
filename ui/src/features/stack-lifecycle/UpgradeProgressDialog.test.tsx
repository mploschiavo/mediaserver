import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const progressState = vi.hoisted(() => ({
  data: undefined as
    | {
        state: "queued" | "running" | "done" | "failed";
        progress?: number;
        log_tail?: readonly string[];
      }
    | undefined,
}));

vi.mock("./hooks", () => ({
  useStackUpgradeProgress: () => ({
    data: progressState.data,
    isLoading: false,
    error: null,
  }),
}));

import { UpgradeProgressDialog } from "./UpgradeProgressDialog";

describe("UpgradeProgressDialog", () => {
  const onClose = vi.fn();
  beforeEach(() => {
    onClose.mockReset();
    progressState.data = { state: "running", progress: 0.5, log_tail: [] };
  });
  afterEach(() => {
    progressState.data = undefined;
  });

  it("renders the dialog with the current state", () => {
    renderWithProviders(
      <UpgradeProgressDialog taskId="t1" onClose={onClose} />,
    );
    expect(screen.getByTestId("upgrade-progress-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("upgrade-progress-state")).toHaveTextContent(
      /Upgrade in progress/,
    );
  });

  it("renders the progress bar with the current percentage", () => {
    renderWithProviders(
      <UpgradeProgressDialog taskId="t1" onClose={onClose} />,
    );
    const bar = screen.getByTestId("upgrade-progress-bar");
    expect(bar).toHaveAttribute("aria-valuenow", "50");
  });

  it("renders only the last 50 log lines", () => {
    const lines = Array.from({ length: 75 }, (_, i) => `line-${i}`);
    progressState.data = {
      state: "running",
      progress: 0.1,
      log_tail: lines,
    };
    renderWithProviders(
      <UpgradeProgressDialog taskId="t1" onClose={onClose} />,
    );
    const log = screen.getByTestId("upgrade-progress-log");
    // First (line-0..line-24) must be sliced off. Last (line-74) kept.
    expect(log.textContent).not.toContain("line-0\n");
    expect(log.textContent).toContain("line-25");
    expect(log.textContent).toContain("line-74");
  });

  it("does not call onClose while running (escape vetoed)", async () => {
    renderWithProviders(
      <UpgradeProgressDialog taskId="t1" onClose={onClose} />,
    );
    await userEvent.keyboard("{Escape}");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("calls onClose once the task finishes and the user closes", async () => {
    progressState.data = { state: "done", progress: 1, log_tail: ["bye"] };
    renderWithProviders(
      <UpgradeProgressDialog taskId="t1" onClose={onClose} />,
    );
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("renders 'Upgrade failed' when the task failed", () => {
    progressState.data = { state: "failed", log_tail: ["boom"] };
    renderWithProviders(
      <UpgradeProgressDialog taskId="t1" onClose={onClose} />,
    );
    expect(screen.getByTestId("upgrade-progress-state")).toHaveTextContent(
      /Upgrade failed/,
    );
  });
});
