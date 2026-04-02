# Bootstrap Profile

`bootstrap/media-stack.bootstrap.yaml` is the canonical deployment profile for rebuild/install defaults.

It is intentionally brief and strict:
- platform target (`metadata.platform`)
- environment purpose (`metadata.purpose`)
- stack identity (`metadata.name`)
- storage + network intent (`resources.*`)
- install tier (`install_profile`: `minimal` | `standard` | `full`)
- optional per-app overrides (`apps`)
- routing/auth posture (`routing` + `auth`)
- Live TV source defaults (`live_tv_defaults`)

## Files

- Profile: `bootstrap/media-stack.bootstrap.yaml`
- Schema: `bootstrap/media-stack.bootstrap.profile.schema.json`
- Validator: `bash scripts/validate-bootstrap-profile.sh`
- Example set: `examples/bootstrap-profiles/`

## Canonical Example

```yaml
schema_version: 1
kind: media_stack_profile

metadata:
  name: media-dev
  platform: compose
  purpose: dev

resources:
  disk_space_gb: 500
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

live_tv_defaults:
  tuner_url: https://iptv-org.github.io/iptv/countries/us.m3u
  guide_url: https://iptv-epg.org/files/epg-us.xml
  default_program_icon_url: https://raw.githubusercontent.com/iptv-org/logo/master/tv.png
```

## Install Tiers

- `minimal`: Jellyfin/Jellyseerr/Prowlarr/qBittorrent/Homepage
- `standard`: `minimal` + Sonarr/Radarr/Bazarr/SABnzbd/Maintainerr/Unpackerr
- `full`: all supported apps enabled

Automatic content behavior policy:

- `minimal` and `standard` default to manual content mode (`auto_download_content: false`)
- `full` defaults to automatic content mode (`auto_download_content: true`)
- All tiers can still preconfigure API keys and initial app preferences

Use `apps` for explicit overrides on top of the tier.

Routing provider keys:

- canonical values: `traefik`, `envoy`
- set under `routing.provider`

## Profile Matrix Examples

- Compose minimal: `examples/bootstrap-profiles/media-compose-minimal.yaml`
- Compose standard: `examples/bootstrap-profiles/media-compose-standard.yaml`
- Compose full: `examples/bootstrap-profiles/media-compose-full.yaml`
- K8s minimal: `examples/bootstrap-profiles/media-k8s-minimal.yaml`
- K8s standard: `examples/bootstrap-profiles/media-k8s-standard.yaml`
- K8s full: `examples/bootstrap-profiles/media-k8s-full.yaml`

## Live TV URLs Used In Code

These profile defaults intentionally match the current bootstrap config defaults in
`bootstrap/media-stack.bootstrap.json` (`jellyfin_livetv` section):

- Tuner playlist: `https://iptv-org.github.io/iptv/countries/us.m3u`
- XMLTV guide: `https://iptv-epg.org/files/epg-us.xml`
- Default icon: `https://raw.githubusercontent.com/iptv-org/logo/master/tv.png`

## CLI Integration

Rebuild runner auto-loads `bootstrap/media-stack.bootstrap.yaml` when present.

Override path:

```bash
bash scripts/deploy-stack.sh \
  --bootstrap-profile-file /path/to/profile.yaml
```

Quick validation:

```bash
bash scripts/validate-bootstrap-profile.sh
bash scripts/validate-bootstrap-profile.sh --config examples/bootstrap-profiles/media-k8s-full.yaml
```
