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
    file: "jobs/JobDetailBreakdown.tsx",
    reason:
      "drawer-internal breakdown — opens on row-click, not a sort/filter " +
      "scenario; intentionally non-DataTable. Extracted from " +
      "JobDetailPanel.tsx so the outer panel migrates cleanly.",
  },
  // ``jobs/JobBreakdownTable.tsx`` retired in v1.0.284: the only
  // consumer (``JobHistoryPanel``) was sunset when guardrails
  // unified onto JobRunner, so every Recent-batches surface now
  // flows through /api/runs + the DataTable-based RunHistoryPanel.
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
    // floor: 3 (BulkImportDialog, UserDetailDrawer, JobDetailBreakdown).
    // v1.0.284 retired JobBreakdownTable when guardrails unified onto
    // JobRunner and the legacy Recent-batches surface (its only
    // consumer) was deleted. LogsTable was migrated once DataTable
    // shipped the `renderRowAttributes` prop in v1.3.19. Goal is 2
    // (only the truly non-table allowed entries — CSV preview +
    // side-pane key/value rows).
    expect(_LEGACY_TABLE_ALLOWLIST.length).toBeLessThanOrEqual(3);
  });

  // -------------------------------------------------------------------
  // Tabular <ul> ratchet — sister rule to the <Table>/<table> check
  // above. Catches the legacy ``<ul>{rows.map(r => <li key={r.id}>...
  // <Badge .../><span class="font-mono">...</span>...</li>)}</ul>``
  // pattern that was rendered tabular data without going through
  // <DataTable>. Older RunHistoryPanel (pre-v1.3.66) is the canonical
  // example. Detection signals:
  //   1. ``<li key={`` — keyed mapping (i.e. row rendering)
  //   2. ``font-mono`` OR ``tabular-nums`` — a tabular column style
  //      operators don't use for prose lists
  //   3. Two or more ``<Badge`` references — multi-column row chrome
  //   4. NOT importing from ``@/components/data-table``
  // Files matching ALL FOUR signals are tabular-list-without-DataTable
  // and need migration. The baseline is pinned at the current
  // count; new files can't add to it.
  // -------------------------------------------------------------------

  // Pre-existing tabular <ul> users at the time this rule landed
  // (v1.3.66). Each entry is a follow-up migration target — drop
  // from the allowlist once the file uses DataTable. No new
  // entries should be added without a "side-pane / non-sortable
  // intentional" justification matching the _LEGACY_TABLE_ALLOWLIST
  // pattern above. Goal is to drive this set to an empty set; the
  // ``ratchets the size cap downward`` test below enforces that.
  const _UL_TABULAR_ALLOWLIST: ReadonlySet<string> = new Set<string>([
    // Side-pane / drawer-internal row layouts — small, contextual,
    // not "what tabular data does the operator want to sort?".
    "jobs/LastRunPanel.tsx",
    "jobs/RunDrawerPanels.tsx",
    "users-admin/UserDetailDrawer.tsx",
    // Card-internal status rows (one row per service / config item).
    // Sortable + filterable would be useful but each card is small
    // (≤10 rows typically); migrate opportunistically.
    "infra-detail/GpuCard.tsx",
    "ops-detail/ConfigIntegrityCard.tsx",
    "ops-detail/CrashloopsCard.tsx",
    "routing-admin/EnvoyAdminSummaryCard.tsx",
    "settings/DisplayPrefsCard.tsx",
    "settings/EffectiveProfileCard.tsx",
    "settings/EnvViewerCard.tsx",
    // Genuinely tabular — high-value migration targets.
    "livetv/EpgHealthCard.tsx",
    "me/LoginHistoryCard.tsx",
  ]);

  function looksTabular(src: string): boolean {
    if (!/<li\s+key=\{/.test(src)) return false;
    if (!/font-mono|tabular-nums/.test(src)) return false;
    const badgeMatches = src.match(/<Badge\b/g);
    if (!badgeMatches || badgeMatches.length < 2) return false;
    if (
      /from\s+["']@\/components\/data-table["']/.test(src) ||
      /\bDataTable\b/.test(src)
    ) {
      return false;
    }
    return true;
  }

  it("no tabular <ul>...<li key=...> rendering outside DataTable adopters", () => {
    const violations: string[] = [];
    for (const file of walk(FEATURES_DIR)) {
      const src = fs.readFileSync(file, "utf-8");
      if (!looksTabular(src)) continue;
      const rel = relPath(file);
      if (_UL_TABULAR_ALLOWLIST.has(rel)) continue;
      violations.push(rel);
    }
    if (violations.length > 0) {
      const msg = [
        `${violations.length} feature file(s) render tabular data via raw`,
        "<ul>...<li key={...}> instead of <DataTable>. The signals: keyed",
        "mapping + font-mono/tabular-nums + 2+ Badge cells. Migrate each",
        "to <DataTable> for site-wide consistency. If a file legitimately",
        "renders prose / non-tabular data via <ul>, refactor it to drop",
        "the font-mono signal so it doesn't trip this ratchet, OR add an",
        "entry to _UL_TABULAR_ALLOWLIST with a justification.",
        "",
        "Files needing migration:",
        ...violations.map((v) => `  - features/${v}`),
      ].join("\n");
      throw new Error(msg);
    }
    expect(violations).toEqual([]);
  });

  it("UL allowlist size doesn't grow without reason", () => {
    // Floor — only goes DOWN as files migrate to DataTable. v1.3.66
    // seeded the allowlist with 12 pre-existing tabular <ul> users.
    // Bump down each time you remove an entry (via a real
    // DataTable migration). New tabular <ul> rendering MUST go
    // through <DataTable> — only side-pane / drawer-internal row
    // layouts get justified exemptions.
    expect(_UL_TABULAR_ALLOWLIST.size).toBeLessThanOrEqual(12);
  });
});
