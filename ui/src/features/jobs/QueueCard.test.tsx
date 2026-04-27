import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const queueState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const mutationFns = vi.hoisted(() => ({
  remove: vi.fn(),
  reorder: vi.fn(),
}));

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useJobQueue: () => ({
      data: queueState.data,
      isLoading: queueState.isLoading,
      error: queueState.error,
    }),
    useRemoveQueueEntry: () => ({
      mutate: mutationFns.remove,
      isPending: false,
    }),
    useReorderQueueEntry: () => ({
      mutate: mutationFns.reorder,
      isPending: false,
    }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { QueueCard } from "./QueueCard";
import type { QueueEntryShape } from "./hooks";

function makeEntry(
  overrides: Partial<QueueEntryShape> = {},
): QueueEntryShape {
  return {
    id: 1,
    job_name: "refresh-iptv-channels",
    source: "manual",
    scheduled_at: 0,
    enqueued_at: 1_700_000_000,
    label: "refresh IPTV channels",
    ...overrides,
  };
}

function reset() {
  queueState.data = undefined;
  queueState.isLoading = false;
  queueState.error = null;
  mutationFns.remove = vi.fn();
  mutationFns.reorder = vi.fn();
}

describe("QueueCard", () => {
  it("renders skeletons while loading", () => {
    reset();
    queueState.isLoading = true;
    renderWithProviders(<QueueCard />);
    expect(screen.getByTestId("queue-card-loading")).toBeInTheDocument();
  });

  it("renders nothing when the queue is empty", () => {
    reset();
    queueState.data = { queue: [], count: 0 };
    renderWithProviders(<QueueCard />);
    expect(screen.queryByTestId("queue-card")).toBeNull();
    expect(screen.queryByTestId("queue-card-loading")).toBeNull();
  });

  it("renders one row per queued entry with its position number", () => {
    reset();
    queueState.data = {
      queue: [
        makeEntry({ id: 1, label: "first" }),
        makeEntry({ id: 2, label: "second" }),
      ],
      count: 2,
    };
    renderWithProviders(<QueueCard />);
    expect(screen.getByTestId("queue-row-1")).toHaveAttribute(
      "data-position",
      "0",
    );
    expect(screen.getByTestId("queue-row-2")).toHaveAttribute(
      "data-position",
      "1",
    );
    expect(screen.getByTestId("queue-row-1")).toHaveTextContent("#1");
    expect(screen.getByTestId("queue-row-2")).toHaveTextContent("#2");
  });

  it("renders the count badge with the entry total", () => {
    reset();
    queueState.data = {
      queue: [makeEntry({ id: 1 }), makeEntry({ id: 2 }), makeEntry({ id: 3 })],
      count: 3,
    };
    renderWithProviders(<QueueCard />);
    expect(screen.getByTestId("queue-count")).toHaveTextContent("3");
  });

  it("disables the up button on the head row and the down button on the tail row", () => {
    reset();
    queueState.data = {
      queue: [makeEntry({ id: 1 }), makeEntry({ id: 2 })],
      count: 2,
    };
    renderWithProviders(<QueueCard />);
    expect(screen.getByTestId("queue-up-1")).toBeDisabled();
    expect(screen.getByTestId("queue-down-1")).not.toBeDisabled();
    expect(screen.getByTestId("queue-up-2")).not.toBeDisabled();
    expect(screen.getByTestId("queue-down-2")).toBeDisabled();
  });

  it("calls reorder up when the up button is clicked", () => {
    reset();
    queueState.data = {
      queue: [makeEntry({ id: 1 }), makeEntry({ id: 2 })],
      count: 2,
    };
    renderWithProviders(<QueueCard />);
    fireEvent.click(screen.getByTestId("queue-up-2"));
    expect(mutationFns.reorder).toHaveBeenCalledWith(
      { entry_id: 2, direction: "up" },
      expect.any(Object),
    );
  });

  it("calls reorder down when the down button is clicked", () => {
    reset();
    queueState.data = {
      queue: [makeEntry({ id: 1 }), makeEntry({ id: 2 })],
      count: 2,
    };
    renderWithProviders(<QueueCard />);
    fireEvent.click(screen.getByTestId("queue-down-1"));
    expect(mutationFns.reorder).toHaveBeenCalledWith(
      { entry_id: 1, direction: "down" },
      expect.any(Object),
    );
  });

  it("calls remove when the trash button is clicked", () => {
    reset();
    queueState.data = {
      queue: [makeEntry({ id: 7 })],
      count: 1,
    };
    renderWithProviders(<QueueCard />);
    fireEvent.click(screen.getByTestId("queue-remove-7"));
    expect(mutationFns.remove).toHaveBeenCalledWith(7, expect.any(Object));
  });

  it("toasts success on remove via onSuccess callback", async () => {
    reset();
    const { toast } = await import("sonner");
    queueState.data = {
      queue: [makeEntry({ id: 7, job_name: "refresh-iptv-channels" })],
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
    renderWithProviders(<QueueCard />);
    fireEvent.click(screen.getByTestId("queue-remove-7"));
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining("refresh-iptv-channels"),
    );
  });

  it("toasts error on remove failure via onError callback", async () => {
    reset();
    const { toast } = await import("sonner");
    queueState.data = {
      queue: [makeEntry({ id: 7 })],
      count: 1,
    };
    mutationFns.remove = vi.fn(
      (
        _id: number,
        opts?: { onSuccess?: () => void; onError?: (e: Error) => void },
      ) => {
        opts?.onError?.(new Error("nope"));
      },
    );
    renderWithProviders(<QueueCard />);
    fireEvent.click(screen.getByTestId("queue-remove-7"));
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("nope"));
  });

  it("toasts error on reorder failure via onError callback", async () => {
    reset();
    const { toast } = await import("sonner");
    queueState.data = {
      queue: [makeEntry({ id: 1 }), makeEntry({ id: 2 })],
      count: 2,
    };
    mutationFns.reorder = vi.fn(
      (
        _input: unknown,
        opts?: { onSuccess?: () => void; onError?: (e: Error) => void },
      ) => {
        opts?.onError?.(new Error("flap"));
      },
    );
    renderWithProviders(<QueueCard />);
    fireEvent.click(screen.getByTestId("queue-down-1"));
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("flap"));
  });
});
