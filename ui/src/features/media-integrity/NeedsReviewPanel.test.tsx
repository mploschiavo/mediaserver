import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";
import { NeedsReviewPanel } from "./NeedsReviewPanel";
import type { MediaIntegrityStatusShape } from "@/api";

const mutate = vi.hoisted(() => vi.fn());
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    useResolveReview: () => ({
      mutate,
      isPending: false,
      data: undefined,
      error: null,
    }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

function makeStatus(
  needsReview: Array<{
    release_id: string;
    title: string;
    candidates: Array<{ file_id: string; size: number }>;
  }>,
): MediaIntegrityStatusShape {
  return {
    last_enforce: { ts: "", detail: {} },
    last_reconcile: {
      ts: new Date().toISOString(),
      detail: {
        servarr: {
          results: {
            radarr: {
              total_needs_review: needsReview.length,
              needs_review: needsReview,
            },
          },
        },
      },
    },
    policy_version: 1,
    servarr_adapters: ["radarr"],
    bazarr_present: false,
    missing_api_keys: [],
  };
}

describe("NeedsReviewPanel", () => {
  beforeEach(() => {
    mutate.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
  });
  afterEach(() => {
    mutate.mockReset();
  });

  it("renders nothing when status is undefined", () => {
    renderWithProviders(<NeedsReviewPanel />);
    expect(screen.queryByTestId("needs-review-panel")).toBeNull();
  });

  it("renders nothing when there are no review items", () => {
    renderWithProviders(<NeedsReviewPanel status={makeStatus([])} />);
    expect(screen.queryByTestId("needs-review-panel")).toBeNull();
  });

  it("renders a row per release when items are present", () => {
    renderWithProviders(
      <NeedsReviewPanel
        status={makeStatus([
          {
            release_id: "rel-1",
            title: "The Matrix",
            candidates: [
              { file_id: "f1", size: 1024 * 1024 * 200 },
              { file_id: "f2", size: 1024 * 1024 * 350 },
            ],
          },
        ])}
      />,
    );
    expect(screen.getByTestId("needs-review-panel")).toBeInTheDocument();
    expect(screen.getByText("The Matrix")).toBeInTheDocument();
    expect(screen.getByTestId("keep-rel-1-f1")).toBeInTheDocument();
    expect(screen.getByTestId("keep-rel-1-f2")).toBeInTheDocument();
  });

  it("calls the resolve mutation with the right body when 'Keep' is clicked", async () => {
    renderWithProviders(
      <NeedsReviewPanel
        status={makeStatus([
          {
            release_id: "rel-1",
            title: "The Matrix",
            candidates: [{ file_id: "winner-id", size: 1024 }],
          },
        ])}
      />,
    );
    await userEvent.click(screen.getByTestId("keep-rel-1-winner-id"));
    expect(mutate).toHaveBeenCalledTimes(1);
    const call = mutate.mock.calls[0]?.[0] as {
      body: { app: string; release_id: string; winner_file_id: string };
    };
    expect(call.body).toMatchObject({
      app: "radarr",
      release_id: "rel-1",
      winner_file_id: "winner-id",
    });
  });

  it("toasts success with the deleted-others count on resolve", async () => {
    mutate.mockImplementation(
      (
        _vars: { body: unknown },
        opts: {
          onSuccess: (out: { deleted_ids: string[] }) => void;
        },
      ) => {
        opts.onSuccess({ deleted_ids: ["a", "b"] });
      },
    );
    renderWithProviders(
      <NeedsReviewPanel
        status={makeStatus([
          {
            release_id: "rel-1",
            title: "Foo",
            candidates: [{ file_id: "winner", size: 1 }],
          },
        ])}
      />,
    );
    await userEvent.click(screen.getByTestId("keep-rel-1-winner"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(toastSuccess.mock.calls[0]?.[0]).toMatch(/deleted 2/);
  });

  it("toasts the error message on resolve failure", async () => {
    mutate.mockImplementation(
      (
        _vars: { body: unknown },
        opts: { onError: (err: Error) => void },
      ) => {
        opts.onError(new Error("nope"));
      },
    );
    renderWithProviders(
      <NeedsReviewPanel
        status={makeStatus([
          {
            release_id: "rel-1",
            title: "Foo",
            candidates: [{ file_id: "w", size: 1 }],
          },
        ])}
      />,
    );
    await userEvent.click(screen.getByTestId("keep-rel-1-w"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("nope"));
  });

  it("renders a header that pluralizes correctly", () => {
    renderWithProviders(
      <NeedsReviewPanel
        status={makeStatus([
          {
            release_id: "r1",
            title: "A",
            candidates: [{ file_id: "x", size: 1 }],
          },
          {
            release_id: "r2",
            title: "B",
            candidates: [{ file_id: "y", size: 1 }],
          },
        ])}
      />,
    );
    expect(screen.getByText(/2 releases/)).toBeInTheDocument();
  });
});
