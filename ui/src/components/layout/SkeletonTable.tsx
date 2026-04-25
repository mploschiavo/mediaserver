import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/cn";

interface SkeletonTableProps {
  rows: number;
  columns: number;
  className?: string;
}

/**
 * Table-shaped placeholder. Renders the same `<Table>` chrome as the
 * real content so column widths land in the same place once data
 * resolves; only the cell contents are pulse-skeletons.
 */
export function SkeletonTable({ rows, columns, className }: SkeletonTableProps) {
  // `Array.from({ length: n }, (_, i) => i)` keeps row/column identity
  // stable for React keys without leaning on the (avoid-)`index` lint.
  const rowKeys = Array.from({ length: rows }, (_, i) => i);
  const colKeys = Array.from({ length: columns }, (_, i) => i);

  return (
    <div
      aria-busy="true"
      aria-hidden="true"
      data-testid="skeleton-table"
      className={cn(className)}
    >
      <Table>
        <TableHeader>
          <TableRow>
            {colKeys.map((c) => (
              <TableHead key={`h-${c}`}>
                <Skeleton className="h-3 w-20" />
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rowKeys.map((r) => (
            <TableRow key={`r-${r}`}>
              {colKeys.map((c) => (
                <TableCell key={`r-${r}-c-${c}`}>
                  <Skeleton className="h-3 w-full" />
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
