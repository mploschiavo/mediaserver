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
- all examples default to `traefik`; switch a profile by changing that one field

Validate any profile with:

```bash
bash scripts/validate-bootstrap-profile.sh --config examples/bootstrap-profiles/media-compose-standard.yaml
```
