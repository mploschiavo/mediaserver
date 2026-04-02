# Environment Templates

These templates provide a safe, repeatable way to run stack commands per namespace.

## Why Use These

- Keep namespace/domain pairs explicit per environment.
- Avoid accidental teardown by default (`DELETE_NAMESPACE=0`).
- Reuse one env file for install, rebuild, smoke tests, and status checks.

## Quick Start

Create a local env file (recommended outside the repo):

```bash
cp examples/environments/media-dev.env.example ~/.config/media-stack/media-dev.env
```

Run install with the env file:

```bash
bash scripts/with-env.sh ~/.config/media-stack/media-dev.env \
  bash scripts/install.sh
```

Run rebuild/bootstrap with the same env file:

```bash
bash scripts/with-env.sh ~/.config/media-stack/media-dev.env \
  bash scripts/rebuild-and-bootstrap.sh
```

Render host entries for that namespace:

```bash
bash scripts/with-env.sh ~/.config/media-stack/media-dev.env \
  bash scripts/render-hosts-example.sh "$NODE_IP" "$NAMESPACE"
```

## Notes

- Keep `INGRESS_DOMAIN` unique per namespace (for example `media-dev.local`).
- Set `DELETE_NAMESPACE=1` only for intentional destructive rebuilds.
