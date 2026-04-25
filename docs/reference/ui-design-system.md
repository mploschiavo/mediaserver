# UI design system

The dashboard is a React 19 single-page app served by the
[`media-stack-ui`](ui-container.md) container. Every route MUST look like
the same product. This document is the contract; the components, tokens,
and a11y patterns below are the **only** sanctioned shape. Anything not
listed here is forbidden.

The contract is enforced by a combination of:

- `pnpm lint` — locks `no-console`, `no-only-tests`,
  `@typescript-eslint/no-explicit-any` at zero.
- `pnpm size` — `size-limit` per-chunk + a 250 KB total JS-gzip ceiling
  (current: 240.8 KB).
- `pnpm check:todos` — TODO snapshot.
- Path contract: every `/api/*` literal in the SPA must exist in the
  generated OpenAPI types.
- Manifest contract: every PNG referenced in `dist/manifest.webmanifest`
  must exist on disk and match the declared dimensions.
- A11y ratchet: see [security-a11y-contract.md](security-a11y-contract.md).

Adding a new visual pattern means adding it here AND lowering the
budgeted `size-limit` headroom in the same PR — which forces a review
conversation about whether the new pattern earns its keep.

## Stack

- **React 19** + **Vite 6** — the build emits hashed `dist/assets/*` for
  the nginx layer, served `1y immutable`.
- **Tailwind v4** (beta) — utility-first; tokens live in
  `ui/src/styles/tokens.css` as `@theme` CSS custom properties (see
  *Theme tokens* below).
- **shadcn/ui** — components are *copied* into `ui/src/components/ui/*`,
  not consumed as a runtime dep. Wrappers over Radix primitives.
- **Radix primitives** — Dialog, Popover, DropdownMenu, Tabs, Tooltip.
  Every primitive is properly labeled (see [a11y contract](security-a11y-contract.md)).
- **TanStack Router** (file-based) + **TanStack Query** + **TanStack Table**.
- **Framer Motion** — entrance/transition animation, respects
  `prefers-reduced-motion`.
- **cmdk** — command palette (`Cmd-K` / `Ctrl-K`).
- **Sonner** — toasts.
- **Vaul** — bottom-sheet drawer on mobile.
- **Geist Variable** — primary typeface, loaded from `cdn.jsdelivr.net`
  with a `CacheFirst` Workbox strategy.
- **lucide-react** — icon set.
- **next-themes** — light/dark switching, no flash on first paint.
- **PWA** via `vite-plugin-pwa` (Workbox). `/api/*` is `NetworkOnly`;
  Geist CDN is `CacheFirst`; the rest of `dist/` is precached.

## Principles

1. **One way to do each thing.** If a button can be primary or
   secondary, those are the only two. A request for a third style starts
   with updating this document, not with a one-off `className`.
2. **Compose, don't customize.** Build a panel out of `<Card>` +
   `<Badge>` + the existing typography utilities rather than a one-off
   layout.
3. **Every interactive element is keyboard-reachable and screen-reader
   labelled.** ARIA discipline is enforced by axe-core; see the a11y
   contract.
4. **Mobile is the baseline, not an afterthought.** See *Mobile-first
   commitments* below — these are checked in code, not policy.
5. **Tokens, not magic numbers.** Colors are OKLCH custom properties;
   spacing is the Tailwind 4-pt scale. Hex, rgb, and arbitrary px values
   in component code are reviewer pushback.

## Mobile-first commitments

These are baked into `ui/src/styles/*.css` and the shadcn wrappers — not
optional:

- **44 px minimum touch target** on every interactive element. The
  shadcn `<Button>` and `<IconButton>` defaults already meet this; new
  affordances must too.
- **`@media (hover:hover)` guards every `:hover` rule.** No
  hover-stuck-on-touch. Use `:active` / `data-state` for tap feedback.
- **`-webkit-tap-highlight-color: transparent`** globally — we draw our
  own focus + active states.
- **Safe-area insets** via `env(safe-area-inset-*)` on the AppShell so
  the bottom nav clears the home indicator and the top nav clears the
  notch.
- **16 px input font-size** to suppress iOS Safari auto-zoom on focus.
  Never override down to a smaller `text-sm` on `<input>`/`<textarea>`.
- **Bottom nav at `<= sm`, sidebar at `>= md`.** Same routes, two
  presentations; pick which one is visible via Tailwind responsive
  modifiers, never via JS branching.

## Theme tokens

Tokens are declared once in `ui/src/styles/tokens.css` under
`@theme { ... }`. Tailwind v4 reads them and emits matching utilities
(`bg-bg`, `text-fg`, `border-border`, etc.). Never hardcode colors or
sizes outside what's defined here.

The palette is **OKLCH** with a pinned hue strategy:

- **Neutrals** (`--bg`, `--bg2`, `--bg3`, `--bg4`, `--fg`, `--fg2`,
  `--fg3`, `--border`) — hue **270** (cool indigo-leaning), perceptually
  uniform lightness ramp.
- **Accent / success** (`--accent`, `--success`) — hue **150** (cool
  green). `--accent` and `--success` share the hue intentionally; they
  are matched by *intent*, not by name.
- **Status** (`--warning`, `--error`, `--info`) — distinct hues, all
  declared in OKLCH so dark/light variants are computed by lightness
  flip rather than re-eyeballed.

Light/dark are toggled by `next-themes` setting `data-theme="dark"` on
`<html>`; tokens.css declares both `:root` and `[data-theme="dark"]`
sets. Color contrast is verified at WCAG 2 AA minimums in **both**
themes — the a11y ratchet asserts this.

Radii (`--radius-sm/md/lg`), the 4-pt spacing scale, and the typography
ramp are likewise tokens; refer to `tokens.css` for the canonical list.

## Components

The shipped surface lives under `ui/src/components/`:

- **AppShell** (`AppShell.tsx`) — top bar (brand, theme toggle, user
  menu), responsive sidebar (`>=md`) / bottom nav (`<sm`), main content
  region with a `#main-content` anchor for the skip link, toaster slot.
- **CommandPalette** — cmdk-backed, `Cmd-K`/`Ctrl-K`. Every route is a
  command; mutating actions (reconcile, enforce, revoke) are commands
  too, gated by role.
- **UserMenu** — Radix DropdownMenu wrapper. Logout, profile, settings.
- **shadcn primitives** under `ui/src/components/ui/` — `Button`,
  `Card`, `Badge`, `Input`, `Select`, `Dialog`, `Drawer`, `Sheet`,
  `Table`, `Tooltip`, `Popover`, `Skeleton`, `Tabs`. These are vendored
  (copied from shadcn), not a runtime dependency. Edit the local copy
  if you must extend; never inline-style around a deficiency.

For numeric/data UI (the media-integrity tab in particular):

- **StatusOverview** — animated bytes-counter cards.
- **AdapterTable** — TanStack Table on `>=md`, card-list fallback on
  `<sm`. Same data, two layouts.
- **NeedsReviewPanel** — list of duplicate-review items with optimistic
  resolve via TanStack Query mutations.
- **ReconcileButton** / **EnforceButton** — primary action buttons that
  POST to `/api/media-integrity/{reconcile,enforce-config}` and surface
  a Sonner toast on settle.
- **ProgressBar** — shimmer animation; respects
  `prefers-reduced-motion`.

## Routes

File-based via TanStack Router. The shipped surface as of v1.1.0:

- `/` — landing.
- `/media-integrity` — the showcase tab (StatusOverview, AdapterTable,
  NeedsReviewPanel, Reconcile/Enforce buttons, ProgressBar). See
  [media-integrity.md](media-integrity.md).
- `/content`, `/logs`, `/ops`, `/routing`, `/webhooks`, `/users`, `/me`
  — operator surfaces.
- `/profile`, `/settings` — placeholders pending feature work.
- `$` — 404 catchall.

Every `/api/*` literal called by these routes must round-trip through
the generated OpenAPI types — the path-contract check fails the build
otherwise.

## ARIA + keyboard discipline

Required on every interactive element:

- **Buttons**: native `<button>`, never a `<div onClick>`. Icon-only
  buttons carry `aria-label`.
- **Dialogs / Drawers / Sheets**: `Dialog.Title` and `Dialog.Description`
  are mandatory. The a11y ratchet asserts every modal in the bundle
  carries both.
- **Tabs / Menus / Listbox**: Radix primitives only; do not roll your
  own.
- **Live regions**: Sonner handles toasts (polite). For inline status,
  use `role="status"` (polite) or `role="alert"` (assertive).
- **Focus**: Tailwind's `focus-visible:` ring on every focusable
  element. Never `outline-none` without an alternative ring.
- **Skip link**: AppShell renders a "Skip to main content" link as the
  first focusable element, targeting `#main-content`.

Keyboard:

- Tab reaches every actionable control in document order.
- `Cmd-K` / `Ctrl-K` opens the command palette.
- Esc closes any open Dialog / Drawer / Sheet / Popover (Radix default).
- Arrow keys move within Radix menus / tablists.

## Animation

- Framer Motion handles entrance/exit, layout shifts, and the
  StatusOverview bytes-counter.
- Every animation respects `prefers-reduced-motion: reduce` — the
  motion config short-circuits to `duration: 0` when set.
- No CSS-only animations on layout-affecting properties; animate
  `transform` / `opacity` only.

## Bug-fixes shipped in v1.1.0

Recorded here so future readers don't reintroduce them:

- The SPA hits **`/api/health`** (the canonical controller route).
  `/healthz` is reserved for the kubelet probe on the nginx
  container — see [ui-container.md](ui-container.md). A prior build
  conflated the two.
- The PWA PNG icons are regenerated from the master SVG via
  ImageMagick. A previous build shipped 0-byte PNGs; the manifest
  contract check now fails the build before that can recur.

## How to extend the system

When you need a visual that isn't here:

1. Open `docs/reference/ui-design-system.md` (this file) and add the new
   component: name, when to use, sample usage, allowed variants.
2. Implement it as a wrapper over a shadcn/Radix primitive where
   possible. If it's wholly new, place it under
   `ui/src/components/`; if it's a styled primitive, copy from
   shadcn rather than depending at runtime.
3. Add a Vitest unit test + at least one a11y assertion (axe-core
   on the rendered output, zero `serious`/`critical` violations).
4. Use it from one route; confirm `pnpm size` is still under
   budget.
5. Send the PR. Reviewers should push back if the new component
   overlaps with an existing one.

The bar for adding a new primitive is "this composition cannot be
expressed by combining what we already have." If a route wants "a
button that opens a menu", the answer is `<Button>` + Radix
`<DropdownMenu>` — not a new `<MenuButton>`.
