# Indexer pipeline — how a fresh install starts grabbing

This document exists because the same chain has been re-debugged many
times. If qBittorrent is at "0 active" on a fresh install, the bug is
ALMOST CERTAINLY in one of the stages below. Walk them in order.

The chain that turns "user installs the stack" into "qBit downloads a
file" runs through five stages. Each stage has a known failure mode
and a known one-liner to verify it.

```
┌────────────────┐     ┌────────────────┐     ┌─────────────────┐     ┌────────────┐     ┌──────┐
│ discover-      │ →   │ tag-indexers-  │ →   │ push-indexers   │ →   │  *arr      │ →   │ qBit │
│ indexers       │     │ for-apps       │     │ (Application-   │     │ search +   │     │      │
│ (Prowlarr)     │     │ (Prowlarr tags)│     │ IndexerSync)    │     │ grab       │     │      │
└────────────────┘     └────────────────┘     └─────────────────┘     └────────────┘     └──────┘
```

## Stage 1 — discover-indexers (Prowlarr)

**What it does.** Iterates Prowlarr's Cardigann definition list (~70),
tests each via `POST /api/v1/indexer/test`, then `POST /api/v1/indexer`
to create the ones that pass. Filtered by
[contracts/curated-indexers.yaml](../contracts/curated-indexers.yaml)
to ~28 known-reliable public trackers (the full set is mostly dead).

**Where it lives.**
- Adapter: [job_adapters.py](../src/media_stack/services/apps/core/job_adapters.py) `discover_indexers`
- Implementation: [reputation_ops.py](../src/media_stack/services/apps/prowlarr/reputation_ops.py) `auto_add_tested_indexers`

**Job declaration.** [contracts/services/core.yaml](../contracts/services/core.yaml):
`discover-indexers` — `non_blocking: true` (8–14 min cold).

**Verify.**

```bash
PK=$(docker exec prowlarr cat /config/config.xml | grep -oP '(?<=<ApiKey>)[^<]+')
curl -s -H "X-Api-Key: $PK" http://localhost:9696/app/prowlarr/api/v1/indexer | jq 'length'
# Expect: 5–15 (the curated allowlist, after CF rejections).
```

**Common failures.**

| Symptom | Cause | Fix |
| --- | --- | --- |
| 0 indexers | Prowlarr not reachable, or curated YAML mode=allowlist with empty list | Check `mode:` and `categories:`/`allowed:` in the YAML |
| `[FAIL] X: create failed (HTTP 409) UNIQUE constraint failed: Indexers.Name` | Two parallel workers raced on the same `(impl, name)` | Already fixed: dedup at workload-build time + treat 409 as benign (v1.0.139) |
| `[SKIP] X: CloudFlare-blocked (no FlareSolverr proxy configured)` | FlareSolverr proxy ID was `None` when discovery ran | The proxy create raced with itself. Re-run discover-indexers — second run finds the existing proxy. Long-term: `proxy_ops.ensure_flaresolverr_proxy` needs a single-flight guard |

## Stage 2 — tag-indexers-for-apps (Prowlarr)

**What it does.** For each indexer × each `*arr` app, probes whether
the indexer returns any results for that app's content. Tags matching
indexers with `sync-{app}`. Then sets each app's `tags` field so
ApplicationIndexerSync only pushes matching ones.

**Where it lives.**
- Adapter: [job_adapters.py](../src/media_stack/services/apps/core/job_adapters.py) `tag_indexers_for_apps`
- Probe + tag: [indexer_app_match.py](../src/media_stack/services/apps/prowlarr/indexer_app_match.py)

**Job declaration.**
```yaml
tag-indexers-for-apps:
  non_blocking: true
  after: [discover-indexers]   # MUST wait or it tags 0 indexers.
```

**Verify.**

```bash
PK=$(docker exec prowlarr cat /config/config.xml | grep -oP '(?<=<ApiKey>)[^<]+')
curl -s -H "X-Api-Key: $PK" http://localhost:9696/app/prowlarr/api/v1/indexer | \
  jq -r '.[] | "\(.name)\ttags=\(.tags)"'
# Expect: each indexer has 1+ tag IDs.
```

**Common failures.**

| Symptom | Cause | Fix |
| --- | --- | --- |
| All indexers have `tags=[]` | Tag job ran BEFORE discover finished | `after: [discover-indexers]` in the contract enforces this. Without `after:`, the dispatcher reports `non_blocking` jobs "done" the instant they spawn the daemon thread, downstream peers race |
| `X → no app match (capability claims didn't survive probe)` for a tracker that obviously carries the content | The probe used an empty `query=` string (older versions). Some Cardigann defs return 0 for empty-query + category filter | Fixed v1.0.140: probe sends `query=inception` for radarr, `query=office` for sonarr, etc. Bumped `_CACHE_VERSION` to invalidate poisoned cache entries |
| Some indexers tagged but TPB/1337x missing for radarr | TPB's Cardigann def doesn't translate the standard newznab `categories=2000` (Movies) cleanly. Probe returns 0 even though TPB has the content | Open. Workaround: manually tag broad indexers for all apps |

## Stage 3 — push-indexers (Prowlarr → *arr sync)

**What it does.** Triggers Prowlarr's `ApplicationIndexerSync` command
(forceSync=true). Prowlarr iterates its applications and, for each,
pushes the indexers whose tags match that app's filter tag. The
`*arr` then has the indexer in its own DB and can search it.

**Where it lives.**
- Adapter: [job_adapters.py](../src/media_stack/services/apps/core/job_adapters.py) `push_indexers`
- Sync: Prowlarr's `/api/v1/command` `{name: ApplicationIndexerSync}`

**Job declaration.**
```yaml
push-indexers:
  after: [reset-prowlarr-app-mappings]   # Transitively waits for tag.
```

**Verify.**

```bash
SK=$(docker exec sonarr cat /config/config.xml | grep -oP '(?<=<ApiKey>)[^<]+')
curl -sL -H "X-Api-Key: $SK" http://localhost:8989/app/sonarr/api/v3/indexer | \
  jq -r '.[] | "\(.name)\trss=\(.enableRss)\tsearch=\(.enableAutomaticSearch)"'
# Expect: ≥1 indexer per relevant *arr, all rss=true and search=true.
```

**Common failures.**

| Symptom | Cause | Fix |
| --- | --- | --- |
| `*arr` has 0 indexers but Prowlarr `tags` look right | `syncLevel=addOnly` mode + Prowlarr's ApplicationIndexerMapping table thinks it's already pushed everything to the empty `*arr` | `reset-prowlarr-app-mappings` clears the mapping rows for any `*arr` at zero, forcing a re-push (v1.0.125) |
| Indexers exist but `enableRss=false` / `enableAutomaticSearch=false` | Prowlarr's push uses the indexer's *current* settings; if some other handler downgraded them it propagates | Check Prowlarr UI; set both true and re-sync |
| URL-base 307 redirects break sync | `*arr` uses `/app/sonarr/...` URL base; Prowlarr POSTs lose body on 307 with stdlib urllib | Already fixed: `_make_servarr_http_request` handles 307 manually preserving method+body (v1.0.121) |

## Stage 4 — *arr search (RSS or manual)

**What it does.** *arr has monitored content (added by user, by
import lists with `enableAuto=true`, or by Jellyseerr requests). For
each missing item, the *arr searches its indexers via Prowlarr (which
proxies the search to the underlying tracker). Search results are
ranked by quality profile, the best is "grabbed" — meaning the *arr
sends the .torrent / magnet to the configured download client (qBit).

**Where it lives.** Inside the `*arr` itself. The controller doesn't
drive search; it only configures the *arr.

**Verify.**

```bash
RK=$(docker exec radarr cat /config/config.xml | grep -oP '(?<=<ApiKey>)[^<]+')

# Are there monitored items at all?
curl -s -H "X-Api-Key: $RK" http://localhost:7878/app/radarr/api/v3/movie | jq 'length'

# Are import lists set to auto-add?
curl -s -H "X-Api-Key: $RK" http://localhost:7878/app/radarr/api/v3/importlist | \
  jq -r '.[] | "\(.name)\tenableAuto=\(.enableAuto)\tsearchOnAdd=\(.searchOnAdd)"'
# Expect: enableAuto=true on every list. enableAuto=false → list imports
#         items but doesn't auto-monitor → no search happens.

# Did anything get grabbed recently?
curl -s -H "X-Api-Key: $RK" "http://localhost:7878/app/radarr/api/v3/history?pageSize=20" | \
  jq -r '.records[] | "\(.eventType)\t\(.sourceTitle)"'
# Expect: at least some "grabbed" entries within minutes of MissingMoviesSearch.
```

**Common failures.**

| Symptom | Cause | Fix |
| --- | --- | --- |
| `*arr` has 0 monitored items | Import lists missing OR `enableAuto=false` on every list | Set `enableAuto=true`. The arr.yaml defaults declare it but the override path through `import_lists.py` isn't reaching Radarr's payload — schema vs override mismatch (open) |
| 0 indexers tagged for this app's content type | Stage 2 probe excluded all of them | See stage 2. For radarr this commonly means only YTS+Nyaa.si match — most popular movies have 0 grabs because YTS's catalog is narrow and Nyaa.si is anime only |
| Search runs but `0 grabs from 2 active indexers` | The indexers don't carry the monitored content. The TMDB Popular Movies list returns brand-new theatrical releases that no public tracker has yet | Use TMDB Top Rated or specific known-available titles for OTB demos |
| `MonoTorrent.TorrentException: Invalid torrent file` | Indexer returned HTML (CloudFlare challenge) instead of .torrent bytes. *arr can't parse | Confirm FlareSolverr is reachable AND that the indexer has the FlareSolverr proxy attached (Prowlarr indexer's `fields.[name=proxyId]`) |

## Stage 5 — qBit (download client)

**What it does.** *arr POSTs the .torrent or magnet to qBit's API.
qBit starts the transfer, *arr tracks it via the queue endpoint,
unpacks/imports on completion.

**Verify.**

```bash
curl -s "http://admin:adminadmin@localhost:8080/api/v2/torrents/info" | \
  jq -r '.[] | "\(.name)\t\(.state)\t\(.progress * 100)%"'
# Expect: at least 1 item per recent grab event.
```

**Common failures.**

| Symptom | Cause | Fix |
| --- | --- | --- |
| `*arr` history shows "grabbed" but qBit is empty | Download client config wrong (host/port/credentials) OR category mismatch | `curl -H "X-Api-Key: $RK" .../downloadclient` and verify `host=qbittorrent`, `port=8080`, credentials match |
| Items stuck "Downloading metadata" forever | qBit can't talk to the BitTorrent network (firewall, vpn, port closed) | Check qBit logs and tracker status |
| qBit fills up over time | media-hygiene cleanup wasn't scheduled | Controller's scheduler thread fires `run-media-hygiene` hourly. If disabled, set `MEDIA_HYGIENE_INTERVAL_SECONDS` to enable |

## The job framework — why ordering matters

The job runner has TWO dependency mechanisms:

1. **`requires: [name, ...]`** — named PRECONDITIONS from a registry
   (e.g. `media_server_reachable`). Each entry is a function that
   returns `True`/`False`. Useful for "is the *arr up?" gates.
2. **`after: [job-name, ...]`** — job-name DEPENDENCIES. Means
   "wait for this other job's handler to FULLY COMPLETE."

The distinction matters because of `non_blocking: true`. A
non-blocking job spawns a daemon thread and returns control to the
dispatcher immediately — the dispatcher records "dispatched" but the
real work continues in the background. Without `after:`, downstream
peers race against that thread and start with empty data. **The
indexer pipeline broke (re-broke?) because of exactly this race**:
`tag-indexers-for-apps` ran 11 seconds into bootstrap and tagged 0
indexers because `discover-indexers` was still 40s away from
finishing.

Implementation: [job_framework.py](../src/media_stack/cli/commands/job_framework.py)
`JobRunner.run` uses a `threading.Condition` to wake when any async
job signals completion, then re-evaluates the ready set. No polling,
no fixed sleeps.

Ratchet: [test_v1_0_122_batch6_ratchets.py](../tests/unit/test_v1_0_122_batch6_ratchets.py)
`NonBlockingJobsHaveAfterDeps` runs an end-to-end runner test that
asserts a downstream `after:`-dependent sibling does NOT start before
the non_blocking upstream finishes.

## "Nothing's downloading" debug recipe

```bash
# 1. Did discovery run?
docker logs media-stack-controller 2>&1 | grep -E "Auto indexer summary"
# Expect: scanned=N/N, added>0

# 2. Did Prowlarr get the indexers?
PK=$(docker exec prowlarr cat /config/config.xml | grep -oP '(?<=<ApiKey>)[^<]+')
curl -s -H "X-Api-Key: $PK" http://localhost:9696/app/prowlarr/api/v1/indexer | jq 'length'

# 3. Did tagging finish AFTER discovery?
docker logs media-stack-controller 2>&1 | grep -E "(discover-indexers|tag-indexers-for-apps).*non-blocking finished"
# Expect: discover-indexers finishes BEFORE tag-indexers starts.

# 4. Are indexers tagged?
curl -s -H "X-Api-Key: $PK" http://localhost:9696/app/prowlarr/api/v1/indexer | \
  jq -r '.[] | "\(.name)\ttags=\(.tags)"'

# 5. Did the *arrs receive them?
SK=$(docker exec sonarr cat /config/config.xml | grep -oP '(?<=<ApiKey>)[^<]+')
curl -sL -H "X-Api-Key: $SK" http://localhost:8989/app/sonarr/api/v3/indexer | jq 'length'

# 6. Are import lists set to auto-add?
RK=$(docker exec radarr cat /config/config.xml | grep -oP '(?<=<ApiKey>)[^<]+')
curl -sL -H "X-Api-Key: $RK" http://localhost:7878/app/radarr/api/v3/importlist | \
  jq -r '.[] | "\(.name)\tenableAuto=\(.enableAuto)"'

# 7. Has anything been grabbed?
curl -sL -H "X-Api-Key: $RK" "http://localhost:7878/app/radarr/api/v3/history?pageSize=20" | \
  jq -r '.records[] | "\(.eventType)\t\(.sourceTitle)"' | sort | uniq -c
```

If step 1 shows N=0, the curated allowlist is broken or Prowlarr is
unreachable. If step 3 shows tag starting before discover finishes,
the `after:` chain in `core.yaml` was edited away — restore it. If
step 6 shows `enableAuto=false`, search will never auto-fire even
when the chain works.
