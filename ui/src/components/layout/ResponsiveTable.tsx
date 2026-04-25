import type { ReactNode } from "react";
import { Card } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { EmptyState } from "@/components/layout/EmptyState";
import { cn } from "@/lib/cn";

export interface ResponsiveTableColumn<T> {
  /** Stable id, used as the React key for header / cell pairs. */
  id: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  className?: string;
}

export interface ResponsiveTableProps<T> {
  rows: T[];
  rowKey: (row: T) => string;
  /** Desktop column descriptors. */
  columns: ResponsiveTableColumn<T>[];
  /** Mobile per-row card body. */
  card: (row: T) => ReactNode;
  /** Replaces the empty state when `rows` is empty. */
  empty?: ReactNode;
  className?: string;
}

/**
 * Shared shell for tabular data that adapts to viewport width:
 *  - `>= md`: standard `<Table>` with `columns`,
 *  - `< md`: stacked `<Card>` list using the operator-supplied `card`
 *    renderer.
 *
 * Both DOM trees mount; the inactive one is hidden via Tailwind. That
 * keeps the data source single-sourced (no JS-driven layout shift,
 * SSR-friendly) at the cost of a little extra render work.
 */
export function ResponsiveTable<T>({
  rows,
  rowKey,
  columns,
  card,
  empty,
  className,
}: ResponsiveTableProps<T>) {
  if (rows.length === 0) {
    return (
      <div className={className} data-testid="responsive-table-empty">
        {empty ?? <EmptyState title="Nothing to show" />}
      </div>
    );
  }

  return (
    <div className={cn("w-full", className)} data-testid="responsive-table">
      {/* Desktop: full table. */}
      <div className="hidden md:block" data-testid="responsive-table-desktop">
        <Table>
          <TableHeader>
            <TableRow>
              {columns.map((column) => (
                <TableHead key={column.id} className={column.className}>
                  {column.header}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={rowKey(row)} data-row-key={rowKey(row)}>
                {columns.map((column) => (
                  <TableCell key={column.id} className={column.className}>
                    {column.cell(row)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Mobile: stacked cards. */}
      <div
        className="flex flex-col gap-3 md:hidden"
        data-testid="responsive-table-mobile"
        role="list"
      >
        {rows.map((row) => (
          <Card
            key={rowKey(row)}
            data-row-key={rowKey(row)}
            role="listitem"
            className="p-4"
          >
            {card(row)}
          </Card>
        ))}
      </div>
    </div>
  );
}
