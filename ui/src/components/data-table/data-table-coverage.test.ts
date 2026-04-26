/**
 * Ratchet: every operator-facing table in `ui/src/features/**` must use
 * the `<DataTable>` primitive instead of the raw `<Table>` building
 * blocks from `ui/src/components/ui/table.tsx`.
 *
 * Why this exists: the operator's request was "all tables on the UI
 * should be sortable + filterable, consistency is important."
 * Migrations to `<DataTable>` were attempted in three PRs (PR1 / PR2 /
 * PR3) but PR2 + PR3 reverted under agent file-collision pressure —
 * leaving an inconsistent UI where some cards have sort/filter and
 * most don't. This ratchet locks the contract: any new table must
 * reach for `<DataTable>` first; any legacy `<Table>` surface that
 * sneaks back in fails the build. The ALLOWLIST below documents
 * every legitimate non-table use of `<Table>` (markdown rendering,
 * dialog-internal CSV preview, side-pane row layouts).
 *
 * To migrate a card off the allowlist:
 *   1. Define `ColumnDef<TRow>[]`.
 *   2. Replace `<TableHeader>/<TableBody>/<TableRow>` with
 *      `<DataTable<TRow> columns={cols} data={rows} testId="…">`.
 *   3. Remove the entry from `_LEGACY_TABLE_ALLOWLIST` below.
 *   4. Update the card's `.test.tsx` to match the new row testId
 *      pattern (`<testId>-row-<id>`).
 */

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// Repo root resolved relative to this file: ui/src/components/data-table
// → parents[3] is the ui/ dir; from there walk up to the repo root.
const UI_DIR = path.resolve(__dirname, "..", "..", "..");
const FEATURES_DIR = path.join(UI_DIR, "src", "features");

// Files allowed to use the raw `<Table>` primitive OR raw HTML
// table tags (<table>/<thead>/<tbody>). Each entry MUST include a
// justification — if you're adding to this list, the reviewer's
// first question is "why isn't this a DataTable?"
const _LEGACY_TABLE_ALLOWLIST: ReadonlyArray<{
  file: string;
  reason: string;
}> = [
  {
    file: "users-admin/BulkImportDialog.tsx",
    reason:
      "CSV preview inside a modal — fixed-width preview, not a sortable list",
  },
  {
    file: "users-admin/UserDetailDrawer.tsx",
    reason:
      "Side-pane key/value rows, not a sortable/filterable list of homogeneous records",
  },
  {
    file: "logs/LogsTable.tsx",
    reason:
      "Streaming log viewer with tail-mode auto-scroll, search-mark spans, " +
      "and per-row data-source/level/tone attributes. Needs DataTable to " +
      "expose a renderRowAttributes/renderRow prop before migrating " +
      "(Wave 6 candidate).",
  },
  {
    file: "jobs/JobDetailPanel.tsx",
    reason:
      "Embedded breakdown table inside the job-detail drawer — outer " +
      "DataTable migration would distort the drawer's nested-grid layout. " +
      "Wave 6: split inner table out as a presentational sub-component " +
      "and migrate that.",
  },
  {
    file: "jobs/JobHistoryPanel.tsx",
    reason:
      "Outer 'Recent batches' table IS migrated to <DataTable>. The " +
      "remaining raw <table> is the per-batch breakdown inside the Vaul " +
      "drawer (opens on row-click) — drawer-only, not a sort/filter " +
      "scenario. Wave 6: extract drawer breakdown into its own file " +
      "(jobs/JobBreakdownTable.tsx) and allowlist that instead.",
  },
];

// Catches both the `<Table>` primitive AND raw HTML `<table>` tags.
// Raw <table> regex requires a word boundary or attribute character so
// it doesn't false-positive on words like "Notable" or "Tabletop".
const _LEGACY_TABLE_PATTERN =
  /<(?:Table|TableHeader|TableBody|TableRow|TableHead)\b|<table[\s>]|<thead[\s>]|<tbody[\s>]/;

function relPath(abs: string): string {
  return path.relative(FEATURES_DIR, abs).replace(/\\/g, "/");
}

function* walk(dir: string): Generator<string> {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    if (ent.name === "node_modules" || ent.name === "__tests__") continue;
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) {
      yield* walk(p);
    } else if (ent.name.endsWith(".tsx") && !ent.name.endsWith(".test.tsx") && !ent.name.endsWith(".stories.tsx")) {
      yield p;
    }
  }
}

describe("DataTable coverage ratchet", () => {
  it("every <Table> usage in features/ is migrated to <DataTable> or allowlisted", () => {
    const allowSet = new Set(_LEGACY_TABLE_ALLOWLIST.map((e) => e.file));
    const violations: string[] = [];
    for (const file of walk(FEATURES_DIR)) {
      const src = fs.readFileSync(file, "utf-8");
      if (!_LEGACY_TABLE_PATTERN.test(src)) continue;
      const rel = relPath(file);
      if (allowSet.has(rel)) continue;
      violations.push(rel);
    }
    if (violations.length > 0) {
      const msg = [
        `${violations.length} feature file(s) still use the raw <Table> primitive`,
        "instead of <DataTable>. Migrate each to use",
        "`ui/src/components/data-table/DataTable.tsx` for site-wide consistency",
        "(sort + filter + column-visibility). If a file genuinely shouldn't be",
        "migrated, add it to _LEGACY_TABLE_ALLOWLIST in this file with a reason.",
        "",
        "Files needing migration:",
        ...violations.map((v) => `  - features/${v}`),
      ].join("\n");
      throw new Error(msg);
    }
    expect(violations).toEqual([]);
  });

  it("allowlist entries actually use <Table>", () => {
    // Guard against stale allowlist entries: if a file no longer uses
    // <Table> (because it was migrated), the entry should be removed
    // from _LEGACY_TABLE_ALLOWLIST. Prevents the allowlist from
    // accumulating dead entries.
    for (const entry of _LEGACY_TABLE_ALLOWLIST) {
      const abs = path.join(FEATURES_DIR, entry.file);
      expect(fs.existsSync(abs), `allowlisted file does not exist: ${entry.file}`).toBe(true);
      const src = fs.readFileSync(abs, "utf-8");
      expect(
        _LEGACY_TABLE_PATTERN.test(src),
        `allowlisted file no longer uses <Table>; remove from allowlist: ${entry.file}`,
      ).toBe(true);
    }
  });

  it("allowlist size doesn't grow without reason", () => {
    // Floor — only goes DOWN as we migrate. Bump down each time you
    // remove an entry. Prevents silent allowlist growth. Current
    // floor: 5 (BulkImportDialog, UserDetailDrawer, LogsTable,
    // JobDetailPanel, JobHistoryPanel). Goal is 2 (only the truly
    // non-table allowed entries — CSV preview + side-pane key/value
    // rows; the 3 drawer/streaming cases get their inner tables
    // extracted in Wave 6).
    expect(_LEGACY_TABLE_ALLOWLIST.length).toBeLessThanOrEqual(5);
  });
});
