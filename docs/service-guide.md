# Service Guide

## Jellyfin
Primary media server. Reads finalized media from `/media/*` and renders it to clients.

## Jellyseerr
Request UI. Sends movie/show requests to Radarr/Sonarr and shows availability from Jellyfin.

## Prowlarr
Central indexer manager. Sonarr/Radarr/Lidarr/Readarr receive indexers from Prowlarr app links.

## Sonarr / Radarr / Lidarr / Readarr
Automation managers for TV, movies, music, and books.
They search via Prowlarr, send downloads to qB/SAB, then import into `/media/*`.
Bootstrap also enforces CDH + hardlink-friendly media management and quality-profile preference (1080p then 720p fallback for Sonarr/Radarr), plus quality-upgrade lifecycle stop conditions (default blocks 4K tiers).
Radarr TMDb discovery lists are configured OTB for self-filling libraries (Trending/Popular/Top Rated/Upcoming).
They are not full library groomers for stale/old content; for deeper lifecycle pruning use a policy tool such as Maintainerr.

## Bazarr
Subtitle automation.
Bootstrap wires Bazarr to Sonarr and Radarr via Bazarr config-as-code.
Bazarr does not integrate with Lidarr/Readarr (music/books) because subtitle automation is for movies/TV content.
Bazarr also does not directly connect to qBittorrent or SABnzbd; download client wiring remains in the Arr apps.

## qBittorrent
Torrent downloader. Receives jobs from Arr and stores into `/data/torrents/*`.
It is not a Jellyfin library source directly.
This stack can enforce seeding limits and cleanup behavior from config-as-code (ratio/time + optional disk guardrails).
It also supports scheduled IP blocklist refresh (`media_hygiene.qbit_ipfilter`) with cached fallback if the upstream list is temporarily unavailable.

## SABnzbd
Usenet downloader. Receives jobs from Arr and stores into `/data/usenet/*`.
It is also not a Jellyfin library source directly.

## Grooming / Retention Layer
Recommended best-practice split:
- Arr stack: acquisition and import orchestration.
- qB/SAB: transport lifecycle cleanup.
- Dedicated groomer policy tool (Maintainerr): library retention based on watch/use rules.

In other words, no single Arr app is the full groomer. Use the layered approach above.

This stack ships:
- downloader-side cleanup defaults (CDH + qB seeding/cleanup policy)
- disk-usage guardrails (`disk_guardrails` in `bootstrap/media-stack.bootstrap.json`, default max 65% used on `/srv-stack/media`)
- scheduled media hygiene (`media_hygiene`) for failed queue cleanup + temp/orphan cleanup
- Jellyfin prewarm schedule (`jellyfin_prewarm`) for recurring metadata/artwork + guide/channel refresh
- Maintainerr app route (`maintainerr.<domain>`) with persistent config (`/opt/data`)
- Maintainerr policy-as-code file generation (`maintainerr` -> `/srv-config/maintainerr/policy.json`)

## Unpackerr
Watches completed download folders and unpacks for the arr apps.
Bootstrap now syncs Arr API keys into secret config and restarts Unpackerr automatically when the deployment is active (replicas > 0).

## Homepage
Single-pane dashboard.
Bootstrap generates Homepage `services.yaml` from active ingress hosts so app links are auto-populated.

## Maintainerr
Retention policy UI and API for media lifecycle operations.
Deployed in full/public-demo/power-user profiles and exposed at `maintainerr.<domain>`.
Bootstrap now reconciles Maintainerr main settings (`applicationUrl`, media-server type, Jellyfin URL/API key/user, Seerr URL)
plus integrations for Radarr, Sonarr, Jellyseerr, and Tautulli with API-level test calls.
Policy rules are maintained as a config-as-code library (one JSON file per rule) under
`scripts/bootstrap_defaults/maintainerr_rules/` and rendered to `/srv-config/maintainerr/policy.json`.

## Traefik
Reverse proxy for nice local hostnames.

## Plex / Tautulli / FlareSolverr
Optional extras.
When FlareSolverr is enabled in bootstrap config, bootstrap reconciles a Prowlarr FlareSolverr indexer proxy and runs a proxy connection test automatically.
