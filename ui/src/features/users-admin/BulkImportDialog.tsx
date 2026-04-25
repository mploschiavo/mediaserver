import { useState, type ChangeEvent } from "react";
import { Upload } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useBulkImportUsers, type BulkImportRow } from "./hooks";

function explain(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

/**
 * Tiny CSV parser. Handles quoted fields and commas in quotes; not a
 * complete RFC-4180 implementation but covers the common payloads
 * the controller accepts. Bringing a real CSV lib in would push the
 * 250 KB JS bundle ceiling — admin-only flow, kept slim deliberately.
 */
function parseCsv(text: string): BulkImportRow[] {
  const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (lines.length === 0) return [];
  const header = splitCsvLine(lines[0]!);
  const rows: BulkImportRow[] = [];
  for (let i = 1; i < lines.length; i++) {
    const cells = splitCsvLine(lines[i]!);
    const row: Record<string, string> = {};
    header.forEach((h, j) => {
      row[h.trim()] = cells[j]?.trim() ?? "";
    });
    if (row.username) {
      rows.push(row as unknown as BulkImportRow);
    }
  }
  return rows;
}

function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let buf = "";
  let inQuote = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line.charAt(i);
    if (inQuote) {
      if (ch === '"' && line.charAt(i + 1) === '"') {
        buf += '"';
        i++;
      } else if (ch === '"') {
        inQuote = false;
      } else {
        buf += ch;
      }
    } else {
      if (ch === '"') {
        inQuote = true;
      } else if (ch === ",") {
        out.push(buf);
        buf = "";
      } else {
        buf += ch;
      }
    }
  }
  out.push(buf);
  return out;
}

export function BulkImportDialog() {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<BulkImportRow[]>([]);
  const [filename, setFilename] = useState("");
  const importMut = useBulkImportUsers();

  const reset = () => {
    setRows([]);
    setFilename("");
  };

  const handleFile = async (ev: ChangeEvent<HTMLInputElement>) => {
    const file = ev.target.files?.[0];
    if (!file) return;
    setFilename(file.name);
    const text = await file.text();
    setRows(parseCsv(text));
  };

  const handleImport = () => {
    if (rows.length === 0) return;
    importMut.mutate(
      { rows },
      {
        onSuccess: () => {
          toast.success(
            `Imported ${rows.length} user${rows.length === 1 ? "" : "s"}`,
          );
          reset();
          setOpen(false);
        },
        onError: (err) =>
          toast.error(`Import failed: ${explain(err, "request failed")}`),
      },
    );
  };

  const preview = rows.slice(0, 5);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button variant="secondary" size="sm" data-testid="bulk-import-trigger">
          <Upload aria-hidden />
          Bulk import
        </Button>
      </DialogTrigger>
      <DialogContent
        className="max-w-2xl"
        data-testid="bulk-import-dialog"
      >
        <DialogHeader>
          <DialogTitle>Bulk import users</DialogTitle>
          <DialogDescription>
            Upload a CSV with headers <code>username</code>, <code>email</code>,
            <code> role_slug</code>. Preview shows the first 5 rows.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="bulk-csv">CSV file</Label>
          <Input
            id="bulk-csv"
            type="file"
            accept=".csv,text/csv"
            onChange={handleFile}
            data-testid="bulk-import-file"
          />
          {filename ? (
            <p className="text-xs text-fg-muted" data-testid="bulk-import-name">
              {filename} · {rows.length} row{rows.length === 1 ? "" : "s"}
            </p>
          ) : null}
        </div>

        {preview.length > 0 ? (
          <div
            className="rounded-md border border-border"
            data-testid="bulk-import-preview"
          >
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Username</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead>Role</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {preview.map((r, idx) => (
                  <TableRow key={`${r.username}-${idx}`}>
                    <TableCell>{r.username}</TableCell>
                    <TableCell className="text-fg-muted">
                      {r.email ?? ""}
                    </TableCell>
                    <TableCell className="text-fg-muted">
                      {r.role_slug ?? ""}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : null}

        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="secondary">
              Cancel
            </Button>
          </DialogClose>
          <Button
            type="button"
            variant="primary"
            disabled={rows.length === 0}
            loading={importMut.isPending}
            onClick={handleImport}
            data-testid="bulk-import-submit"
          >
            Import {rows.length || ""}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
