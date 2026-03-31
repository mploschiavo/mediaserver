# Runtime Overlays

Runtime config now supports layered loading:

1. `config/runtime/base.json`
2. `config/runtime/overlays/<env>.json`
3. the explicit config file (for example `bootstrap/media-stack.bootstrap.json`)

Set environment via:

- CLI: `--env dev|stage|prod`
- env var: `MEDIA_STACK_ENV=dev|stage|prod`
- config: `config_overlays.env`

Disable overlay loading for a config by setting:

```json
{
  "config_overlays": {
    "enabled": false
  }
}
```

