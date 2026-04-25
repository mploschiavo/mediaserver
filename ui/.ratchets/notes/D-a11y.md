# Ratchet D: a11y violation ratchet via `vitest-axe`

## Rationale

End-to-end a11y checks (Playwright + axe-playwright) only run on the
deploy-staged branch and surface regressions hours after the offending
change merges. By moving axe-core into the unit-test layer with
`vitest-axe`, every PR that touches a major page gets a sub-second
"zero serious/critical violations" gate at the same point in the
pipeline as the rest of `pnpm test`.

The ratchet locks **serious + critical** violations to zero. Minor
and moderate findings are still printed in the test log but do not
fail the build — they're often layout / contrast nits that can lag a
release without blocking shipping.

## Scope (which routes / components are gated)

| Surface                              | Why it's a major page                                      |
| ------------------------------------ | ---------------------------------------------------------- |
| `routes/media-integrity`             | The luxury showcase route + the source of operator CTAs.   |
| `components/layout/AppShell`         | Wraps every route — skip-link + main landmark must work.   |
| `components/layout/CommandPalette`   | Modal dialog (Radix Dialog + cmdk) opened on every screen. |
| `components/layout/UserMenu`         | Dropdown menu (Radix DropdownMenu) shown in the TopBar.    |

Each test wraps a single `render(...)` followed by
`assertNoA11yViolations(container)`; no per-test allowlists.

## Tag set

`runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] }` — WCAG 2.1
A + AA. We deliberately exclude `best-practice` (which has many
opinionated checks like "use landmarks") and `wcag21aaa` (overkill
for an internal admin SPA).

## Polyfills

`src/test/setup.ts` already polyfills the happy-dom gaps that bite
Radix UI (`hasPointerCapture`, `setPointerCapture`,
`releasePointerCapture`, `scrollIntoView`). axe-core 4.10 does **not**
require additional happy-dom polyfills — it reads computed styles
and the layout tree via APIs happy-dom 15 already implements. No
modifications to `setup.ts` were needed.

## Pinned versions (in `D-a11y.json`)

- `vitest-axe@^0.1.0` — Vitest-native fork of `jest-axe`. Returns
  the standard axe results object so the helper can filter by
  `impact` directly.
- `axe-core@^4.10.3` — peer dep; pinned high enough to get the
  React 19 / cmdk-friendly rule set.

## Files

- `ui/src/test/a11y.ts` — `assertNoA11yViolations(container)` helper.
- `ui/src/routes/media-integrity.a11y.test.tsx`
- `ui/src/components/layout/AppShell.a11y.test.tsx`
- `ui/src/components/layout/CommandPalette.a11y.test.tsx`
- `ui/src/components/layout/UserMenu.a11y.test.tsx`
- `ui/.ratchets/pending/D-a11y.json` — `package.json` delta for the
  main agent to merge.

## Workflow

1. CI runs `pnpm test` — the four `*.a11y.test.tsx` files participate
   in the regular test glob and fail like any other test.
2. On a regression, the failing test prints each rule ID + node
   selector via the `console.error` block in `assertNoA11yViolations`.
   That's enough to land the fix without re-running locally with a
   verbose reporter.
3. To add a new gated surface: drop `<name>.a11y.test.tsx` next to
   the component, mirror the mock surface from the component's
   existing test, render, and call `assertNoA11yViolations(container)`.

## Anticipated violations (static reasoning)

The components were read top-to-bottom; nothing obvious looks
broken at WCAG 2 A/AA serious/critical:

- `AppShell` wires a "Skip to main content" link, gives `<main>` an
  `id` + `tabindex="-1"`, and gates the mobile drawer behind a Vaul
  primitive that ships axe-clean.
- `CommandPalette` includes `Dialog.Title` + `Dialog.Description`
  (both `sr-only`) and an `aria-label` on the dialog wrapper. Each
  `Command.Item` carries visible text, so the `button-name` /
  `link-name` rules pass.
- `UserMenu` puts an `aria-label="Open account menu"` on the
  trigger, uses `Avatar.Image` with `alt={name}`, and every
  `DropdownMenu.Item` ships visible text.
- `MediaIntegrityPage` consists of a `<PageHeader>` with `<h1>` /
  `<p>` plus card-style children — no obvious heading-order or
  landmark-naming traps.

Two flags worth watching when the tests actually run:

1. **Color-contrast** — axe checks computed styles, but happy-dom
   doesn't paint, so contrast violations are usually skipped (axe
   reports them as `incomplete`, not `violation`). If a future
   environment switch flips that, a Tailwind `text-fg-faint` token
   on a low-contrast background could trip `color-contrast`.
2. **`region` / `landmark-one-main`** — these live under
   `best-practice` (excluded) so they won't fail today. If the tag
   set is widened later, the `<aside>`-less drawer content might
   need explicit `role="navigation"` wrapping.

If the tests do surface a real violation when run, the failure
output (rule + selector) is the report — fix at the source rather
than allowlisting in this helper.
