# Upgrades

Media Stack ships as a pinned controller image (`harbor.iomio.io/library/media-stack-controller:vX.Y.Z`). The app images (Jellyfin, Sonarr, etc.) come from upstream registries and follow their own release cadence.

## Routine upgrade

The controller version pinned in `dist/docker-compose.yml` (and `dist/k8s-deploy.yaml`) is the source of truth for which controller you're running. To upgrade:

### Docker Compose

```bash
git pull                                                # if you cloned the repo
docker compose -f dist/docker-compose.yml pull          # pull new images
docker compose -f dist/docker-compose.yml up -d         # recreate changed containers
```

### Kubernetes

```bash
git pull
kubectl apply -k k8s/profiles/standard                  # or whichever profile you use
```

The controller's bootstrap re-runs idempotently after any restart, so any new promises shipped in the upgrade will be applied automatically.

## Verifying after upgrade

```bash
bash bin/verify-stack.sh                                # quick smoke test
bash bin/verify-fresh-install.sh                        # full promises probe (slower)
```

The dashboard's Routes tab is the fastest visual check — green across all rows means the gateway, auth, and per-service routing all survived the upgrade.

## Backups before upgrading

For non-trivial upgrades (controller minor version bumps, app major version bumps, or anything labelled "breaking"):

```bash
bash bin/backup-stack.sh                                # creates ./backups/media-stack-backup-<ts>.tar.gz
```

Restore if needed:

```bash
bash bin/restore-stack.sh ./backups/media-stack-backup-<ts>.tar.gz
```

## Rolling back the controller

If a new controller version misbehaves, pin the previous tag in `dist/docker-compose.yml` and `dist/k8s-deploy.yaml`:

```bash
sed -i 's|controller:vX.Y.Z|controller:vX.Y.<previous>|g' dist/docker-compose.yml dist/k8s-deploy.yaml
docker compose -f dist/docker-compose.yml up -d
```

Bootstrap state is forward / backward compatible across patch versions in the same minor (`v1.0.X`). Major-minor downgrades may require a restore from backup if the controller wrote schema changes to `${CONFIG_ROOT}/.controller/`.

## Upgrade-induced breakage

If a promise fails after upgrade, that's the meta-ratchet doing its job:

1. Check `docker logs media-stack-controller --tail 200` for the failing job.
2. Re-run just that job: `curl -X POST http://localhost:9100/actions/<job-name>`.
3. If it still fails, the upgrade probably needs a profile change — see the changelog entry for that version.

For anything else, [open a bug](../CONTRIBUTING.md#reporting-a-bug) with the controller version and the failing probe.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
