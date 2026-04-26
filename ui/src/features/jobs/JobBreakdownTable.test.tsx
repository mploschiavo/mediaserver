import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import {
  JobBreakdownTable,
  type JobBreakdownRow,
} from "./JobBreakdownTable";

function makeRows(): JobBreakdownRow[] {
  return [
    {
      name: "scan-completed-downloads",
      service: "qbittorrent",
      status: "error",
      elapsed: 0.4,
      error: "boom",
    },
    {
      name: "discover-api-keys",
      service: undefined,
      status: "ok",
      elapsed: 0.1,
      error: undefined,
    },
  ];
}

describe("JobBreakdownTable", () => {
  it("renders one row per provided breakdown entry", () => {
    renderWithProviders(<JobBreakdownTable rows={makeRows()} />);
    expect(
      screen.getByTestId(
        "job-history-breakdown-row-scan-completed-downloads",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("job-history-breakdown-row-discover-api-keys"),
    ).toBeInTheDocument();
  });

  it("surfaces the per-job error message inline with the row", () => {
    renderWithProviders(<JobBreakdownTable rows={makeRows()} />);
    expect(screen.getByText("boom")).toBeInTheDocument();
  });

  it("renders the service badge only when the row has a service", () => {
    renderWithProviders(<JobBreakdownTable rows={makeRows()} />);
    expect(
      screen.getByTestId(
        "job-history-breakdown-service-scan-completed-downloads",
      ),
    ).toHaveTextContent("qbittorrent");
    expect(
      screen.queryByTestId(
        "job-history-breakdown-service-discover-api-keys",
      ),
    ).toBeNull();
  });
});
