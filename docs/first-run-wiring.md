# First Run Wiring

Use helper script for fastest setup flow:
```bash
bash scripts/fast-first-run.sh <NODE_IP>
```

Full zero-to-usable automation:
```bash
bash scripts/rebuild-and-bootstrap.sh <NODE_IP>
bash scripts/install.sh --profile full --node-ip <NODE_IP>
```

Run declarative bootstrap first:
```bash
bash scripts/set-qbit-secret.sh
bash scripts/ensure-qbit-credentials.sh
bash scripts/ensure-sabnzbd-api-access.sh
# optional override; otherwise auto-discovered from Jellyfin DB:
bash scripts/set-jellyfin-api-key.sh <JELLYFIN_API_KEY>
bash scripts/run-bootstrap-job.sh
bash scripts/sync-unpackerr-keys.sh
bash scripts/run-prowlarr-auto-indexers.sh
bash scripts/bootstrap-all.sh
```
For indexers-as-code, add entries under `prowlarr_indexers` in `bootstrap/media-stack.bootstrap.json` using `bootstrap/prowlarr-indexers.example.json` as a reference, then re-run the bootstrap job.

## qBittorrent categories
- tv
- movies
- music
- books

## Arr root folders
- Sonarr -> /media/tv
- Radarr -> /media/movies
- Lidarr -> /media/music
- Readarr -> /media/books

## Download client URLs
- qBittorrent -> http://qbittorrent:8080
- SABnzbd -> http://sabnzbd:8080

Bootstrap behavior:
- Arr download clients for qBittorrent are reconciled OTB.
- Arr download clients for SABnzbd are also reconciled OTB when `download_clients.sabnzbd.configure_arr_clients=true`.
- SAB API key is read from `SABNZBD_API_KEY` when set, or auto-discovered from `sabnzbd/sabnzbd.ini`.
- SAB API-access guardrails are reconciled OTB (`host_whitelist`/`local_ranges`) so Arr pods can test/connect.
- SAB defaults are reconciled OTB (`download_dir=/data/usenet/incomplete`, `complete_dir=/data/usenet/completed`, `auto_browser=0`).
- SAB categories (`tv/movies/music/books`) are reconciled to explicit category directories under `/data/usenet/completed/<category>`.
- Arr remote path mappings are reconciled for SAB legacy paths (`/config/Downloads/complete` -> `/data/usenet/completed`).
- Arr media-management hardlinks are enforced (`copyUsingHardlinks=true`) to avoid copy-on-import bloat.
- Sonarr default `createEmptySeriesFolders` is enforced.
- Sonarr/Radarr quality profile preference defaults to 1080p with 720p fallback.
- Radarr discovery lists are reconciled OTB:
  - TMDb Trending Movies
  - TMDb Popular Movies
  - TMDb Top Rated Movies
  - TMDb Upcoming Movies
- Sonarr Trakt discovery list definitions are reconciled OTB when Trakt OAuth env vars are present (`TRAKT_ACCESS_TOKEN`, `TRAKT_REFRESH_TOKEN`, `TRAKT_USERNAME`).
- Homepage services config is generated from ingress host rules.
- Homepage includes device onboarding cards OTB:
  - Jellyfin/Jellyseerr QR setup links + short links
  - Samsung TV quick-start card
  - Vizio quick-start card
  - TCL quick-start card
- Bazarr Sonarr/Radarr wiring is reconciled in Bazarr config.
- Jellyfin playback defaults are reconciled OTB:
  - audio/subtitle language defaults + subtitle mode
  - next-episode autoplay + remembered selection behavior
  - metadata/artwork-oriented server defaults (language/region + trickplay defaults)
  - display preference defaults for backdrop/theme-video-style browsing views
- Jellyfin libraries are tuned OTB for UX:
  - TMDb-first metadata provider ordering (+ Fanart promotion when available)
  - artwork profile limits for logos/backdrops/thumbs
  - realtime monitoring enabled
  - preview thumbnail extraction enabled for Movies/TV
  - auto library refresh after tuning
- Jellyfin curated `BoxSet` rails are now **disabled by default** to avoid clunky
  collection-first navigation. Native Jellyfin home sections remain primary.
- If previously-created synthetic rails exist (`Trending`, `Top Rated`, etc.),
  bootstrap cleanup removes them when `jellyfin_home_rails.cleanup_collections_when_disabled=true`.

## Prowlarr app links
Bootstrap already links Sonarr, Radarr, Lidarr, and Readarr in Prowlarr.
You only need to add/enable indexers that pass tests.

## Jellyseerr
Sonarr, Radarr, and Jellyfin are configured by bootstrap.
Local Jellyseerr admin is seeded from `STACK_ADMIN_USERNAME` / `STACK_ADMIN_PASSWORD`.

## End-to-end flow checklist
1. Request content in Jellyseerr.
2. Sonarr/Radarr/Lidarr/Readarr search via Prowlarr indexers.
3. Arr sends release to qBittorrent or SABnzbd.
4. Completed Download Handling (CDH) imports and renames into `/media/*`.
5. Jellyfin sees imported files in configured libraries.

Per-app outcome map:
- Sonarr -> `/media/tv` -> Jellyfin TV libraries.
- Radarr -> `/media/movies` -> Jellyfin Movie libraries.
- Lidarr -> `/media/music` -> Jellyfin Music libraries.
- Readarr -> `/media/books` -> audiobook/book workflow (ebook-focused browsing is usually done in a dedicated reader app).
- Bazarr -> subtitles for imported TV/movies.

Downloader note:
- qBittorrent and SABnzbd are transport clients. They do not render media directly in Jellyfin.
- Media appears in Jellyfin after Arr import into library folders.
- Bootstrap enforces CDH for Arr apps and wires Jellyfin libraries for Movies/TV/Music/Books.
- Jellyfin Auto Collections is deployed OTB with a safe default config; add your own list sources in `bootstrap/media-stack.bootstrap.json` (`jellyfin_auto_collections.plugins`) if you want curated auto-generated collections.
- Curated Jellyfin home rails can be enabled as an advanced option via
  `jellyfin_home_rails.enabled=true`; default behavior favors native home UX.

If `jellyfin.local` still opens `/web/#/wizard/start`:
```bash
bash scripts/ensure-jellyfin-bootstrap.sh
bash scripts/bootstrap-all.sh
```
Then retry in a private/incognito browser session to avoid stale local UI state.

## Troubleshooting
If bootstrap appears stuck or partially applied:
```bash
bash scripts/bootstrap-debug.sh
bash scripts/stack-status.sh
bash scripts/verify-flow.sh
```
`scripts/verify-flow.sh` now includes writable-path checks to catch permission/path mismatches early.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
