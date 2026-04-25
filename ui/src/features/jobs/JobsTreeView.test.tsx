import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { JobsTreeView } from "./JobsTreeView";
import type { JobHistoryEntry, JobMeta, JobTreeNode } from "./hooks";

function makeTree(): readonly JobTreeNode[] {
  return [
    {
      name: "bootstrap",
      sub_jobs: [
        {
          name: "configure-pre-bootstrap",
          sub_jobs: [
            { name: "seed-runtime-overrides", sub_jobs: [] },
            { name: "discover-api-keys", sub_jobs: [] },
          ],
        },
        {
          name: "configure-media-server",
          sub_jobs: [],
        },
      ],
    },
  ];
}

function makeCatalog(): ReadonlyMap<string, JobMeta> {
  return new Map<string, JobMeta>([
    ["bootstrap", { name: "bootstrap", label: "Bootstrap" }],
    [
      "configure-pre-bootstrap",
      {
        name: "configure-pre-bootstrap",
        label: "Pre-bootstrap config",
        service: "controller",
      },
    ],
    [
      "seed-runtime-overrides",
      { name: "seed-runtime-overrides", service: "controller" },
    ],
    ["discover-api-keys", { name: "discover-api-keys" }],
    [
      "configure-media-server",
      { name: "configure-media-server", service: "jellyfin" },
    ],
  ]);
}

function makeLatest(): JobHistoryEntry {
  return {
    ts: 1_700_000_000,
    elapsed: 0.5,
    ok: 1,
    skipped: 1,
    errors: 0,
    jobs: {
      "configure-media-server": { status: "skipped", elapsed: 0 },
      "discover-api-keys": { status: "ok", elapsed: 0.1 },
    },
  };
}

describe("JobsTreeView", () => {
  it("renders a 3-deep tree with the root row expanded by default", () => {
    const onSelect = vi.fn();
    renderWithProviders(
      <JobsTreeView
        tree={makeTree()}
        catalog={makeCatalog()}
        latest={makeLatest()}
        selectedName={null}
        onSelect={onSelect}
      />,
    );
    // Root visible.
    expect(screen.getByTestId("jobs-tree-row-bootstrap")).toBeInTheDocument();
    // First-level children visible (root expanded by default).
    expect(
      screen.getByTestId("jobs-tree-row-configure-pre-bootstrap"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("jobs-tree-row-configure-media-server"),
    ).toBeInTheDocument();
    // Deeper children NOT visible until we expand.
    expect(
      screen.queryByTestId("jobs-tree-row-seed-runtime-overrides"),
    ).toBeNull();
  });

  it("expands a node when its chevron is clicked", () => {
    renderWithProviders(
      <JobsTreeView
        tree={makeTree()}
        catalog={makeCatalog()}
        selectedName={null}
        onSelect={vi.fn()}
      />,
    );
    const chevron = screen.getByTestId(
      "jobs-tree-chevron-configure-pre-bootstrap",
    );
    fireEvent.click(chevron);
    expect(
      screen.getByTestId("jobs-tree-row-seed-runtime-overrides"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("jobs-tree-row-discover-api-keys"),
    ).toBeInTheDocument();
  });

  it("calls onSelect with the leaf job and its catalog meta", () => {
    const onSelect = vi.fn();
    renderWithProviders(
      <JobsTreeView
        tree={makeTree()}
        catalog={makeCatalog()}
        selectedName={null}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(
      screen.getByTestId("jobs-tree-chevron-configure-pre-bootstrap"),
    );
    fireEvent.click(screen.getByTestId("jobs-tree-name-discover-api-keys"));
    expect(onSelect).toHaveBeenCalledTimes(1);
    const [name, meta] = onSelect.mock.calls[0] ?? [];
    expect(name).toBe("discover-api-keys");
    expect(meta).toMatchObject({ name: "discover-api-keys" });
  });

  it("paints an amber dot for jobs skipped in the latest history entry", () => {
    renderWithProviders(
      <JobsTreeView
        tree={makeTree()}
        catalog={makeCatalog()}
        latest={makeLatest()}
        selectedName={null}
        onSelect={vi.fn()}
      />,
    );
    const dot = screen.getByTestId(
      "jobs-tree-dot-configure-media-server",
    );
    expect(dot.getAttribute("data-status")).toBe("skipped");
  });

  it("renders an empty state when the tree is empty", () => {
    renderWithProviders(
      <JobsTreeView
        tree={[]}
        catalog={makeCatalog()}
        selectedName={null}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByTestId("jobs-tree-empty")).toBeInTheDocument();
  });

  it("appends a truncated error snippet for errored leaves", () => {
    const tree: readonly JobTreeNode[] = [
      { name: "configure-media-server", sub_jobs: [] },
    ];
    const latest: JobHistoryEntry = {
      ts: 1_700_000_000,
      jobs: {
        "configure-media-server": {
          status: "error",
          error: "Connection refused — jellyfin pod not ready",
        },
      },
    };
    renderWithProviders(
      <JobsTreeView
        tree={tree}
        catalog={makeCatalog()}
        latest={latest}
        selectedName={null}
        onSelect={vi.fn()}
      />,
    );
    const snip = screen.getByTestId("jobs-tree-error-configure-media-server");
    expect(snip).toBeInTheDocument();
    expect(snip).toHaveTextContent(/Connection refused/);
    // title attribute carries the full text.
    expect(snip.getAttribute("title")).toBe(
      "Connection refused — jellyfin pod not ready",
    );
  });

  it("renders a `(skipped)` hint with explainer tooltip for skipped leaves", () => {
    renderWithProviders(
      <JobsTreeView
        tree={makeTree()}
        catalog={makeCatalog()}
        latest={makeLatest()}
        selectedName={null}
        onSelect={vi.fn()}
      />,
    );
    const skip = screen.getByTestId(
      "jobs-tree-skipped-configure-media-server",
    );
    expect(skip).toHaveTextContent(/skipped/);
    expect(skip.getAttribute("title")).toMatch(/dependency/i);
  });

  it("paints a `running…` badge on the leaf matching inFlightName", () => {
    renderWithProviders(
      <JobsTreeView
        tree={makeTree()}
        catalog={makeCatalog()}
        latest={makeLatest()}
        selectedName={null}
        onSelect={vi.fn()}
        inFlightName="configure-media-server"
      />,
    );
    expect(
      screen.getByTestId("jobs-tree-running-configure-media-server"),
    ).toBeInTheDocument();
    // Only the in-flight leaf shows the badge.
    expect(
      screen.queryByTestId("jobs-tree-running-discover-api-keys"),
    ).toBeNull();
  });

  it("renders the dim service tag inline for catalog leaves with a service", () => {
    const tree: readonly JobTreeNode[] = [
      { name: "configure-media-server", sub_jobs: [] },
    ];
    renderWithProviders(
      <JobsTreeView
        tree={tree}
        catalog={makeCatalog()}
        selectedName={null}
        onSelect={vi.fn()}
      />,
    );
    expect(
      screen.getByTestId("jobs-tree-service-configure-media-server"),
    ).toHaveTextContent("jellyfin");
  });
});
