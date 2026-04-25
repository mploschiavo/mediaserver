# Ratchet E: TODO/FIXME ratchet

## Rationale

Sprawl of `TODO`/`FIXME`/`XXX`/`HACK` comments tends to grow monotonically
unless something fails CI. We pin the count to a snapshot in
`.ratchets/todos.json` and fail the build if the count goes up. The count
can only go *down* — and when it does, contributors lower the snapshot in
the same PR so the win is locked in.

## How it works

`scripts/check-todos.mjs` walks `src/**/*.{ts,tsx}` (skipping
`*.test.{ts,tsx}` and `*.stories.tsx`) and word-bounded-matches
`TODO|FIXME|XXX|HACK` (case-insensitive). It compares to
`.ratchets/todos.json`:

- **current > snapshot.max** → exit 1, prints offending `file:line — text`.
- **current < snapshot.max** → exit 0, friendly nudge to lower the snapshot.
- **current == snapshot.max** → silent OK.

Zero dependencies — only `node:fs`, `node:path`, `node:url`. No
`npm install` required to run it in CI.

## Workflow

1. CI runs `pnpm check:todos`. New TODOs without snapshot bumps fail the PR.
2. To intentionally raise the ratchet (rare — should always be challenged in
   review): resolve via PR description, then run

   ```sh
   pnpm check:todos -- --update
   ```

3. To lower the ratchet after deleting TODOs (the common case), the script
   will tell you. Run the same `--update` command and commit the result.

## Files

- `ui/scripts/check-todos.mjs` — the ratchet script (zero deps).
- `ui/.ratchets/todos.json` — the snapshot (max + listing).
- `ui/.ratchets/pending/E-package.json` — `package.json` delta for the main
  agent to merge (adds the `check:todos` script).
