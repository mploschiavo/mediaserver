import { useMemo, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { useSnapshotContent, useSnapshotDiff, type SnapshotEntry } from "./hooks";

interface SnapshotDiffDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  snapshots: readonly SnapshotEntry[];
  initialA?: string;
  initialB?: string;
}

interface DiffLine {
  kind: "context" | "add" | "remove";
  text: string;
}

/**
 * Tiny, dependency-free line-by-line diff. Walks both texts in
 * lockstep using a longest-common-subsequence trace; for the
 * snapshot UI we only need a readable stream of `+ ` / `- `
 * lines — not a real Myers/Hunt-McIlroy diff. Bundle budget
 * forbids pulling `jsdiff` or `diff-match-patch`.
 */
function lineDiff(a: string, b: string): DiffLine[] {
  const aLines = a.split(/\r?\n/);
  const bLines = b.split(/\r?\n/);
  const m = aLines.length;
  const n = bLines.length;
  // LCS dp table — flat row-major to keep `noUncheckedIndexedAccess`
  // happy without sprinkling non-null assertions through the trace.
  const get = (i: number, j: number): number => dp[i * (n + 1) + j] ?? 0;
  const set = (i: number, j: number, v: number): void => {
    dp[i * (n + 1) + j] = v;
  };
  const dp = new Array<number>((m + 1) * (n + 1)).fill(0);
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      if (aLines[i] === bLines[j]) set(i, j, get(i + 1, j + 1) + 1);
      else set(i, j, Math.max(get(i + 1, j), get(i, j + 1)));
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    const aLine = aLines[i] ?? "";
    const bLine = bLines[j] ?? "";
    if (aLine === bLine) {
      out.push({ kind: "context", text: aLine });
      i++;
      j++;
    } else if (get(i + 1, j) >= get(i, j + 1)) {
      out.push({ kind: "remove", text: aLine });
      i++;
    } else {
      out.push({ kind: "add", text: bLine });
      j++;
    }
  }
  while (i < m) out.push({ kind: "remove", text: aLines[i++] ?? "" });
  while (j < n) out.push({ kind: "add", text: bLines[j++] ?? "" });
  return out;
}

function snapshotToText(snap: Record<string, string> | undefined): string {
  if (!snap || typeof snap !== "object") return "";
  return Object.entries(snap)
    .map(([path, body]) => `===== ${path} =====\n${body}`)
    .join("\n\n");
}

/**
 * Diff dialog with two snapshot select dropdowns and a unified-diff
 * display. Pulls each side's content via `useSnapshotContent` and
 * computes the line diff client-side (no external diff library —
 * bundle budget tight). Additions are styled green-ish, removals
 * red-ish, with `+ `/`- ` prefixes for screen readers.
 *
 * The high-level per-file diff metadata from `/api/snapshot-diff`
 * is shown above the unified body as a quick "what changed" badge
 * row.
 */
export function SnapshotDiffDialog({
  open,
  onOpenChange,
  snapshots,
  initialA,
  initialB,
}: SnapshotDiffDialogProps) {
  const [a, setA] = useState<string | undefined>(initialA);
  const [b, setB] = useState<string | undefined>(initialB);
  const contentA = useSnapshotContent(a);
  const contentB = useSnapshotContent(b);
  const summary = useSnapshotDiff(a, b);

  const diff = useMemo(() => {
    if (!a || !b || a === b) return null;
    if (!contentA.data || !contentB.data) return null;
    return lineDiff(
      snapshotToText(contentA.data.snapshot),
      snapshotToText(contentB.data.snapshot),
    );
  }, [a, b, contentA.data, contentB.data]);

  const loading = contentA.isLoading || contentB.isLoading;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-3xl"
        data-testid="snapshot-diff-dialog"
      >
        <DialogHeader>
          <DialogTitle>Compare snapshots</DialogTitle>
          <DialogDescription>
            Pick two snapshots to view the per-line differences in their
            captured config files.
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="snapshot-diff-a">Snapshot A</Label>
            <Select value={a} onValueChange={setA}>
              <SelectTrigger
                id="snapshot-diff-a"
                data-testid="snapshot-diff-a-select"
              >
                <SelectValue placeholder="Select snapshot" />
              </SelectTrigger>
              <SelectContent>
                {snapshots.map((s) => (
                  <SelectItem key={s.file} value={s.file}>
                    {s.file}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="snapshot-diff-b">Snapshot B</Label>
            <Select value={b} onValueChange={setB}>
              <SelectTrigger
                id="snapshot-diff-b"
                data-testid="snapshot-diff-b-select"
              >
                <SelectValue placeholder="Select snapshot" />
              </SelectTrigger>
              <SelectContent>
                {snapshots.map((s) => (
                  <SelectItem key={s.file} value={s.file}>
                    {s.file}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {summary.data && summary.data.diffs && summary.data.diffs.length > 0 ? (
          <div
            className="flex flex-wrap gap-1.5"
            data-testid="snapshot-diff-summary"
          >
            {summary.data.diffs.map((d) => (
              <Badge
                key={d.file}
                variant={
                  d.status === "added"
                    ? "success"
                    : d.status === "removed"
                      ? "danger"
                      : "warning"
                }
              >
                {d.status} {d.file}
              </Badge>
            ))}
          </div>
        ) : null}

        <div
          className="max-h-[55vh] overflow-auto rounded-md border border-border bg-bg-2"
          data-testid="snapshot-diff-body"
        >
          {!a || !b ? (
            <p className="p-4 text-sm text-fg-muted">
              Choose two snapshots to compare.
            </p>
          ) : a === b ? (
            <p className="p-4 text-sm text-fg-muted">
              Pick two distinct snapshots to compare.
            </p>
          ) : loading ? (
            <div className="space-y-2 p-4" data-testid="snapshot-diff-loading">
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-4 w-full" />
              ))}
            </div>
          ) : contentA.error || contentB.error ? (
            <div
              role="alert"
              data-testid="snapshot-diff-error"
              className="p-4 text-sm text-danger"
            >
              {contentA.error?.message ?? contentB.error?.message ?? "error"}
            </div>
          ) : diff ? (
            <pre className="m-0 whitespace-pre p-3 font-mono text-xs leading-relaxed">
              {diff.map((line, idx) => {
                const prefix =
                  line.kind === "add" ? "+ " : line.kind === "remove" ? "- " : "  ";
                const cls =
                  line.kind === "add"
                    ? "block bg-[color-mix(in_oklab,var(--color-success)_18%,transparent)] text-success"
                    : line.kind === "remove"
                      ? "block bg-[color-mix(in_oklab,var(--color-danger)_18%,transparent)] text-danger"
                      : "block text-fg";
                return (
                  <span key={idx} className={cls}>
                    {prefix}
                    {line.text}
                    {"\n"}
                  </span>
                );
              })}
            </pre>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
