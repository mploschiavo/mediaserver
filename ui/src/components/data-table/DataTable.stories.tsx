import type { Meta, StoryObj } from "@storybook/react";
import type { ColumnDef } from "@tanstack/react-table";
import { Badge } from "@/components/ui/badge";
import { DataTable } from "./DataTable";

interface Token {
  id: string;
  name: string;
  scopes: string[];
  createdAt: string;
  lastUsedAt: string | null;
}

const fixtureRows: Token[] = [
  {
    id: "t-1",
    name: "CI deploy",
    scopes: ["read", "write"],
    createdAt: "2026-04-01",
    lastUsedAt: "2026-04-23",
  },
  {
    id: "t-2",
    name: "release-bot",
    scopes: ["read"],
    createdAt: "2026-03-15",
    lastUsedAt: null,
  },
  {
    id: "t-3",
    name: "audit-readonly",
    scopes: ["read"],
    createdAt: "2026-02-08",
    lastUsedAt: "2026-04-22",
  },
];

const columns: ColumnDef<Token>[] = [
  {
    id: "name",
    accessorKey: "name",
    header: "Name",
    meta: { label: "Name" },
  },
  {
    id: "scopes",
    accessorFn: (row) => row.scopes.join(" "),
    header: "Scopes",
    meta: { label: "Scopes" },
    cell: ({ row }) => (
      <div className="flex flex-wrap gap-1">
        {row.original.scopes.map((scope) => (
          <Badge key={scope} variant="default">
            {scope}
          </Badge>
        ))}
      </div>
    ),
  },
  {
    id: "createdAt",
    accessorKey: "createdAt",
    header: "Created",
    meta: { label: "Created" },
    enableColumnFilter: false,
  },
  {
    id: "lastUsedAt",
    accessorKey: "lastUsedAt",
    header: "Last used",
    meta: { label: "Last used" },
    cell: ({ row }) => row.original.lastUsedAt ?? "never",
    enableColumnFilter: false,
  },
];

const meta: Meta<typeof DataTable<Token>> = {
  title: "Molecules/DataTable",
  component: DataTable<Token>,
  parameters: { layout: "padded" },
};
export default meta;
type Story = StoryObj<typeof DataTable<Token>>;

export const Default: Story = {
  args: {
    columns,
    data: fixtureRows,
    getRowId: (row) => row.id,
    caption: `${fixtureRows.length} tokens`,
  },
};

export const Empty: Story = {
  args: {
    columns,
    data: [],
    emptyState: "No tokens issued.",
  },
};

export const NoVisibilityMenu: Story = {
  args: {
    columns,
    data: fixtureRows,
    getRowId: (row) => row.id,
    enableColumnVisibility: false,
  },
};

export const Clickable: Story = {
  args: {
    columns,
    data: fixtureRows,
    getRowId: (row) => row.id,
    onRowClick: (row) => alert(`row ${row.id} clicked`),
  },
};
