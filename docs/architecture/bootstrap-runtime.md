# Bootstrap Runtime

Two things drive every install:

1. **The bootstrap profile** (`contracts/media-stack.profile.yaml`) — a single YAML file that declares what to install, where, and how to wire it.
2. **The bootstrap execution** — the controller reads the profile, applies the per-service contracts, and runs idempotent wiring jobs until the stack matches the declared desired state.

This page documents both.

## The bootstrap profile

`contracts/media-stack.profile.yaml` is the canonical deployment profile for rebuild/install defaults. It's the single file that defines a deployment:

| Section | Purpose |
|---|---|
| `metadata.platform` | `compose` or `k8s` |
| `metadata.purpose` | environment label (e.g. `dev`, `prod`) |
| `metadata.name` | stack identity |
| `resources.*` | storage + network intent |
| `install_profile` | install tier (`minimal` / `standard` / `full`) |
| `apps` | optional per-app overrides on top of the tier |
| `bootstrap` | execution behavior (preconfigure, indexer sync, health refresh) |
| `technology_bindings` | which implementation runs each role |
| `routing` + `auth` | gateway + auth posture |
| `app_auth` | per-service passwords (Sonarr, Radarr, etc.) |
| `chaos` | optional chaos-recovery test window |
| `live_tv_defaults` | Live TV source URLs |

### Files

- Profile: `contracts/media-stack.profile.yaml`
- Schema: `contracts/media-stack.profile.schema.json`
- Validator: `bash bin/utils/validate-bootstrap-profile.sh`
- Example set: `examples/bootstrap-profiles/`

### Canonical example

```yaml
schema_version: 1
kind: media_stack_profile

metadata:
  name: media-dev
  platform: compose
  purpose: dev

resources:
  disk_space_gb: 50
  network_cidr: 192.168.1.0/24

install_profile: standard

apps:
  lidarr: false
  readarr: false
  tautulli: false

bootstrap:
  preconfigure_apps: true
  preconfigure_api_keys: true
  apply_initial_preferences: true
  auto_download_content: false
  trigger_indexer_sync: true
  refresh_health_after_setup: true

# Role-to-technology mappings — swap implementations here
technology_bindings:
  torrent_client: qbittorrent
  usenet_client: sabnzbd
  media_server: jellyfin
  request_manager: jellyseerr
  indexer_manager: prowlarr

routing:
  internet_exposed: false
  strategy: hybrid
  provider: traefik
  base_domain: local
  stack_subdomain: media-dev
  gateway_host: apps.media-dev.local
  app_path_prefix: /app
  direct_hosts:
    media_server: jellyfin.media-dev.local

auth:
  enabled: false
  provider: none

# Service-level auth (passwords on Sonarr, Radarr, etc.)
app_auth:
  enabled: true
  method: Forms
  required: DisabledForLocalAddresses
  fail_on_error: false
  username_env: STACK_ADMIN_USERNAME
  password_env: STACK_ADMIN_PASSWORD

chaos:
  enabled: false
  duration_minutes: 5
  interval_seconds: 60
  actions:
    - restart_container
    - pause_container
    - network_disconnect

live_tv_defaults:
  tuner_url: https://iptv-org.github.io/iptv/countries/us.m3u
  guide_url: https://iptv-epg.org/files/epg-us.xml
  default_program_icon_urls:
    - https://raw.githubusercontent.com/iptv-org/logo/master/tv.png
```

### Install tiers

| Tier | Apps |
|---|---|
| `minimal` | Jellyfin, Jellyseerr, Prowlarr, qBittorrent, Homepage |
| `standard` | `minimal` + Sonarr, Radarr, Lidarr, Readarr, Bazarr, SABnzbd, Tautulli, Maintainerr, Unpackerr, FlareSolverr + Envoy + controller |
| `full` | All supported apps (adds Plex) |

Use `apps:` for explicit overrides on top of the tier.

#### Auto-download policy by tier

- `minimal` and `standard` default to manual content mode (`auto_download_content: false`).
- `full` defaults to automatic content mode (`auto_download_content: true`).
- All tiers can still preconfigure API keys and initial app preferences.
- `auto_download_content` can be toggled at runtime: `POST /config {"auto_download_content": true}`.

### Routing provider keys

- canonical values: `traefik`, `envoy`
- set under `routing.provider`

### Chaos keys

- `chaos.enabled` — default `false`
- `chaos.duration_minutes` — default `5`
- `chaos.interval_seconds` — default `60`
- `chaos.actions` — `restart_container`, `pause_container`, `network_disconnect`

### Profile matrix

| Profile | File |
|---|---|
| Compose minimal | `examples/bootstrap-profiles/media-compose-minimal.yaml` |
| Compose standard | `examples/bootstrap-profiles/media-compose-standard.yaml` |
| Compose full | `examples/bootstrap-profiles/media-compose-full.yaml` |
| K8s minimal | `examples/bootstrap-profiles/media-k8s-minimal.yaml` |
| K8s standard | `examples/bootstrap-profiles/media-k8s-standard.yaml` |
| K8s full | `examples/bootstrap-profiles/media-k8s-full.yaml` |

### Live TV defaults used in code

These profile defaults match the Jellyfin per-service YAML defaults (`contracts/services/jellyfin.yaml` `livetv` section):

- Tuner playlist: `https://iptv-org.github.io/iptv/countries/us.m3u`
- XMLTV guide: `https://iptv-epg.org/files/epg-us.xml`
- Default icon: `https://raw.githubusercontent.com/iptv-org/logo/master/tv.png`

### CLI integration

The deploy runners auto-load `contracts/media-stack.profile.yaml` when present. Override path:

```bash
bash bin/deploy-stack.sh --bootstrap-profile-file /path/to/profile.yaml
```

Quick validation:

```bash
bash bin/utils/validate-bootstrap-profile.sh
bash bin/utils/validate-bootstrap-profile.sh --config examples/bootstrap-profiles/media-k8s-full.yaml
```

---

## Bootstrap execution

### Fastest path

```bash
bash bin/fast-first-run.sh <NODE_IP>
```

### Full zero-to-usable

```bash
bash bin/deploy-stack.sh <NODE_IP>
bash bin/install.sh --profile full --node-ip <NODE_IP>
```

### Step-by-step (for debugging)

```bash
bash bin/set-qbit-secret.sh
bash bin/ensure-qbit-credentials.sh
bash bin/ensure-sabnzbd-api-access.sh
# optional override; otherwise auto-discovered from Jellyfin DB
bash bin/set-jellyfin-api-key.sh <JELLYFIN_API_KEY>
bash bin/run-bootstrap-job.sh
bash bin/sync-unpackerr-keys.sh
bash bin/run-prowlarr-auto-indexers.sh
bash bin/bootstrap-all.sh
```

Indexers are configured via Prowlarr auto-discovery (`bin/run-prowlarr-auto-indexers.sh`) or the controller dashboard at `http://localhost:9100/`.

### What bootstrap configures

#### qBittorrent categories

`tv`, `movies`, `music`, `books`.

#### Arr root folders + downloader URLs

| Arr | Root folder |
|---|---|
| Sonarr | `/media/tv` |
| Radarr | `/media/movies` |
| Lidarr | `/media/music` |
| Readarr | `/media/books` |

| Downloader | URL |
|---|---|
| qBittorrent | `http://qbittorrent:8080` |
| SABnzbd | `http://sabnzbd:8080` |

#### Reconciled out-of-the-box

- Arr download clients for qBittorrent are reconciled.
- Arr download clients for SABnzbd are reconciled when `download_clients.sabnzbd.configure_arr_clients=true`.
- SAB API key is read from `SABNZBD_API_KEY` when set, or auto-discovered from `sabnzbd/sabnzbd.ini`.
- SAB API access guardrails (`host_whitelist` / `local_ranges`) so Arr pods can test/connect.
- SAB defaults: `download_dir=/data/usenet/incomplete`, `complete_dir=/data/usenet/completed`, `auto_browser=0`.
- SAB categories (`tv` / `movies` / `music` / `books`) reconciled to explicit dirs under `/data/usenet/completed/<category>`.
- Arr remote path mappings reconciled for SAB legacy paths (`/config/Downloads/complete` → `/data/usenet/completed`).
- Arr media-management hardlinks enforced (`copyUsingHardlinks=true`) to avoid copy-on-import bloat.
- Sonarr default `createEmptySeriesFolders` enforced.
- Sonarr / Radarr quality profile preference defaults to 1080p with 720p fallback.
- Radarr discovery lists: TMDb Trending, Popular, Top Rated, Upcoming Movies.
- Sonarr Trakt discovery lists reconciled when `TRAKT_ACCESS_TOKEN`, `TRAKT_REFRESH_TOKEN`, `TRAKT_USERNAME` are set.
- Homepage services config generated from ingress host rules.
- Homepage device-onboarding cards (Jellyfin / Jellyseerr QR setup links + short links, Samsung TV, Vizio, TCL quick-start cards).
- Bazarr Sonarr / Radarr wiring reconciled.

#### Jellyfin tuning

- Audio / subtitle language defaults + subtitle mode.
- Next-episode autoplay + remembered selection behavior.
- Metadata / artwork-oriented server defaults (language / region + trickplay defaults).
- Display preference defaults for backdrop / theme-video-style browsing views.
- Library tuning: TMDb-first metadata provider ordering (+ Fanart promotion when available), artwork profile limits, realtime monitoring, preview thumbnail extraction for Movies / TV, auto library refresh after tuning.
- Curated `BoxSet` rails are **disabled by default** to avoid clunky collection-first navigation. If previously-created synthetic rails exist (`Trending`, `Top Rated`, etc.), bootstrap cleanup removes them when `jellyfin_home_rails.cleanup_collections_when_disabled=true`.

#### Jellyseerr

Sonarr, Radarr, and Jellyfin connections configured by bootstrap. Local Jellyseerr admin seeded from `STACK_ADMIN_USERNAME` / `STACK_ADMIN_PASSWORD`.

### End-to-end flow

1. User requests content in Jellyseerr.
2. Sonarr / Radarr / Lidarr / Readarr search via Prowlarr indexers.
3. Arr sends release to qBittorrent or SABnzbd.
4. Completed Download Handling (CDH) imports and renames into `/media/*`.
5. Jellyfin sees imported files in configured libraries.

### Per-app outcome

| App | Outcome |
|---|---|
| Sonarr | `/media/tv` → Jellyfin TV libraries |
| Radarr | `/media/movies` → Jellyfin Movie libraries |
| Lidarr | `/media/music` → Jellyfin Music libraries |
| Readarr | `/media/books` → audiobook/book workflow (ebook-focused browsing usually in a dedicated reader app) |
| Bazarr | Subtitles for imported TV / movies |

### Downloader note

qBittorrent and SABnzbd are transport clients. They don't render media in Jellyfin directly — media appears in Jellyfin after Arr imports into library folders. Bootstrap enforces CDH for Arr apps and wires Jellyfin libraries for Movies / TV / Music / Books.

Jellyfin Auto Collections is deployed OTB with a safe default config; add list sources in `contracts/services/jellyfin.yaml` (`jellyfin_auto_collections.plugins`) for curated auto-generated collections. Curated home rails can be enabled via `jellyfin_home_rails.enabled=true`; default behavior favors native home UX.

### Troubleshooting

If bootstrap appears stuck or partially applied:

```bash
MEDIA_STACK_LOG_LEVEL=DEBUG bash bin/bootstrap-all.sh --no-resume
bash bin/stack-status.sh
bash bin/verify-flow.sh
```

`bin/verify-flow.sh` includes writable-path checks to catch permission / path mismatches early.

If `jellyfin.local` still opens `/web/#/wizard/start`:

```bash
bash bin/ensure-jellyfin-bootstrap.sh
bash bin/bootstrap-all.sh
```

Then retry in a private/incognito browser session to avoid stale local UI state.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
