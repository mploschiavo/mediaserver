import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";
import type { LogSource } from "@/api/shapes";
import { LogsToolbar } from "./LogsToolbar";
import { LEVELS, type LevelTag } from "./hooks";

type ToolbarProps = ComponentProps<typeof LogsToolbar>;

function harness(overrides: Partial<ToolbarProps> = {}): ToolbarProps {
  const defaults: ToolbarProps = {
    sources: ["controller"] as LogSource[],
    onSourcesChange: vi.fn(),
    tailing: true,
    onTailingChange: vi.fn(),
    search: "",
    onSearchChange: vi.fn(),
    enabledLevels: new Set(LEVELS) as ReadonlySet<LevelTag>,
    onToggleLevel: vi.fn(),
    onExport: vi.fn(),
    // v1.0.270 Logs Phase 1 controls — these are required props now;
    // tests pass sane defaults that exercise the same UX as before.
    limit: 100,
    onLimitChange: vi.fn(),
    limitOptions: [100, 500, 1000, 5000, 10000, 50000],
    since: "",
    onSinceChange: vi.fn(),
    sinceOptions: [
      { value: "", label: "All available" },
      { value: "5m", label: "Last 5 min" },
    ],
    actionFilter: "",
    onActionFilterChange: vi.fn(),
  };
  return { ...defaults, ...overrides };
}

describe("LogsToolbar", () => {
  it("renders one chip per supported source with the selected ones aria-checked", () => {
    const props = harness();
    renderWithProviders(<LogsToolbar {...props} />);
    expect(
      screen.getByTestId("logs-source-chip-controller"),
    ).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("logs-source-chip-sonarr")).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("toggles a source when the chip is clicked", async () => {
    const onSourcesChange = vi.fn();
    const props = harness({ onSourcesChange });
    renderWithProviders(<LogsToolbar {...props} />);
    await userEvent.click(screen.getByTestId("logs-source-chip-sonarr"));
    expect(onSourcesChange).toHaveBeenCalledTimes(1);
    const next = onSourcesChange.mock.calls[0]?.[0] as readonly string[];
    expect(next).toContain("controller");
    expect(next).toContain("sonarr");
  });

  it("renders the tail/pause button reflecting the current state", () => {
    renderWithProviders(<LogsToolbar {...harness({ tailing: true })} />);
    expect(screen.getByTestId("logs-tail-toggle")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByTestId("logs-tail-toggle")).toHaveTextContent(/pause/i);
  });

  it("flips tail/pause on click", async () => {
    const onTailingChange = vi.fn();
    renderWithProviders(
      <LogsToolbar {...harness({ tailing: true, onTailingChange })} />,
    );
    await userEvent.click(screen.getByTestId("logs-tail-toggle"));
    expect(onTailingChange).toHaveBeenCalledWith(false);
  });

  it("calls onToggleLevel when a level chip is clicked", async () => {
    const onToggleLevel = vi.fn();
    renderWithProviders(<LogsToolbar {...harness({ onToggleLevel })} />);
    await userEvent.click(screen.getByTestId("logs-level-chip-ERR"));
    expect(onToggleLevel).toHaveBeenCalledWith("[ERR]");
  });

  it("disables the export button when there's nothing to export", () => {
    renderWithProviders(
      <LogsToolbar {...harness({ exportDisabled: true })} />,
    );
    expect(screen.getByTestId("logs-export")).toBeDisabled();
  });

  it("forwards search input changes to onSearchChange", async () => {
    const onSearchChange = vi.fn();
    renderWithProviders(<LogsToolbar {...harness({ onSearchChange })} />);
    await userEvent.type(screen.getByTestId("logs-search"), "boot");
    // Each char fires once.
    expect(onSearchChange).toHaveBeenLastCalledWith("boot");
  });
});
