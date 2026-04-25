# Media Stack

Self-hosted media server (Jellyfin) + automation pipeline (Sonarr / Radarr / Lidarr / Readarr / Prowlarr / qBittorrent / Bazarr / Maintainerr / Jellyseerr) packaged as a single declarative unit. Run one command on Docker Compose or Kubernetes; a controller wires every service together — auth (Authelia SSO), edge gateway (Envoy + TLS), indexers, quality profiles, download clients, library paths, subtitles. **Users only interact with Jellyseerr (request) and Jellyfin (watch).**

Nothing about the stack requires per-app clicks. The controller's promises registry guarantees the same behavior survives a wipe-and-redeploy.

## Quick install

```bash
git clone https://github.com/mploschiavo/mediaserver.git && cd mediaserver

# Easiest — Docker Compose
./deploy-compose.sh

# Or Kubernetes (MicroK8s + ingress class "public" assumed)
./deploy-k8s.sh

# Cross-platform Python launcher
python deploy.py compose       # or: python deploy.py k8s
```

Bootstrap takes 3-5 minutes the first time. Open the controller dashboard at <http://localhost:9100> to watch progress, then point a browser at Jellyseerr to request your first show.

## Where to read next

| You want to... | Go to |
|---|---|
| **Deploy and use the stack** | [Quickstart](docs/tutorials/quickstart.md) |
| **Understand all deployment options** | [Deployment](docs/how-to/deployment.md) |
| **Maintain a running stack** | [Operations](docs/how-to/operations.md) + [Troubleshooting](docs/how-to/troubleshooting.md) |
| **Extend the code or add a service** | [internals/principles.md](docs/architecture/principles.md), then [internals/architecture.md](docs/architecture/overview.md) |
| **Look something up** | [reference/](docs/reference/) |
| **Report a bug or contribute** | [CONTRIBUTING.md](CONTRIBUTING.md) |

The full doc index lives at [docs/README.md](docs/README.md).

## What "out of the box" actually means

Things that work without any clicking after `up -d`:

- Jellyfin libraries (Movies / TV / Music / Books) wired to `/media/*`.
- Sonarr / Radarr / Lidarr / Readarr connected to qBittorrent + Prowlarr indexers + path mappings.
- Bazarr subtitles for new TV / movies (curated provider set, English profile by default).
- Jellyseerr connected to all four Arr apps + Jellyfin (with Authelia SSO when auth is enabled).
- Maintainerr collection rules linked to the right Arr apps.
- Envoy gateway with self-signed TLS on `:443`, HTTP→HTTPS redirect on `:80`.
- 33 OTB promises, each verified by a probe against the live stack — see [reference/promises.md](docs/reference/promises.md).

## Dashboard

The controller dashboard at `:9100` is a React 19 SPA (Vite 6 + Tailwind v4 + shadcn/ui + Tanstack Router/Query/Table + Framer Motion + cmdk + Sonner + Geist Variable). Mobile-first (44px touch-target floor, safe-area insets, bottom nav) with light/dark OKLCH theming via `next-themes`.

Installable as a PWA — service worker is NetworkOnly for `/api/*`, CacheFirst for the Geist CDN, with home-screen shortcuts for Media Integrity, Logs, and Reconcile now. ⌘K opens the in-app command palette; `ConnectionStatus` polls `/api/health`. Auth is unchanged: cookies from Authelia, validated by Envoy `ext_authz`, UI sends `credentials: "same-origin"`.

Quality ratchets enforced in CI: `pnpm size` (250 KB total JS gzip ceiling, currently 240.8 KB), `pnpm check:todos` (snapshot at `.ratchets/todos.json`), `pnpm lint` (flat ESLint locks `no-console` / `no-only-tests` / `no-explicit-any` at 0), `vitest-axe` a11y on AppShell / CommandPalette / UserMenu / MediaIntegrity (blocks serious + critical), a path-contract test that every `/api/*` literal in `src/` exists in the OpenAPI spec, and a manifest contract that every PNG referenced from `dist/manifest.webmanifest` exists at the declared dimensions.

UI image: `harbor.iomio.io/library/media-stack-ui:v1.1.0` (see [CHANGELOG.md](CHANGELOG.md)).

## Project status

Active. The controller image is published as `harbor.iomio.io/library/media-stack-controller:vX.Y.Z`. Recent changes are in [CHANGELOG.md](CHANGELOG.md).

If you're going to deploy this in front of the internet, set `routing.internet_exposed: true` in your bootstrap profile. That disables the `STACK_ADMIN_PASSWORD` env-seed fallback, enforces the weak-password blocklist, and fails boot on well-known defaults. Home-LAN installs don't need any of that.

## Support the project

Media Stack is built and maintained on personal time. If it saves you a weekend of fiddling with Jellyfin / Sonarr / Radarr glue and you'd like to chip in, donations help keep the controller image building, the docs current, and new services landing.

<p align="center">
  <a href="https://www.paypal.com/donate?hosted_button_id=XKDG7XXVEQK3W" target="_blank">
    <img src="https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif" alt="Donate with PayPal" />
  </a>
</p>

No pressure — running it for free is the whole point. Stars on the repo and bug reports help just as much.

## Maintainer

Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)

## License

See [LICENSE](LICENSE).
