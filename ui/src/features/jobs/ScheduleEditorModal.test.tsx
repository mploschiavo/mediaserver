import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const mutationFns = vi.hoisted(() => ({
  add: vi.fn(),
  update: vi.fn(),
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useAddSchedule: () => ({ mutate: mutationFns.add, isPending: false }),
    useUpdateSchedule: () => ({ mutate: mutationFns.update, isPending: false }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { ScheduleEditorModal } from "./ScheduleEditorModal";
import type { ScheduleShape } from "./hooks";

function makeSched(overrides: Partial<ScheduleShape> = {}): ScheduleShape {
  return {
    id: 7,
    action: "media-integrity:scan",
    interval_seconds: 900,
    label: "MI scan",
    created_at: 1_700_000_000,
    last_run: 0,
    enabled: true,
    ...overrides,
  };
}

function reset() {
  mutationFns.add = vi.fn();
  mutationFns.update = vi.fn();
}

describe("ScheduleEditorModal", () => {
  it("does not render the dialog content when open=false", () => {
    reset();
    renderWithProviders(
      <ScheduleEditorModal open={false} editing={null} onClose={() => {}} />,
    );
    expect(screen.queryByTestId("schedule-editor-modal")).toBeNull();
  });

  it("renders create-mode title when editing=null", () => {
    reset();
    renderWithProviders(
      <ScheduleEditorModal open editing={null} onClose={() => {}} />,
    );
    expect(screen.getByTestId("schedule-editor-modal")).toHaveTextContent(
      /New schedule/,
    );
  });

  it("renders edit-mode title when editing!=null", () => {
    reset();
    renderWithProviders(
      <ScheduleEditorModal
        open
        editing={makeSched()}
        onClose={() => {}}
      />,
    );
    expect(screen.getByTestId("schedule-editor-modal")).toHaveTextContent(
      /Edit schedule/,
    );
  });

  it("hydrates fields from the editing schedule", () => {
    reset();
    renderWithProviders(
      <ScheduleEditorModal
        open
        editing={makeSched({ action: "scan", interval_seconds: 600 })}
        onClose={() => {}}
      />,
    );
    expect(
      (screen.getByTestId("schedule-editor-action") as HTMLInputElement).value,
    ).toBe("scan");
    expect(
      (screen.getByTestId("schedule-editor-interval") as HTMLInputElement)
        .value,
    ).toBe("600");
  });

  it("calls add mutation with the form values on submit (create mode)", () => {
    reset();
    renderWithProviders(
      <ScheduleEditorModal open editing={null} onClose={() => {}} />,
    );
    fireEvent.change(screen.getByTestId("schedule-editor-action"), {
      target: { value: "ensure-arr-download-client" },
    });
    fireEvent.change(screen.getByTestId("schedule-editor-interval"), {
      target: { value: "1800" },
    });
    fireEvent.submit(screen.getByTestId("schedule-editor-form"));
    expect(mutationFns.add).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "ensure-arr-download-client",
        interval_seconds: 1800,
        enabled: true,
      }),
      expect.any(Object),
    );
  });

  it("calls update mutation in edit mode and forwards the schedule_id", () => {
    reset();
    renderWithProviders(
      <ScheduleEditorModal
        open
        editing={makeSched({ id: 42 })}
        onClose={() => {}}
      />,
    );
    fireEvent.submit(screen.getByTestId("schedule-editor-form"));
    expect(mutationFns.update).toHaveBeenCalledWith(
      expect.objectContaining({ schedule_id: 42 }),
      expect.any(Object),
    );
  });

  it("rejects an empty action with a toast and does not mutate", async () => {
    reset();
    const { toast } = await import("sonner");
    renderWithProviders(
      <ScheduleEditorModal open editing={null} onClose={() => {}} />,
    );
    fireEvent.change(screen.getByTestId("schedule-editor-interval"), {
      target: { value: "300" },
    });
    fireEvent.submit(screen.getByTestId("schedule-editor-form"));
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("Action is required"),
    );
    expect(mutationFns.add).not.toHaveBeenCalled();
  });

  it("rejects an interval below the minimum with a toast and does not mutate", async () => {
    reset();
    const { toast } = await import("sonner");
    renderWithProviders(
      <ScheduleEditorModal open editing={null} onClose={() => {}} />,
    );
    fireEvent.change(screen.getByTestId("schedule-editor-action"), {
      target: { value: "scan" },
    });
    fireEvent.change(screen.getByTestId("schedule-editor-interval"), {
      target: { value: "30" },
    });
    fireEvent.submit(screen.getByTestId("schedule-editor-form"));
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("at least"),
    );
    expect(mutationFns.add).not.toHaveBeenCalled();
  });

  it("calls onClose when Cancel is pressed", () => {
    reset();
    const onClose = vi.fn();
    renderWithProviders(
      <ScheduleEditorModal open editing={null} onClose={onClose} />,
    );
    fireEvent.click(screen.getByTestId("schedule-editor-cancel"));
    expect(onClose).toHaveBeenCalled();
  });

  it("synthesizes a default label when none is provided", () => {
    reset();
    renderWithProviders(
      <ScheduleEditorModal open editing={null} onClose={() => {}} />,
    );
    fireEvent.change(screen.getByTestId("schedule-editor-action"), {
      target: { value: "scan-completed" },
    });
    fireEvent.change(screen.getByTestId("schedule-editor-interval"), {
      target: { value: "1200" },
    });
    fireEvent.submit(screen.getByTestId("schedule-editor-form"));
    expect(mutationFns.add).toHaveBeenCalledWith(
      expect.objectContaining({
        label: "scan-completed every 1200s",
      }),
      expect.any(Object),
    );
  });

  it("toggles the enabled checkbox", () => {
    reset();
    renderWithProviders(
      <ScheduleEditorModal open editing={null} onClose={() => {}} />,
    );
    const checkbox = screen.getByTestId(
      "schedule-editor-enabled",
    ) as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(false);
  });

  it("toasts error on a failed add mutation via onError callback", async () => {
    reset();
    const { toast } = await import("sonner");
    mutationFns.add = vi.fn(
      (
        _input: unknown,
        opts?: { onSuccess?: () => void; onError?: (e: Error) => void },
      ) => {
        opts?.onError?.(new Error("backend-said-no"));
      },
    );
    renderWithProviders(
      <ScheduleEditorModal open editing={null} onClose={() => {}} />,
    );
    fireEvent.change(screen.getByTestId("schedule-editor-action"), {
      target: { value: "scan" },
    });
    fireEvent.change(screen.getByTestId("schedule-editor-interval"), {
      target: { value: "600" },
    });
    fireEvent.submit(screen.getByTestId("schedule-editor-form"));
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("backend-said-no"),
    );
  });

  it("calls onClose + toasts success on a successful update mutation", async () => {
    reset();
    const onClose = vi.fn();
    const { toast } = await import("sonner");
    mutationFns.update = vi.fn(
      (
        _input: unknown,
        opts?: { onSuccess?: () => void; onError?: (e: Error) => void },
      ) => {
        opts?.onSuccess?.();
      },
    );
    renderWithProviders(
      <ScheduleEditorModal
        open
        editing={makeSched({ id: 99 })}
        onClose={onClose}
      />,
    );
    fireEvent.submit(screen.getByTestId("schedule-editor-form"));
    expect(onClose).toHaveBeenCalled();
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining("Schedule updated"),
    );
  });

  it("calls onClose after a successful add mutation", () => {
    reset();
    const onClose = vi.fn();
    mutationFns.add = vi.fn(
      (_input: unknown, opts?: { onSuccess?: () => void }) => {
        opts?.onSuccess?.();
      },
    );
    renderWithProviders(
      <ScheduleEditorModal open editing={null} onClose={onClose} />,
    );
    fireEvent.change(screen.getByTestId("schedule-editor-action"), {
      target: { value: "scan" },
    });
    fireEvent.change(screen.getByTestId("schedule-editor-interval"), {
      target: { value: "600" },
    });
    fireEvent.submit(screen.getByTestId("schedule-editor-form"));
    expect(onClose).toHaveBeenCalled();
  });
});
