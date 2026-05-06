import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { LogsTable } from "./LogsTable";
import { parseLogLine, type ParsedLine } from "./hooks";
import { hashSource } from "./format";

function lines(): ParsedLine[] {
  // Use bracketed level prefixes — the parser anchors level extraction
  // to a leading `[TOKEN]` (case-insensitive) on the timestamp-stripped
  // body. Free-text "ERROR:" / "WARN" substrings are NOT promoted; see
  // `extractLevelFromBracketedPrefix` for the contract.
  return [
    parseLogLine("[2026-04-07 12:00:01] [INFO] boot ok", "controller", 0),
    parseLogLine("[2026-04-07 12:00:02] [ERROR] boom", "sonarr", 0),
    parseLogLine("[2026-04-07 12:00:03] [WARN] slow", "radarr", 0),
  ];
}

/**
 * After the `<DataTable>` migration, rows are rendered by the primitive
 * with stable testIds of the form `logs-data-table-row-<id>`. We
 * resolve them here by querying for any element whose `data-testid`
 * starts with that prefix — the existing per-row contracts
 * (`data-source`, `data-level`, `data-tone`) are preserved via
 * DataTable's `renderRowAttributes` hook and asserted directly.
 */
function getRows(): HTMLElement[] {
  return Array.from(
    document.querySelectorAll<HTMLElement>(
      '[data-testid^="logs-data-table-row-"]',
    ),
  );
}

describe("LogsTable", () => {
  it("renders one row per line with timestamp, source, level columns", () => {
    renderWithProviders(<LogsTable lines={lines()} search="" tailing={false} />);
    const rows = getRows();
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveAttribute("data-source", "controller");
    expect(rows[1]).toHaveAttribute("data-source", "sonarr");
    expect(rows[2]).toHaveAttribute("data-source", "radarr");
  });

  it("preserves per-row data-level on each migrated row", () => {
    renderWithProviders(<LogsTable lines={lines()} search="" tailing={false} />);
    const rows = getRows();
    expect(rows[0]).toHaveAttribute("data-level", "[INFO]");
    expect(rows[1]).toHaveAttribute("data-level", "[ERR]");
    expect(rows[2]).toHaveAttribute("data-level", "[WARN]");
  });

  it("propagates the hashed source tone to a row-level data-tone attr", () => {
    renderWithProviders(<LogsTable lines={lines()} search="" tailing={false} />);
    const rows = getRows();
    expect(rows[0]).toHaveAttribute("data-tone", hashSource("controller").fg);
    expect(rows[1]).toHaveAttribute("data-tone", hashSource("sonarr").fg);
  });

  it("colors the source cell with the stable hash tone", () => {
    renderWithProviders(<LogsTable lines={lines()} search="" tailing={false} />);
    const controllerCell = screen.getByTestId(
      "logs-source-cell-controller",
    ) as HTMLElement;
    const sonarrCell = screen.getByTestId(
      "logs-source-cell-sonarr",
    ) as HTMLElement;
    // happy-dom strips unrecognised CSS values (oklch()) from both
    // `getAttribute("style")` and `el.style.color`, so we read the
    // mirrored `data-tone` attribute that the component sets verbatim
    // alongside the inline style. Browsers render the inline style;
    // tests verify the contract via the data-attribute.
    const controllerColor = controllerCell.getAttribute("data-tone") ?? "";
    const sonarrColor = sonarrCell.getAttribute("data-tone") ?? "";
    expect(controllerColor).toMatch(/oklch/i);
    expect(sonarrColor).toMatch(/oklch/i);
    // And the two colors must be DIFFERENT — that's the whole point of
    // the per-source hash tone.
    expect(controllerColor).not.toBe(sonarrColor);
  });

  it("source-color hash is stable across calls (same name -> same tone)", () => {
    expect(hashSource("controller").fg).toBe(hashSource("controller").fg);
    expect(hashSource("sonarr").fg).toBe(hashSource("sonarr").fg);
  });

  it("renders a fallback dash for lines without a parseable timestamp", () => {
    const noTs: ParsedLine[] = [parseLogLine("plain message", "controller", 0)];
    renderWithProviders(<LogsTable lines={noTs} search="" tailing={false} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("highlights matched substring segments in the message", () => {
    renderWithProviders(<LogsTable lines={lines()} search="boot" tailing={false} />);
    const hits = screen.getAllByTestId("logs-search-hit");
    expect(hits.length).toBeGreaterThan(0);
    expect(hits[0]?.textContent).toMatch(/boot/i);
  });

  it("highlights regex matches when search starts with /…/", () => {
    renderWithProviders(<LogsTable lines={lines()} search="/ERR.*/" tailing={false} />);
    const hits = screen.getAllByTestId("logs-search-hit");
    expect(hits[0]?.textContent).toMatch(/ERR/);
  });

  it("toggles data-tailing on the scroller when tailing flips", () => {
    const { rerender } = renderWithProviders(
      <LogsTable lines={lines()} search="" tailing={true} />,
    );
    expect(screen.getByTestId("logs-table-scroller")).toHaveAttribute(
      "data-tailing",
      "true",
    );
    rerender(<LogsTable lines={lines()} search="" tailing={false} />);
    expect(screen.getByTestId("logs-table-scroller")).toHaveAttribute(
      "data-tailing",
      "false",
    );
  });

  it("renders an empty body when no lines are provided", () => {
    renderWithProviders(<LogsTable lines={[]} search="" tailing={false} />);
    expect(getRows()).toHaveLength(0);
  });
});
