# Bootstrap Profile Examples

Canonical examples for both deployment targets and install tiers.

- `media-compose-minimal.yaml`
- `media-compose-standard.yaml`
- `media-compose-full.yaml`
- `media-k8s-minimal.yaml`
- `media-k8s-standard.yaml`
- `media-k8s-full.yaml`

Edge routing provider selection is profile-driven via `routing.provider`.

- canonical values: `traefik`, `envoy`
- `media-compose-standard.yaml` defaults to `envoy`
- other examples default to `traefik`; switch a profile by changing that one field

Compose standard profile uses a single gateway DNS host with path-prefix routing.

- create one local DNS/hosts entry: `127.0.0.1 apps.media-dev.local`
- access apps through `http://apps.media-dev.local:18080/app/<service>`
- representative URLs:
  - `http://apps.media-dev.local:18080/app/homepage`
  - `http://apps.media-dev.local:18080/app/bazarr`
  - `http://apps.media-dev.local:18080/app/jellyseerr`

Chaos testing is profile-driven via `chaos`.

- default is disabled in all examples (`chaos.enabled: false`)
- when enabled, compose deploy runs scheduled chaos actions and waits for self-healing
- canonical actions: `restart_container`, `pause_container`, `network_disconnect`

Validate any profile with:

```bash
bash scripts/validate-bootstrap-profile.sh --config examples/bootstrap-profiles/media-compose-standard.yaml
```
