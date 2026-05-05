import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const updateMutate = vi.hoisted(() => vi.fn());
const updateState = vi.hoisted(() => ({ isPending: false }));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useUpdateThresholds: () => ({
      mutate: updateMutate,
      isPending: updateState.isPending,
    }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import {
  StorageThresholdInputs,
  validateThresholds,
} from "./StorageThresholdInputs";

beforeEach(() => {
  updateMutate.mockReset();
  updateState.isPending = false;
});

describe("validateThresholds", () => {
  it("accepts the default ordering", () => {
    expect(
      validateThresholds({
        watch: 50,
        cleanup: 70,
        lockdown: 75,
        release: 60,
      }).ok,
    ).toBe(true);
  });
  it("rejects release >= lockdown (no hysteresis)", () => {
    const r = validateThresholds({
      watch: 50,
      cleanup: 70,
      lockdown: 75,
      release: 75,
    });
    expect(r.ok).toBe(false);
    expect(r.message).toMatch(/Release.*Lockdown/i);
  });
  it("rejects watch > cleanup", () => {
    const r = validateThresholds({
      watch: 80,
      cleanup: 70,
      lockdown: 75,
      release: 60,
    });
    expect(r.ok).toBe(false);
  });
  it("rejects out-of-range values", () => {
    const r = validateThresholds({
      watch: 0,
      cleanup: 70,
      lockdown: 75,
      release: 60,
    });
    expect(r.ok).toBe(false);
  });
});

describe("StorageThresholdInputs (component)", () => {
  it("disables Save until values change", () => {
    renderWithProviders(
      <StorageThresholdInputs
        defaults={{
          watch_percent: 50,
          cleanup_percent: 70,
          lockdown_percent: 75,
          release_percent: 60,
        }}
      />,
    );
    const save = screen.getByTestId("storage-threshold-save");
    expect(save).toBeDisabled();
  });

  it("enables Save when values change AND validation passes", () => {
    renderWithProviders(
      <StorageThresholdInputs
        defaults={{
          watch_percent: 50,
          cleanup_percent: 70,
          lockdown_percent: 75,
          release_percent: 60,
        }}
      />,
    );
    fireEvent.change(screen.getByTestId("storage-threshold-lockdown"), {
      target: { value: "80" },
    });
    expect(screen.getByTestId("storage-threshold-save")).not.toBeDisabled();
  });

  it("blocks Save and shows validation error when release > lockdown", () => {
    renderWithProviders(
      <StorageThresholdInputs
        defaults={{
          watch_percent: 50,
          cleanup_percent: 70,
          lockdown_percent: 75,
          release_percent: 60,
        }}
      />,
    );
    fireEvent.change(screen.getByTestId("storage-threshold-release"), {
      target: { value: "90" },
    });
    expect(
      screen.getByTestId("storage-threshold-validation"),
    ).toHaveAttribute("data-tone", "critical");
    expect(screen.getByTestId("storage-threshold-save")).toBeDisabled();
  });

  it("posts the camelCase payload through the hook on Save", async () => {
    updateMutate.mockImplementation(
      (_v: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(
      <StorageThresholdInputs
        defaults={{
          watch_percent: 50,
          cleanup_percent: 70,
          lockdown_percent: 75,
          release_percent: 60,
        }}
      />,
    );
    fireEvent.change(screen.getByTestId("storage-threshold-lockdown"), {
      target: { value: "78" },
    });
    fireEvent.click(screen.getByTestId("storage-threshold-save"));
    await waitFor(() => expect(updateMutate).toHaveBeenCalledOnce());
    const args = updateMutate.mock.calls[0]?.[0] as Record<string, number>;
    expect(args.lockdownPercent).toBe(78);
    expect(args.releasePercent).toBe(60);
  });

  it("disables Save when read-only", () => {
    renderWithProviders(
      <StorageThresholdInputs
        defaults={{
          watch_percent: 50,
          cleanup_percent: 70,
          lockdown_percent: 75,
          release_percent: 60,
        }}
        readOnly
      />,
    );
    fireEvent.change(screen.getByTestId("storage-threshold-lockdown"), {
      target: { value: "80" },
    });
    expect(screen.getByTestId("storage-threshold-save")).toBeDisabled();
  });
});
