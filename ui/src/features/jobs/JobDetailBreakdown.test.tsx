import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import {
  JobDetailBreakdown,
  type JobDetailBreakdownRow,
} from "./JobDetailBreakdown";

function makeRows(): JobDetailBreakdownRow[] {
  return [
    { ts: 1_700_000_000, status: "ok", elapsed: 1.2, source: "cron" },
    { ts: 1_699_999_000, status: "skipped", elapsed: 0 },
    { ts: 1_699_998_000, status: "error", elapsed: 0.5, source: "manual" },
  ];
}

describe("JobDetailBreakdown", () => {
  it("renders a tbody row per provided run", () => {
    renderWithProviders(<JobDetailBreakdown rows={makeRows()} />);
    const table = screen.getByTestId("job-detail-runs-table");
    expect(table.querySelectorAll("tbody tr")).toHaveLength(3);
  });

  it("renders the source badge when a row has a source", () => {
    renderWithProviders(<JobDetailBreakdown rows={makeRows()} />);
    expect(screen.getByTestId("job-detail-source-cron")).toHaveTextContent(
      "cron",
    );
    expect(screen.getByTestId("job-detail-source-manual")).toHaveTextContent(
      "manual",
    );
  });

  it("renders an empty body when given no rows", () => {
    renderWithProviders(<JobDetailBreakdown rows={[]} />);
    const table = screen.getByTestId("job-detail-runs-table");
    expect(table.querySelectorAll("tbody tr")).toHaveLength(0);
  });
});
