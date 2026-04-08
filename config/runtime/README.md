# Runtime Overlays

Runtime config now supports layered loading:

1. `config/runtime/base.json`
2. `config/runtime/overlays/<env>.json`
3. per-service YAML contracts (`contracts/services/*.yaml`) and YAML defaults (`contracts/defaults/*.yaml`)

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


---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
