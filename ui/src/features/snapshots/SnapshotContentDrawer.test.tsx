import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const contentState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("./hooks", () => ({
  useSnapshotContent: () => contentState,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { SnapshotContentDrawer } from "./SnapshotContentDrawer";

describe("SnapshotContentDrawer", () => {
  beforeEach(() => {
    contentState.data = undefined;
    contentState.isLoading = false;
    contentState.error = null;
  });
  afterEach(() => {
    contentState.data = undefined;
  });

  it("does not render the drawer when filename is null", () => {
    renderWithProviders(
      <SnapshotContentDrawer filename={null} onOpenChange={vi.fn()} />,
    );
    expect(screen.queryByTestId("snapshot-content-drawer")).toBeNull();
  });

  it("renders the drawer + filename heading when filename is set", async () => {
    contentState.data = {
      file: "snapshot-X.json",
      snapshot: { "sonarr/config.xml": "<Config>...</Config>" },
    };
    renderWithProviders(
      <SnapshotContentDrawer
        filename="snapshot-X.json"
        onOpenChange={vi.fn()}
      />,
    );
    expect(
      await screen.findByTestId("snapshot-content-drawer"),
    ).toBeInTheDocument();
    expect(screen.getByText("snapshot-X.json")).toBeInTheDocument();
  });

  it("renders skeletons while content is loading", async () => {
    contentState.isLoading = true;
    renderWithProviders(
      <SnapshotContentDrawer
        filename="snapshot-X.json"
        onOpenChange={vi.fn()}
      />,
    );
    expect(
      await screen.findByTestId("snapshot-content-loading"),
    ).toBeInTheDocument();
  });

  it("renders the snapshot body as monospaced text", async () => {
    contentState.data = {
      file: "snapshot-X.json",
      snapshot: {
        "sonarr/config.xml": "<Config>SONARR</Config>",
        "radarr/config.xml": "<Config>RADARR</Config>",
      },
    };
    renderWithProviders(
      <SnapshotContentDrawer
        filename="snapshot-X.json"
        onOpenChange={vi.fn()}
      />,
    );
    const pre = await screen.findByTestId("snapshot-content-pre");
    expect(pre.textContent).toContain("sonarr/config.xml");
    expect(pre.textContent).toContain("SONARR");
    expect(pre.textContent).toContain("radarr/config.xml");
  });
});
