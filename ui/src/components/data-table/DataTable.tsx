import * as React from "react";
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type Column,
  type ColumnDef,
  type ColumnFiltersState,
  type SortingState,
  type VisibilityState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";

/**
 * Props for the generic DataTable primitive. Callers describe their
 * columns via TanStack Table's `ColumnDef<T>` shape and supply a row
 * array; the primitive owns sort, per-column text filter, column
 * visibility and sticky-header state. Server-side data fetching is out
 * of scope — give us the materialised rows.
 *
 * `getRowId` is recommended when rows have a stable identifier so the
 * `data-testid` slugs (e.g. `data-table-row-<id>`) stay stable across
 * sort/filter mutations. When omitted we fall back to the row index.
 */
export interface DataTableProps<TData> {
  columns: ColumnDef<TData, unknown>[];
  /**
   * Row data. Accepted as `readonly TData[]` so callers can pass
   * arrays returned from `asArray(...)` (which are `readonly`) without
   * a copy. We spread internally before handing to TanStack Table,
   * which expects a mutable view.
   */
  data: readonly TData[];
  /** Stable id extractor — when provided, used for row keys + testids. */
  getRowId?: (row: TData, index: number) => string;
  /** Rendered when `data.length === 0`. */
  emptyState?: React.ReactNode;
  /** Optional caption above the toolbar (id, count, etc.). */
  caption?: React.ReactNode;
  /** Optional row-click pass-through. Receives the original row. */
  onRowClick?: (row: TData) => void;
  /** Slug used to namespace `data-testid` values. Defaults to `data-table`. */
  testId?: string;
  className?: string;
  /** Set false to drop the column-visibility menu in the toolbar. */
  enableColumnVisibility?: boolean;
  /** Set false to drop per-column filter inputs in the header row. */
  enableFiltering?: boolean;
  /** Set false to drop sort affordances on column headers. */
  enableSorting?: boolean;
  /**
   * Per-row HTML attribute hook. The returned record is spread onto
   * the rendered `<tr>`. Originally added to unblock the LogsTable
   * migration (which needs `data-source` / `data-level` / `data-tone`
   * attributes per row for the existing CSS + tests). The function
   * receives the original row plus its index. Return `{}` for rows
   * that don't need extra attributes.
   *
   * Restrictions: don't return `key`, `onClick`, `data-testid`, or
   * any Tailwind class hook — those are owned by DataTable itself
   * and a caller-provided override would silently break sort/click/
   * test-id behaviour. The runtime guard below strips them.
   */
  renderRowAttributes?: (row: TData, index: number) => Record<string, string>;
}

const ASC_LABEL = "sorted ascending";
const DESC_LABEL = "sorted descending";
const UNSORTED_LABEL = "unsorted";

function sortIndicator(direction: false | "asc" | "desc"): {
  icon: React.ReactNode;
  label: string;
} {
  if (direction === "asc") {
    return {
      icon: <ArrowUp aria-hidden className="size-3.5" />,
      label: ASC_LABEL,
    };
  }
  if (direction === "desc") {
    return {
      icon: <ArrowDown aria-hidden className="size-3.5" />,
      label: DESC_LABEL,
    };
  }
  return {
    icon: <ArrowUpDown aria-hidden className="size-3.5 opacity-50" />,
    label: UNSORTED_LABEL,
  };
}

/**
 * Read the human-friendly column title from `columnDef.meta.label`,
 * falling back to the column id. Putting the label in `meta` keeps the
 * default header-render free to be a custom React node (e.g. an icon
 * + text) without losing a plain string for the visibility menu and
 * `aria-label`s.
 */
function columnLabel<T>(column: Column<T, unknown>): string {
  const meta = column.columnDef.meta as
    | { label?: string }
    | undefined;
  if (meta?.label) return meta.label;
  if (typeof column.columnDef.header === "string") {
    return column.columnDef.header;
  }
  return column.id;
}

/**
 * Generic data table primitive used across the operator UI. Built on
 * TanStack Table v8; client-only sort, filter and column visibility.
 *
 * Test hooks (slug-prefixed by the `testId` prop, default `data-table`):
 * - `${testId}` — root container
 * - `${testId}-toolbar` — toolbar containing column-visibility trigger
 * - `${testId}-visibility-trigger` — column-visibility menu trigger
 * - `${testId}-visibility-toggle-<columnId>` — checkbox toggle
 * - `${testId}-header-<columnId>` — header cell (sortable)
 * - `${testId}-filter-<columnId>` — per-column filter input
 * - `${testId}-row-<id>` — row (id from `getRowId` or the row index)
 * - `${testId}-empty` — empty-state region
 */
export function DataTable<TData>(
  props: DataTableProps<TData>,
): React.ReactElement {
  const {
    columns,
    data,
    getRowId,
    emptyState,
    caption,
    onRowClick,
    testId = "data-table",
    className,
    enableColumnVisibility = true,
    enableFiltering = true,
    enableSorting = true,
    renderRowAttributes,
  } = props;

  // Strip caller-provided overrides for the attributes DataTable
  // owns: key, onClick, data-testid, className, data-state. A
  // mistake here would silently break sort/click/test-id behaviour.
  const _RESERVED_ATTRS = new Set([
    "key",
    "onClick",
    "onclick",
    "data-testid",
    "className",
    "class",
    "data-state",
  ]);

  const [sorting, setSorting] = React.useState<SortingState>([]);
  const [columnFilters, setColumnFilters] =
    React.useState<ColumnFiltersState>([]);
  const [columnVisibility, setColumnVisibility] =
    React.useState<VisibilityState>({});

  // TanStack Table accepts a mutable `TData[]`. We accept readonly for
  // ergonomics, then snapshot to a fresh mutable array per render —
  // this is also what the table internals expect for reference-equality
  // checks against memoised state.
  const tableData = React.useMemo(() => [...data], [data]);

  const table = useReactTable<TData>({
    data: tableData,
    columns,
    state: { sorting, columnFilters, columnVisibility },
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: enableSorting ? getSortedRowModel() : undefined,
    getFilteredRowModel: enableFiltering ? getFilteredRowModel() : undefined,
    enableSorting,
    enableColumnFilters: enableFiltering,
    getRowId: getRowId
      ? (row, index) => getRowId(row, index)
      : undefined,
  });

  const leafColumns = table.getAllLeafColumns();
  const visibleHeaderGroups = table.getHeaderGroups();
  const rows = table.getRowModel().rows;
  const visibleColumnCount = table.getVisibleLeafColumns().length || 1;

  const showToolbar = enableColumnVisibility || caption != null;

  return (
    <div data-testid={testId} className={cn("flex flex-col gap-2", className)}>
      {showToolbar ? (
        <div
          data-testid={`${testId}-toolbar`}
          className="flex items-center justify-between gap-2"
        >
          <div className="text-xs text-fg-muted">{caption}</div>
          {enableColumnVisibility ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  size="sm"
                  variant="secondary"
                  data-testid={`${testId}-visibility-trigger`}
                  aria-label="Toggle column visibility"
                >
                  <Settings2 aria-hidden className="size-3.5" />
                  Columns
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-44">
                <DropdownMenuLabel>Columns</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {leafColumns
                  .filter((column) => column.getCanHide())
                  .map((column) => (
                    <DropdownMenuCheckboxItem
                      key={column.id}
                      checked={column.getIsVisible()}
                      onCheckedChange={(value) =>
                        column.toggleVisibility(Boolean(value))
                      }
                      data-testid={`${testId}-visibility-toggle-${column.id}`}
                      // Radix dismisses the menu by default after a
                      // checkbox toggle; that breaks rapid multi-toggle
                      // and surprises operators. Keep it open.
                      onSelect={(event) => event.preventDefault()}
                    >
                      {columnLabel(column)}
                    </DropdownMenuCheckboxItem>
                  ))}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}
        </div>
      ) : null}

      <div className="relative w-full overflow-auto rounded-md border border-border">
        <table
          className="w-full caption-bottom border-collapse text-sm"
          role="table"
        >
          <thead
            data-testid={`${testId}-thead`}
            className="sticky top-0 z-10 bg-bg-1 [&_tr]:border-b [&_tr]:border-border"
          >
            {visibleHeaderGroups.map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => {
                  const canSort =
                    enableSorting && header.column.getCanSort();
                  const sortDir = header.column.getIsSorted();
                  const indicator = sortIndicator(sortDir);
                  return (
                    <th
                      key={header.id}
                      data-testid={`${testId}-header-${header.column.id}`}
                      data-sort={sortDir || "none"}
                      scope="col"
                      className={cn(
                        "h-10 px-3 text-left align-middle text-xs font-medium uppercase tracking-wide text-fg-muted",
                      )}
                    >
                      {header.isPlaceholder ? null : (
                        <div className="flex flex-col gap-1">
                          {canSort ? (
                            <button
                              type="button"
                              onClick={header.column.getToggleSortingHandler()}
                              className="flex items-center gap-1 text-left text-xs font-medium uppercase tracking-wide text-fg-muted [@media(hover:hover)]:hover:text-fg"
                              aria-label={`${columnLabel(header.column)}, ${indicator.label}, click to sort`}
                            >
                              {flexRender(
                                header.column.columnDef.header,
                                header.getContext(),
                              )}
                              {indicator.icon}
                            </button>
                          ) : (
                            <span>
                              {flexRender(
                                header.column.columnDef.header,
                                header.getContext(),
                              )}
                            </span>
                          )}
                          {enableFiltering && header.column.getCanFilter() ? (
                            <Input
                              data-testid={`${testId}-filter-${header.column.id}`}
                              type="search"
                              value={
                                (header.column.getFilterValue() as
                                  | string
                                  | undefined) ?? ""
                              }
                              onChange={(event) =>
                                header.column.setFilterValue(
                                  event.target.value || undefined,
                                )
                              }
                              placeholder={`Filter ${columnLabel(header.column)}`}
                              aria-label={`Filter ${columnLabel(header.column)}`}
                              className="h-7 w-full text-xs normal-case tracking-normal"
                            />
                          ) : null}
                        </div>
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody className="[&_tr:last-child]:border-0">
            {rows.length === 0 ? (
              <tr>
                <td
                  colSpan={visibleColumnCount}
                  data-testid={`${testId}-empty`}
                  className="px-6 py-10 text-center text-sm text-fg-muted"
                >
                  {emptyState ?? "No results."}
                </td>
              </tr>
            ) : (
              rows.map((row, idx) => {
                const extra = renderRowAttributes
                  ? Object.fromEntries(
                      Object.entries(
                        renderRowAttributes(row.original, idx),
                      ).filter(([k]) => !_RESERVED_ATTRS.has(k)),
                    )
                  : {};
                return (
                <tr
                  key={row.id}
                  data-testid={`${testId}-row-${row.id}`}
                  data-state={row.getIsSelected() ? "selected" : undefined}
                  onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                  className={cn(
                    "border-b border-border transition-colors [@media(hover:hover)]:hover:bg-bg-2/60 even:bg-bg-1/40",
                    onRowClick &&
                      "cursor-pointer focus-within:bg-bg-2/60",
                  )}
                  {...extra}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td
                      key={cell.id}
                      className="p-3 align-middle"
                      data-column={cell.column.id}
                    >
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext(),
                      )}
                    </td>
                  ))}
                </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export type { ColumnDef } from "@tanstack/react-table";
