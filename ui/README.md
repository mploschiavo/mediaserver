# Media Stack — UI

Luxury-tier React dashboard for the media automation stack. Built as a
static SPA, served by nginx inside the `media-stack-ui` container,
fronted by Envoy, talking to the Python controller via `/api/*`.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Framework | React 19 | Best-in-class ecosystem, Tanstack + shadcn compatibility |
| Language | TypeScript 5.x strict | Compile-time API contract enforcement |
| Build | Vite 6 | Fast HMR, native ESM, multi-chunk vendor split |
| Router | Tanstack Router | Type-safe routes, file-based, first-class layouts |
| Data | Tanstack Query | Caching, optimistic updates, retry policy, devtools |
| Tables | Tanstack Table | Virtualized, sortable, mobile-card fallback via `ResponsiveTable` |
| Styling | Tailwind CSS v4 | Config-as-CSS via `@theme`, OKLCH palette, smaller bundle |
| Components | shadcn/ui (copied) | Owned, not a dependency |
| Icons | Lucide | Consistent line-icon set |
| Motion | Framer Motion | Spring physics, layout animations, honor `prefers-reduced-motion` |
| Command palette | cmdk | Power-user keyboard-first nav |
| Toasts | Sonner | Non-jarring, slide-in |
| Drawers | Vaul | Mobile bottom-sheet |
| Fonts | Geist Sans + Geist Mono | Variable, premium feel |
| Testing (unit) | Vitest + happy-dom | Fast, vite-native, jest-dom matchers |
| Testing (e2e) | Playwright | Cross-browser, mobile projects |
| PWA | vite-plugin-pwa | Manifest + service worker + install prompt |
| Package mgr | pnpm 10.x via corepack | Deterministic, disk-efficient |

## Development

```
cd ui
pnpm install
pnpm dev         # http://127.0.0.1:5173
```

`pnpm dev` proxies `/api/*` to `VITE_API_PROXY` (default
`http://127.0.0.1:9100` — your local controller). Override for
staging: `VITE_API_PROXY=https://staging.example.com pnpm dev`.

## Testing

```
pnpm typecheck          # tsc -b --noEmit
pnpm test               # vitest run
pnpm test:watch         # vitest watch
pnpm test --coverage    # line + branch + function coverage
pnpm test:e2e           # playwright (webServer auto-starts `pnpm dev`)
pnpm storybook          # component playground on http://127.0.0.1:6006
```

Coverage thresholds in `vitest.config.ts`: **lines ≥ 85, statements ≥ 85,
branches ≥ 75, functions ≥ 80**. Entrypoints (`main.tsx`, `App.tsx`,
`routes/**`, `routeTree.ts`) are excluded from coverage — they're
exercised by Playwright e2e tests instead.

## Directory layout

```
ui/
├── src/
│   ├── api/               # typed fetcher, shapes, Tanstack Query hooks
│   │   ├── types.ts       # AUTO-GENERATED from openapi.yaml (pnpm gen:api)
│   │   ├── shapes.ts      # hand-typed shapes mirroring the Python service layer
│   │   ├── client.ts      # fetcher<T>, ApiError, auth event bus
│   │   ├── endpoints.ts   # typed endpoint surface (api.X.Y.Z)
│   │   ├── hooks.ts       # useXxx Tanstack Query hooks
│   │   └── query-client.ts
│   ├── components/
│   │   ├── ui/            # shadcn atoms (button, card, chip, table, …)
│   │   └── layout/        # shell pieces (AppShell, Sidebar, TopBar,
│   │                      #               BottomNav, CommandPalette,
│   │                      #               ErrorBoundary, ResponsiveTable,
│   │                      #               PullToRefresh, EmptyState, …)
│   ├── features/          # feature folders — the tab showcases
│   │   └── media-integrity/
│   ├── hooks/             # reusable hooks (usePullToRefresh, useSwipeToOpenSidebar)
│   ├── lib/               # small pure helpers (cn, keyboard, pwa, idempotency, touch-detect)
│   ├── routes/            # Tanstack Router file-based routes
│   ├── styles/
│   │   └── globals.css    # Tailwind v4 @theme + OKLCH palette + global reset
│   ├── test/              # test helpers (renderWithProviders, setup.ts)
│   ├── App.tsx
│   ├── main.tsx
│   └── routeTree.ts       # hand-composed route tree (replace with codegen later)
├── tests/
│   └── e2e/               # Playwright specs + _mocks.ts
├── public/
│   └── icons/             # PWA icons (SVGs + rasterized PNGs)
├── .storybook/
├── package.json
├── vite.config.ts
├── vitest.config.ts
├── playwright.config.ts
├── tsconfig.json
├── tailwind.config.ts     # (Tailwind v4: config lives in globals.css; this file is optional)
└── README.md              # you are here
```

## Design system

See [`docs/ui-design-system.md`](../docs/ui-design-system.md) for the
contract. Key rules:

- **One way to do each thing.** Three button variants, five chip
  variants, one card, one table — that's it.
- **No inline styles in shipped HTML or JS.** Narrow allowlist in
  the ratchet. Drift is reviewed, not assumed.
- **Mobile-first.** Design for iPhone-15 (393px) first; enhance at
  `sm:` and up. 44px touch-target floor. Bottom nav on mobile, sidebar
  on desktop. Hover states gated behind `[@media(hover:hover)]:` so
  touch taps don't trigger stuck-hover. Input font-size 16px on
  mobile to prevent iOS zoom-on-focus.
- **Honor `prefers-reduced-motion`.** All animations short-circuit.

## Luxury features

- **Command palette** — ⌘K (Cmd+K on Mac / Ctrl+K elsewhere) opens a
  fuzzy-search dialog with every route + every admin action + the 5
  most-recently visited routes. `g m` jumps to Media Integrity;
  `g l` to Logs; etc.
- **Type-safe API** — every fetch returns `Promise<T>` where `T` is
  generated from `contracts/.../openapi.yaml`. Rename a field in the
  OpenAPI spec → TypeScript build fails until the caller is updated.
- **Optimistic mutations** — the "Keep this one" button on a needs-
  review duplicate updates the list instantly, rolls back on failure.
- **Framer Motion everywhere** — tasteful 200ms ease-out entrances,
  animated bytes-freed counter, progress-bar shimmer during a pass,
  dry-run label morph.
- **PWA** — add-to-home-screen on iOS/Android, offline app shell,
  three quick-action shortcuts in the home-screen icon long-press
  menu (Media Integrity / Logs / Reconcile now).
- **Connection-status dot** — pulls `/api/healthz` every 10s; tri-
  state (live/degraded/dead) with tooltip showing last-seen age.
- **Pull-to-refresh** on mobile; pull >80px to trigger a Tanstack
  Router `router.invalidate()` (re-runs every loader on the active
  route).
- **Swipe-to-open sidebar** on mobile; left-edge swipe right opens
  the Vaul drawer.

## Build & deploy

The UI ships in the `media-stack-ui` container, built via a two-stage
Dockerfile at `docker/ui.Dockerfile`:

```
bin/build-ui-image.sh        # builds + pushes harbor.iomio.io/library/media-stack-ui:v$VERSION-UI
bin/build-ui-image.sh --no-push
```

Deploy via the existing stack flow: `kubectl set image` for k8s,
`docker compose up -d media-stack-ui` for compose. Versioned
independently from the API/controller via `VERSION-UI` at the repo
root.

## Contributing

1. Run `pnpm typecheck && pnpm test` before every commit.
2. If you add a visual primitive, document it in
   `docs/ui-design-system.md` AND update the ratchet's allowed
   vocabulary.
3. If you add an API endpoint to the controller, regenerate types:
   `pnpm gen:api` (reads `contracts/.../openapi.yaml`).
4. If you add a new route, add a Playwright smoke test in
   `tests/e2e/smoke.spec.ts` asserting it loads without JS errors.
