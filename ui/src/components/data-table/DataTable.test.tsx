import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable, type DataTableProps } from "./DataTable";

interface Row {
  id: string;
  name: string;
  scopes: string;
}

const fixture: Row[] = [
  { id: "r-charlie", name: "charlie", scopes: "read write" },
  { id: "r-alpha", name: "alpha", scopes: "read" },
  { id: "r-bravo", name: "bravo", scopes: "admin" },
];

const baseColumns: ColumnDef<Row>[] = [
  {
    id: "name",
    accessorKey: "name",
    header: "Name",
    meta: { label: "Name" },
  },
  {
    id: "scopes",
    accessorKey: "scopes",
    header: "Scopes",
    meta: { label: "Scopes" },
  },
];

function renderTable(extraProps: Partial<DataTableProps<Row>> = {}) {
  return render(
    <DataTable<Row>
      columns={baseColumns}
      data={fixture}
      getRowId={(row) => row.id}
      {...extraProps}
    />,
  );
}

function rowOrder(): string[] {
  // Read row test ids in DOM order to confirm sort outcome.
  return Array.from(document.querySelectorAll("[data-testid^='data-table-row-']"))
    .map((el) => el.getAttribute("data-testid") ?? "");
}

describe("DataTable", () => {
  it("renders one row per data item with stable test ids", () => {
    renderTable();
    expect(screen.getByTestId("data-table")).toBeInTheDocument();
    expect(screen.getByTestId("data-table-row-r-alpha")).toBeInTheDocument();
    expect(screen.getByTestId("data-table-row-r-bravo")).toBeInTheDocument();
    expect(screen.getByTestId("data-table-row-r-charlie")).toBeInTheDocument();
    // Each header gets a deterministic data-testid.
    expect(screen.getByTestId("data-table-header-name")).toBeInTheDocument();
    expect(screen.getByTestId("data-table-header-scopes")).toBeInTheDocument();
  });

  it("renders the empty state when data is empty", () => {
    renderTable({ data: [], emptyState: "Nothing to show." });
    const empty = screen.getByTestId("data-table-empty");
    expect(empty).toHaveTextContent("Nothing to show.");
    // No row testids at all.
    expect(rowOrder()).toEqual([]);
  });

  it("cycles sort direction asc -> desc -> none on header click", async () => {
    const user = userEvent.setup();
    renderTable();
    // Initial order matches the fixture (insertion order).
    expect(rowOrder()).toEqual([
      "data-table-row-r-charlie",
      "data-table-row-r-alpha",
      "data-table-row-r-bravo",
    ]);

    const nameHeader = screen.getByTestId("data-table-header-name");
    const sortBtn = within(nameHeader).getByRole("button");

    // 1st click -> ascending: alpha, bravo, charlie.
    await user.click(sortBtn);
    expect(nameHeader).toHaveAttribute("data-sort", "asc");
    expect(rowOrder()).toEqual([
      "data-table-row-r-alpha",
      "data-table-row-r-bravo",
      "data-table-row-r-charlie",
    ]);

    // 2nd click -> descending: charlie, bravo, alpha.
    await user.click(sortBtn);
    expect(nameHeader).toHaveAttribute("data-sort", "desc");
    expect(rowOrder()).toEqual([
      "data-table-row-r-charlie",
      "data-table-row-r-bravo",
      "data-table-row-r-alpha",
    ]);

    // 3rd click -> back to insertion order.
    await user.click(sortBtn);
    expect(nameHeader).toHaveAttribute("data-sort", "none");
    expect(rowOrder()).toEqual([
      "data-table-row-r-charlie",
      "data-table-row-r-alpha",
      "data-table-row-r-bravo",
    ]);
  });

  it("filters rows via the per-column filter input", async () => {
    const user = userEvent.setup();
    renderTable();
    const filter = screen.getByTestId("data-table-filter-name");
    await user.type(filter, "alp");
    expect(rowOrder()).toEqual(["data-table-row-r-alpha"]);

    await user.clear(filter);
    expect(rowOrder()).toEqual([
      "data-table-row-r-charlie",
      "data-table-row-r-alpha",
      "data-table-row-r-bravo",
    ]);
  });

  it("hides a column when toggled off in the visibility menu", async () => {
    const user = userEvent.setup();
    renderTable();

    // Scopes column visible up front.
    expect(screen.getByTestId("data-table-header-scopes")).toBeInTheDocument();

    await user.click(screen.getByTestId("data-table-visibility-trigger"));
    const toggle = await screen.findByTestId(
      "data-table-visibility-toggle-scopes",
    );
    await user.click(toggle);

    expect(screen.queryByTestId("data-table-header-scopes")).toBeNull();
    // Toggling back restores visibility.
    await user.click(toggle);
    expect(screen.getByTestId("data-table-header-scopes")).toBeInTheDocument();
  });

  it("invokes onRowClick with the original row data", async () => {
    const user = userEvent.setup();
    const onRowClick = vi.fn();
    renderTable({ onRowClick });
    await user.click(screen.getByTestId("data-table-row-r-bravo"));
    expect(onRowClick).toHaveBeenCalledWith(
      expect.objectContaining({ id: "r-bravo", name: "bravo" }),
    );
  });

  it("namespaces test ids by the testId prop", () => {
    renderTable({ testId: "tokens-table" });
    expect(screen.getByTestId("tokens-table")).toBeInTheDocument();
    expect(screen.getByTestId("tokens-table-row-r-alpha")).toBeInTheDocument();
    expect(screen.getByTestId("tokens-table-header-name")).toBeInTheDocument();
    expect(screen.getByTestId("tokens-table-filter-name")).toBeInTheDocument();
  });
});
