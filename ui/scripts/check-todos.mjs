#!/usr/bin/env node
// TODO/FIXME ratchet for the React UI.
//
// Walks src/**/*.{ts,tsx} (excluding *.test.{ts,tsx} and *.stories.tsx) and
// counts case-insensitive, word-bounded matches of TODO|FIXME|XXX|HACK.
//
// Compares against the snapshot in .ratchets/todos.json:
//   - current > max  -> exit 1, list offenders
//   - current < max  -> exit 0, friendly nudge to lower the snapshot
//   - current == max -> exit 0, silent OK
//
// Pass --update to rewrite the snapshot with the current count + listing.
//
// No deps: only node:fs, node:path, node:url.

import { readdirSync, readFileSync, statSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const UI_ROOT = resolve(HERE, "..");
const SRC_DIR = join(UI_ROOT, "src");
const SNAPSHOT_PATH = join(UI_ROOT, ".ratchets", "todos.json");

const PATTERN = /\b(TODO|FIXME|XXX|HACK)\b/i;

function isSourceFile(name) {
  if (!(name.endsWith(".ts") || name.endsWith(".tsx"))) return false;
  if (name.endsWith(".test.ts") || name.endsWith(".test.tsx")) return false;
  if (name.endsWith(".stories.tsx") || name.endsWith(".stories.ts")) return false;
  if (name.endsWith(".d.ts")) return false;
  return true;
}

function* walk(dir) {
  const entries = readdirSync(dir).sort();
  for (const entry of entries) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      // Skip the test fixture directory and any nested node_modules just in case.
      if (entry === "node_modules" || entry === "__mocks__") continue;
      yield* walk(full);
    } else if (st.isFile() && isSourceFile(entry)) {
      yield full;
    }
  }
}

function findMatches() {
  const hits = [];
  for (const file of walk(SRC_DIR)) {
    const text = readFileSync(file, "utf8");
    const lines = text.split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (PATTERN.test(line)) {
        const rel = relative(UI_ROOT, file);
        const trimmed = line.trim();
        const truncated = trimmed.length > 80 ? trimmed.slice(0, 77) + "..." : trimmed;
        hits.push({ file: rel, line: i + 1, text: truncated });
      }
    }
  }
  return hits;
}

function readSnapshot() {
  try {
    const raw = readFileSync(SNAPSHOT_PATH, "utf8");
    return JSON.parse(raw);
  } catch (err) {
    if (err.code === "ENOENT") return null;
    throw err;
  }
}

function writeSnapshot(hits) {
  mkdirSync(dirname(SNAPSHOT_PATH), { recursive: true });
  const snap = {
    max: hits.length,
    snapshot_taken: new Date().toISOString(),
    files_with_todos: hits.map((h) => `${h.file}:${h.line} — ${h.text}`),
  };
  writeFileSync(SNAPSHOT_PATH, JSON.stringify(snap, null, 2) + "\n", "utf8");
  return snap;
}

function main() {
  const args = process.argv.slice(2);
  const update = args.includes("--update");

  const hits = findMatches();
  const current = hits.length;

  if (update) {
    const snap = writeSnapshot(hits);
    console.log(
      `[check-todos] wrote snapshot: ${SNAPSHOT_PATH}\n` +
        `  max = ${snap.max}\n` +
        `  taken = ${snap.snapshot_taken}`,
    );
    return 0;
  }

  const snap = readSnapshot();
  if (!snap) {
    console.error(
      `[check-todos] no snapshot at ${SNAPSHOT_PATH}.\n` +
        `Run: node scripts/check-todos.mjs --update`,
    );
    return 1;
  }

  if (current > snap.max) {
    console.error(
      `[check-todos] FAIL: found ${current} TODO/FIXME/XXX/HACK comments, ` +
        `snapshot allows max ${snap.max}.`,
    );
    console.error(`Offending lines:`);
    for (const h of hits) {
      console.error(`  ${h.file}:${h.line} - ${h.text}`);
    }
    console.error(
      `\nResolve the new comments, or (if intentional) update the snapshot:\n` +
        `  node scripts/check-todos.mjs --update`,
    );
    return 1;
  }

  if (current < snap.max) {
    console.log(
      `[check-todos] OK: ${current} comments (snapshot max = ${snap.max}). ` +
        `You removed ${snap.max - current} TODO(s)! ` +
        `Lower the ratchet:\n  node scripts/check-todos.mjs --update`,
    );
    return 0;
  }

  console.log(`[check-todos] OK: ${current} comments (matches snapshot).`);
  return 0;
}

process.exit(main());
