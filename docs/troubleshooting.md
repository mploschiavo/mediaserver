# Troubleshooting

## 1) Bootstrap Job Fails

Check:
```bash
kubectl -n <NAMESPACE> describe job media-stack-bootstrap
kubectl -n <NAMESPACE> logs job/media-stack-bootstrap --tail=300
bash scripts/bootstrap-debug.sh
```

Common causes:
- stale credentials
- provider/indexer auth issues
- path or permissions mismatches

## 2) Arr Says qBittorrent Is Unreachable

- Verify service and pod health.
- Reconcile credentials and download clients.

```bash
bash scripts/ensure-qbit-credentials.sh
bash scripts/bootstrap-all.sh
bash scripts/verify-flow.sh <NAMESPACE>
```

## 3) Arr Imports Fail (Remote Path Mapping)

Symptoms:
- completed downloads exist, but imports fail
- Arr warns remote path does not exist

Actions:
```bash
bash scripts/bootstrap-all.sh
# legacy-hostpath mode only:
sudo PUID=911 PGID=911 bash scripts/fix-media-perms.sh /srv/media-stack
```

## 4) Jellyfin Shows Wizard Again

Run:
```bash
python3 scripts/ensure-jellyfin-bootstrap.py
bash scripts/bootstrap-all.sh
```
Then retry in private/incognito browser session to avoid stale client state.

## 5) Missing Artwork/Backdrops

Checklist:
- confirm media is imported into `/media/*`
- confirm naming quality
- confirm metadata tuning and plugin reconcile succeeded
- trigger bootstrap reconcile and library refresh

```bash
bash scripts/bootstrap-all.sh
bash scripts/verify-flow.sh <NAMESPACE>
```

## 6) Live TV Channels Show but Guide/Now Is Empty

This usually means tuner playlist data exists but guide data is missing or not mapped yet.

Checklist:
- ensure `jellyfin_livetv.guides` is configured in `bootstrap/media-stack.bootstrap.json`
- keep `jellyfin_livetv.refresh_on_bootstrap=true` so Guide/Now refresh runs each bootstrap
- keep `jellyfin_livetv.cleanup_duplicates=true` and `jellyfin_livetv.recreate_managed_guides=true`
  so stale/duplicate Live TV bindings are auto-repaired
- keep `jellyfin_livetv.prune_unmanaged_tuners=true` and `jellyfin_livetv.prune_unmanaged_guides=true`
  so old manual bindings do not drift the current config-as-code state
- for IPTV-ORG playlists, keep tuner options:
  - `normalize_tvg_id_suffix=true`
  - `filter_to_guide_channels=true`
  These normalize `tvg-id` values (for example `ABCWBMA.us@SD` -> `ABCWBMA.us`) and keep only channels
  that are present in the configured XMLTV guide.
- rerun bootstrap to reconcile tuners/guides
- check bootstrap logs for `requested guide refresh` and `requested channel refresh`

```bash
bash scripts/run-bootstrap-job.sh
kubectl -n <NAMESPACE> logs job/media-stack-bootstrap --tail=300 | grep -E "Jellyfin Live TV"
```

## 7) Ingress Routes Return 404

Usually wrong ingress class or DNS/hosts mismatch.

```bash
kubectl get ingressclass
kubectl -n <NAMESPACE> get ingress
bash scripts/microk8s-patch-ingress-class.sh <INGRESS_CLASS>
bash scripts/microk8s-smoke-test.sh <NODE_IP> <NAMESPACE>
```

## 8) Kustomize Profile Apply Fails With Load Restrictions

If your kubectl enforces strict load restrictions, use installer/rebuild scripts. They already include fallback behavior to direct manifest apply.

```bash
bash scripts/rebuild-and-bootstrap.sh <NODE_IP>
```

## 9) qBittorrent Login Drift

```bash
bash scripts/ensure-qbit-credentials.sh
# if needed
bash scripts/reset-qbit-webui-auth.sh
bash scripts/set-qbit-secret.sh <USERNAME> <PASSWORD>
```

## 10) Disk Usage Keeps Growing

This stack includes `disk_guardrails` (default 65% max used on `/srv-stack`) with qB cleanup policy.

Check:
```bash
kubectl -n <NAMESPACE> logs job/media-stack-bootstrap --tail=300 | grep -E "Disk guardrails"
```

Tune policy:
- `bootstrap/media-stack.bootstrap.json` -> `disk_guardrails`
- adjust `max_used_percent`, `target_used_percent`, and `qbit_cleanup` criteria

## 11) Jellyfin Collection Click Starts Playback Instead of Opening

If synthetic curated collections (`Trending`, `Top Rated`, etc.) feel clunky, disable
collection rails and let bootstrap clean them up:

```bash
bash scripts/reconcile-jellyfin-home-rails.sh
```

By default this stack now runs in native-first Jellyfin mode:
- `jellyfin_home_rails.enabled=false`
- `jellyfin_home_rails.cleanup_collections_when_disabled=true`

Then hard-refresh Jellyfin (`Ctrl+Shift+R`) and reopen Movies/Home.
