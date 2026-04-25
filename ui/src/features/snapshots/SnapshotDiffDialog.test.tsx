import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const contentByFile = vi.hoisted(() => new Map<string, unknown>());
const diffData = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", () => ({
  useSnapshotContent: (file: string | undefined) => {
    if (!file) return { data: undefined, isLoading: false, error: null };
    return {
      data: contentByFile.get(file),
      isLoading: false,
      error: null,
    };
  },
  useSnapshotDiff: () => diffData,
}));

import { SnapshotDiffDialog } from "./SnapshotDiffDialog";

describe("SnapshotDiffDialog", () => {
  beforeEach(() => {
    contentByFile.clear();
    diffData.data = undefined;
    diffData.isLoading = false;
    diffData.error = null;
  });
  afterEach(() => {
    contentByFile.clear();
  });

  const snapshots = [
    { file: "A.json", size: 1, created: "t1" },
    { file: "B.json", size: 1, created: "t2" },
  ];

  it("does not render when closed", () => {
    renderWithProviders(
      <SnapshotDiffDialog
        open={false}
        onOpenChange={vi.fn()}
        snapshots={snapshots}
      />,
    );
    expect(screen.queryByTestId("snapshot-diff-dialog")).toBeNull();
  });

  it("renders the dialog with both select dropdowns when open", async () => {
    renderWithProviders(
      <SnapshotDiffDialog
        open
        onOpenChange={vi.fn()}
        snapshots={snapshots}
      />,
    );
    expect(
      await screen.findByTestId("snapshot-diff-dialog"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("snapshot-diff-a-select")).toBeInTheDocument();
    expect(screen.getByTestId("snapshot-diff-b-select")).toBeInTheDocument();
  });

  it("renders +/- prefixed unified diff when both snapshots resolve", async () => {
    contentByFile.set("A.json", {
      file: "A.json",
      snapshot: { "x.cfg": "alpha\nbeta\ngamma" },
    });
    contentByFile.set("B.json", {
      file: "B.json",
      snapshot: { "x.cfg": "alpha\nbeta-CHANGED\ngamma" },
    });
    renderWithProviders(
      <SnapshotDiffDialog
        open
        onOpenChange={vi.fn()}
        snapshots={snapshots}
        initialA="A.json"
        initialB="B.json"
      />,
    );
    const body = await screen.findByTestId("snapshot-diff-body");
    expect(body.textContent).toContain("- beta");
    expect(body.textContent).toContain("+ beta-CHANGED");
  });

  it("shows the per-file summary badges when the diff endpoint resolves", async () => {
    diffData.data = {
      diffs: [
        { file: "sonarr/config.xml", status: "changed" },
        { file: "tautulli/config.ini", status: "added" },
      ],
      file_a: "A.json",
      file_b: "B.json",
      total_changes: 2,
    };
    renderWithProviders(
      <SnapshotDiffDialog
        open
        onOpenChange={vi.fn()}
        snapshots={snapshots}
        initialA="A.json"
        initialB="B.json"
      />,
    );
    const summary = await screen.findByTestId("snapshot-diff-summary");
    expect(summary).toBeInTheDocument();
    expect(summary.textContent).toMatch(/sonarr\/config\.xml/);
    expect(summary.textContent).toMatch(/tautulli\/config\.ini/);
  });
});
