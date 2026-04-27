import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const schedulesState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const mutationFns = vi.hoisted(() => ({
  pause: vi.fn(),
  resume: vi.fn(),
  remove: vi.fn(),
  add: vi.fn(),
  update: vi.fn(),
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useSchedules: () => ({
      data: schedulesState.data,
      isLoading: schedulesState.isLoading,
      error: schedulesState.error,
    }),
    usePauseSchedule: () => ({ mutate: mutationFns.pause, isPending: false }),
    useResumeSchedule: () => ({ mutate: mutationFns.resume, isPending: false }),
    useDeleteSchedule: () => ({ mutate: mutationFns.remove, isPending: false }),
    useAddSchedule: () => ({ mutate: mutationFns.add, isPending: false }),
    useUpdateSchedule: () => ({ mutate: mutationFns.update, isPending: false }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { SchedulesCard } from "./SchedulesCard";
import type { ScheduleShape } from "./hooks";

function makeSched(overrides: Partial<ScheduleShape> = {}): ScheduleShape {
  return {
    id: 1,
    action: "scan-completed-downloads",
    interval_seconds: 900,
    label: "scan every 15m",
    created_at: 1_700_000_000,
    last_run: 0,
    enabled: true,
    ...overrides,
  };
}

function reset() {
  schedulesState.data = undefined;
  schedulesState.isLoading = false;
  schedulesState.error = null;
  mutationFns.pause = vi.fn();
  mutationFns.resume = vi.fn();
  mutationFns.remove = vi.fn();
}

describe("SchedulesCard", () => {
  it("renders skeletons while loading", () => {
    reset();
    schedulesState.isLoading = true;
    renderWithProviders(<SchedulesCard />);
    expect(screen.getByTestId("schedules-loading")).toBeInTheDocument();
  });

  it("renders the empty state when zero schedules are configured", () => {
    reset();
    schedulesState.data = { schedules: [], count: 0 };
    renderWithProviders(<SchedulesCard />);
    expect(screen.getByTestId("schedules-empty")).toBeInTheDocument();
  });

  it("renders an error alert on fetch failure", () => {
    reset();
    schedulesState.error = new Error("nope");
    renderWithProviders(<SchedulesCard />);
    const err = screen.getByTestId("schedules-error");
    expect(err).toHaveTextContent(/nope/);
  });

  it("groups schedules by action namespace prefix", () => {
    reset();
    schedulesState.data = {
      schedules: [
        makeSched({ id: 1, action: "media-integrity:scan", label: "MI scan" }),
        makeSched({ id: 2, action: "media-integrity:enforce", label: "MI enforce" }),
        makeSched({ id: 3, action: "scan-completed-downloads", label: "Scan DL" }),
      ],
      count: 3,
    };
    renderWithProviders(<SchedulesCard />);
    // Each row mounts under its group; groups are sorted alphabetically.
    expect(screen.getByTestId("schedule-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("schedule-row-2")).toBeInTheDocument();
    expect(screen.getByTestId("schedule-row-3")).toBeInTheDocument();
    const list = screen.getByTestId("schedules-list");
    expect(list.textContent).toMatch(/general/);
    expect(list.textContent).toMatch(/media-integrity/);
  });

  it("flips paused row's data-attribute and surfaces the paused badge", () => {
    reset();
    schedulesState.data = {
      schedules: [makeSched({ id: 1, enabled: false })],
      count: 1,
    };
    renderWithProviders(<SchedulesCard />);
    expect(screen.getByTestId("schedule-row-1")).toHaveAttribute(
      "data-enabled",
      "false",
    );
  });

  it("calls pause mutation when an enabled row's toggle is clicked", () => {
    reset();
    schedulesState.data = {
      schedules: [makeSched({ id: 1, enabled: true })],
      count: 1,
    };
    renderWithProviders(<SchedulesCard />);
    fireEvent.click(screen.getByTestId("schedule-toggle-1"));
    expect(mutationFns.pause).toHaveBeenCalledWith(1, expect.any(Object));
  });

  it("calls resume mutation when a paused row's toggle is clicked", () => {
    reset();
    schedulesState.data = {
      schedules: [makeSched({ id: 1, enabled: false })],
      count: 1,
    };
    renderWithProviders(<SchedulesCard />);
    fireEvent.click(screen.getByTestId("schedule-toggle-1"));
    expect(mutationFns.resume).toHaveBeenCalledWith(1, expect.any(Object));
  });

  it("calls remove mutation after confirm() returns true", () => {
    reset();
    schedulesState.data = {
      schedules: [makeSched({ id: 1 })],
      count: 1,
    };
    const confirmSpy = vi
      .spyOn(window, "confirm")
      .mockReturnValue(true);
    try {
      renderWithProviders(<SchedulesCard />);
      fireEvent.click(screen.getByTestId("schedule-remove-1"));
      expect(mutationFns.remove).toHaveBeenCalledWith(1, expect.any(Object));
    } finally {
      confirmSpy.mockRestore();
    }
  });

  it("does not call remove when confirm() is cancelled", () => {
    reset();
    schedulesState.data = {
      schedules: [makeSched({ id: 1 })],
      count: 1,
    };
    const confirmSpy = vi
      .spyOn(window, "confirm")
      .mockReturnValue(false);
    try {
      renderWithProviders(<SchedulesCard />);
      fireEvent.click(screen.getByTestId("schedule-remove-1"));
      expect(mutationFns.remove).not.toHaveBeenCalled();
    } finally {
      confirmSpy.mockRestore();
    }
  });

  it("opens the editor modal when + Schedule is clicked", () => {
    reset();
    schedulesState.data = { schedules: [], count: 0 };
    renderWithProviders(<SchedulesCard />);
    fireEvent.click(screen.getByTestId("schedules-add"));
    expect(screen.getByTestId("schedule-editor-modal")).toBeInTheDocument();
  });

  it("opens the editor in edit mode when an edit pencil is clicked", () => {
    reset();
    schedulesState.data = {
      schedules: [makeSched({ id: 7 })],
      count: 1,
    };
    renderWithProviders(<SchedulesCard />);
    fireEvent.click(screen.getByTestId("schedule-edit-7"));
    expect(screen.getByTestId("schedule-editor-modal")).toBeInTheDocument();
  });

  it("toasts success on pause then refetch via onSuccess callback", async () => {
    reset();
    const { toast } = await import("sonner");
    schedulesState.data = {
      schedules: [makeSched({ id: 1, enabled: true })],
      count: 1,
    };
    mutationFns.pause = vi.fn(
      (
        _id: number,
        opts?: { onSuccess?: () => void; onError?: (e: Error) => void },
      ) => {
        opts?.onSuccess?.();
      },
    );
    renderWithProviders(<SchedulesCard />);
    fireEvent.click(screen.getByTestId("schedule-toggle-1"));
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining("scan-completed-downloads"),
    );
  });

  it("toasts error on pause failure via onError callback", async () => {
    reset();
    const { toast } = await import("sonner");
    schedulesState.data = {
      schedules: [makeSched({ id: 1, enabled: true })],
      count: 1,
    };
    mutationFns.pause = vi.fn(
      (
        _id: number,
        opts?: { onSuccess?: () => void; onError?: (e: Error) => void },
      ) => {
        opts?.onError?.(new Error("boom"));
      },
    );
    renderWithProviders(<SchedulesCard />);
    fireEvent.click(screen.getByTestId("schedule-toggle-1"));
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("boom"),
    );
  });

  it("toasts success on resume via onSuccess callback", async () => {
    reset();
    const { toast } = await import("sonner");
    schedulesState.data = {
      schedules: [makeSched({ id: 1, enabled: false })],
      count: 1,
    };
    mutationFns.resume = vi.fn(
      (
        _id: number,
        opts?: { onSuccess?: () => void; onError?: (e: Error) => void },
      ) => {
        opts?.onSuccess?.();
      },
    );
    renderWithProviders(<SchedulesCard />);
    fireEvent.click(screen.getByTestId("schedule-toggle-1"));
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining("scan-completed-downloads"),
    );
  });

  it("toasts error on resume failure via onError callback", async () => {
    reset();
    const { toast } = await import("sonner");
    schedulesState.data = {
      schedules: [makeSched({ id: 1, enabled: false })],
      count: 1,
    };
    mutationFns.resume = vi.fn(
      (
        _id: number,
        opts?: { onSuccess?: () => void; onError?: (e: Error) => void },
      ) => {
        opts?.onError?.(new Error("nope"));
      },
    );
    renderWithProviders(<SchedulesCard />);
    fireEvent.click(screen.getByTestId("schedule-toggle-1"));
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("nope"));
  });

  it("toasts success on remove + invokes onSuccess callback", async () => {
    reset();
    const { toast } = await import("sonner");
    schedulesState.data = {
      schedules: [makeSched({ id: 1 })],
      count: 1,
    };
    mutationFns.remove = vi.fn(
      (
        _id: number,
        opts?: { onSuccess?: () => void; onError?: (e: Error) => void },
      ) => {
        opts?.onSuccess?.();
      },
    );
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    try {
      renderWithProviders(<SchedulesCard />);
      fireEvent.click(screen.getByTestId("schedule-remove-1"));
      expect(toast.success).toHaveBeenCalledWith(
        expect.stringContaining("scan-completed-downloads"),
      );
    } finally {
      confirmSpy.mockRestore();
    }
  });

  it("toasts error on remove failure via onError callback", async () => {
    reset();
    const { toast } = await import("sonner");
    schedulesState.data = {
      schedules: [makeSched({ id: 1 })],
      count: 1,
    };
    mutationFns.remove = vi.fn(
      (
        _id: number,
        opts?: { onSuccess?: () => void; onError?: (e: Error) => void },
      ) => {
        opts?.onError?.(new Error("rm-failed"));
      },
    );
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    try {
      renderWithProviders(<SchedulesCard />);
      fireEvent.click(screen.getByTestId("schedule-remove-1"));
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining("rm-failed"),
      );
    } finally {
      confirmSpy.mockRestore();
    }
  });

  it("formats interval seconds into a human-friendly cadence", () => {
    reset();
    schedulesState.data = {
      schedules: [
        makeSched({ id: 1, interval_seconds: 60, label: "minute" }),
        makeSched({ id: 2, interval_seconds: 3600, label: "hour" }),
        makeSched({ id: 3, interval_seconds: 86400, label: "day" }),
        makeSched({ id: 4, interval_seconds: 30, label: "sub-minute" }),
      ],
      count: 4,
    };
    renderWithProviders(<SchedulesCard />);
    expect(screen.getByTestId("schedule-row-1")).toHaveTextContent(/1m/);
    expect(screen.getByTestId("schedule-row-2")).toHaveTextContent(/1h/);
    expect(screen.getByTestId("schedule-row-3")).toHaveTextContent(/1d/);
    expect(screen.getByTestId("schedule-row-4")).toHaveTextContent(/30s/);
  });
});
