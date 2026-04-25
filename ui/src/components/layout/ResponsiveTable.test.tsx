import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "./ResponsiveTable";

interface Row {
  id: string;
  name: string;
  qty: number;
}

const rows: Row[] = [
  { id: "a", name: "Apple", qty: 1 },
  { id: "b", name: "Banana", qty: 2 },
  { id: "c", name: "Cherry", qty: 3 },
];

const columns: ResponsiveTableColumn<Row>[] = [
  { id: "name", header: "Name", cell: (row) => row.name },
  { id: "qty", header: "Qty", cell: (row) => String(row.qty) },
];

const renderCard = (row: Row) => (
  <div data-testid={`card-${row.id}`}>
    <div>{row.name}</div>
    <div>{row.qty}</div>
  </div>
);

describe("ResponsiveTable", () => {
  it("renders both desktop table and mobile card list (responsive toggle is CSS-driven)", () => {
    render(
      <ResponsiveTable
        rows={rows}
        rowKey={(row) => row.id}
        columns={columns}
        card={renderCard}
      />,
    );
    expect(screen.getByTestId("responsive-table-desktop")).toBeInTheDocument();
    expect(screen.getByTestId("responsive-table-mobile")).toBeInTheDocument();
  });

  it("renders one row per item in the desktop table", () => {
    render(
      <ResponsiveTable
        rows={rows}
        rowKey={(row) => row.id}
        columns={columns}
        card={renderCard}
      />,
    );
    const desktop = screen.getByTestId("responsive-table-desktop");
    const dataRows = within(desktop).getAllByRole("row");
    // Header row + 3 data rows
    expect(dataRows.length).toBe(rows.length + 1);
    expect(within(desktop).getByText("Apple")).toBeInTheDocument();
    expect(within(desktop).getByText("Cherry")).toBeInTheDocument();
  });

  it("renders one card per row on mobile", () => {
    render(
      <ResponsiveTable
        rows={rows}
        rowKey={(row) => row.id}
        columns={columns}
        card={renderCard}
      />,
    );
    const mobile = screen.getByTestId("responsive-table-mobile");
    expect(within(mobile).getAllByRole("listitem")).toHaveLength(rows.length);
    for (const row of rows) {
      expect(
        within(mobile).getByTestId(`card-${row.id}`),
      ).toBeInTheDocument();
    }
  });

  it("mobile and desktop share the same row data (no drift)", () => {
    render(
      <ResponsiveTable
        rows={rows}
        rowKey={(row) => row.id}
        columns={columns}
        card={renderCard}
      />,
    );
    for (const row of rows) {
      const desktop = screen.getByTestId("responsive-table-desktop");
      const mobile = screen.getByTestId("responsive-table-mobile");
      expect(within(desktop).getByText(row.name)).toBeInTheDocument();
      expect(within(mobile).getByText(row.name)).toBeInTheDocument();
    }
  });

  it("renders an empty state when rows is empty", () => {
    render(
      <ResponsiveTable
        rows={[]}
        rowKey={(row: Row) => row.id}
        columns={columns}
        card={renderCard}
      />,
    );
    expect(screen.getByTestId("responsive-table-empty")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /nothing to show/i }),
    ).toBeInTheDocument();
  });

  it("uses the supplied empty override when provided", () => {
    render(
      <ResponsiveTable
        rows={[]}
        rowKey={(row: Row) => row.id}
        columns={columns}
        card={renderCard}
        empty={<div data-testid="custom-empty">No rows yet.</div>}
      />,
    );
    expect(screen.getByTestId("custom-empty")).toBeInTheDocument();
  });

  it("uses hidden md:block on the desktop tree and block md:hidden on mobile", () => {
    render(
      <ResponsiveTable
        rows={rows}
        rowKey={(row) => row.id}
        columns={columns}
        card={renderCard}
      />,
    );
    const desktop = screen.getByTestId("responsive-table-desktop");
    const mobile = screen.getByTestId("responsive-table-mobile");
    expect(desktop.className).toContain("hidden");
    expect(desktop.className).toContain("md:block");
    expect(mobile.className).toContain("md:hidden");
  });
});
