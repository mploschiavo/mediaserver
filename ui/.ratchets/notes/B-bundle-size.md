# Ratchet B: bundle-size budget via `size-limit`

## Rationale

We ship a SPA that loads on every page view. Without a CI ratchet, dependency
creep (an extra icon pack, a "tiny" date lib, an unused Radix primitive) slips
in unnoticed and gzip payload drifts up by tens of KB per quarter. `size-limit`
fails the build when any tracked artifact crosses its budget, so growth becomes
a deliberate, reviewable decision instead of an accident.

## Budgets (gzip)

Set ~5–10% above the current `vite build` output to leave normal-noise headroom
without masking real regressions.

| Artifact                       | Current  | Budget   | Headroom |
| ------------------------------ | -------- | -------- | -------- |
| `dist/assets/index-*.js`       | 118.43 KB | 130 KB  | ~9.8%    |
| `dist/assets/ui-*.js`          | 78.05 KB  | 85 KB   | ~8.9%    |
| `dist/assets/tanstack-*.js`    | 42.35 KB  | 48 KB   | ~13.3%   |
| `dist/assets/index-*.css`      | 9.61 KB   | 12 KB   | ~24.9%   |
| Total JS (`dist/assets/*.js`)  | ~241 KB   | 250 KB  | ~3.7%    |

The total-JS ceiling is intentionally tighter than the per-chunk sum: it stops
"shuffle weight from one chunk into another" from quietly defeating the
per-chunk budgets.

## Why preset-app

`@size-limit/preset-app` measures gzipped size of pre-built artifacts directly
(no re-bundling), which is what we want — we already trust Vite's output and
just want to check the numbers.

## Workflow

1. CI runs `pnpm size` after `pnpm build`. Failures block the PR.
2. On a deliberate, justified bump (new feature, vendored lib upgrade), run:

   ```sh
   pnpm size --json
   ```

   Copy the new byte counts into `.size-limit.json`, rounding up to leave
   ~5–10% headroom again. Justify the bump in the PR description (what feature
   added the weight, what alternatives were considered).
3. On a *reduction*, lower the budgets in the same PR so the win is locked in.

## Known issue (do not fix here)

`dist/assets/react-*.js` is effectively empty (44 bytes) due to a `manualChunks`
misconfiguration in `vite.config.ts` — react/react-dom are landing in the main
`index-*.js` chunk instead of their own vendor chunk. We deliberately do **not**
add a budget for `react-*.js`: a budget on a 44-byte stub would either be
trivially passing or would lock in the bug. Once `manualChunks` is fixed in a
separate PR, add a `react-*.js` entry (~50 KB gzip is the typical react+react-dom
floor) and tighten `index-*.js` accordingly.

## Files

- `ui/.size-limit.json` — budget config
- `ui/.ratchets/pending/B-size-limit.json` — `package.json` delta for the main agent to merge
