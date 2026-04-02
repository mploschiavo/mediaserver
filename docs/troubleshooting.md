# Troubleshooting

## 0) Everything Is Running, but I Can’t Access It in My Browser

This stack is exposed through Kubernetes Ingress. Browser access requires both:

1. An Ingress Controller running in your cluster.
2. DNS (or hosts-file) records that point your hostnames to your cluster node IP.

Quick verification:
```bash
kubectl get ingressclass
kubectl -n <NAMESPACE> get ingress media-stack-ingress -o wide
kubectl -n ingress get pods
kubectl -n <NAMESPACE> get svc
```

If Ingress Controller is missing:

- MicroK8s:
```bash
microk8s enable ingress
kubectl get ingressclass
```

- NGINX Ingress (generic clusters):
```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml
kubectl -n ingress-nginx rollout status deploy/ingress-nginx-controller
kubectl get ingressclass
```

Point hostnames to the node IP:

- Generate entries:
```bash
bash scripts/render-hosts-example.sh <NODE_IP> <NAMESPACE>
```

- Linux/macOS (`/etc/hosts`):
```bash
sudo nano /etc/hosts
# add lines from render-hosts-example output
```

- Windows (`C:\Windows\System32\drivers\etc\hosts`) using Admin editor:
```text
192.168.1.60 jellyfin.local jellyseerr.local sonarr.local radarr.local ...
```

- Local network DNS (router/dnsmasq/Pi-hole):
  - map each `*.local` host used by this stack to `<NODE_IP>`, or
  - use generated dnsmasq snippets:
```bash
bash scripts/render-dnsmasq-snippet.sh <NODE_IP> <NAMESPACE>
```

Then validate from the same client machine:
```bash
nslookup jellyfin.local
curl -I http://jellyfin.local
curl -I http://homepage.local
```

If name resolution works but UI still fails:
- hard refresh browser (`Ctrl+Shift+R`)
- test from private/incognito window
- run smoke test:
```bash
bash scripts/microk8s-smoke-test.sh <NODE_IP> <NAMESPACE>
```

## 1) Bootstrap Job Fails

Check:
```bash
kubectl -n <NAMESPACE> describe job media-stack-bootstrap
kubectl -n <NAMESPACE> logs job/media-stack-bootstrap --tail=300
MEDIA_STACK_LOG_LEVEL=DEBUG bash scripts/bootstrap-all.sh --no-resume
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

## 2b) Sonarr/Radarr Show 0 Downloads

Most often this is an indexer availability issue (not a Sonarr/Radarr path issue).

Checklist:
- confirm Prowlarr has active indexers (`/api/v1/indexer` not empty)
- run auto-indexer reconcile and Arr indexer sync
- if strict tested-indexer mode yields zero adds, enable limited untested fallback
  (`prowlarr_indexer_reputation.allow_untested_fallback=true`) and keep a small
  fallback cap (`untested_fallback_max_add`)
- re-run bootstrap with `--no-resume` after changing config

Commands:
```bash
bash scripts/run-prowlarr-auto-indexers.sh
bash scripts/bootstrap-all.sh --no-resume
bash scripts/verify-flow.sh <NAMESPACE>
```

## 3) Arr Imports Fail (Remote Path Mapping)

Symptoms:
- completed downloads exist, but imports fail
- Arr warns remote path does not exist

Actions:
```bash
bash scripts/bootstrap-all.sh
```

## 4) Jellyfin Shows Wizard Again

Run:
```bash
bash scripts/ensure-jellyfin-bootstrap.sh
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
bash scripts/deploy-stack.sh <NODE_IP>
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

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
