# Networking Model

## Routing Model

Every app in the stack is reachable through **three consistent route patterns** via Envoy:

| Pattern | Example | Use Case |
|---|---|---|
| Simple host | `sonarr.local` | Bookmarks, direct access |
| Namespace-qualified host | `sonarr.media-stack.local` | Multi-environment isolation |
| Path-prefix (gateway) | `apps.media-stack.local/app/sonarr` | Single endpoint, upstream routers |

All 19 services in the standard profile support all three patterns:
bazarr, controller, envoy, flaresolverr, homepage, jellyfin, jellyseerr,
lidarr, maintainerr, plex, prowlarr, qbittorrent, radarr, readarr, recyclarr,
sabnzbd, sonarr, tautulli, unpackerr.

The route strategy is set in the bootstrap profile (`routing.strategy`):
- **hybrid** (default): all three patterns active
- **path-prefix**: gateway path-prefix routes only
- **subdomain**: host-based routes only

The subdomain base is derived from the gateway host. For example:
- Gateway `apps.media-stack.local` → subdomain base `media-stack.local` → `sonarr.media-stack.local`
- Gateway `apps.media-dev.local` → subdomain base `media-dev.local` → `sonarr.media-dev.local`

## Namespace-Aware Domains

For parallel environments, use unique namespace + domain suffix pairs.

Example (cross-platform — Windows / macOS / Linux):
```bash
.venv/bin/python -m media_stack.cli.commands.install_main \
    --profile full --namespace media-stack-dev --ingress-domain dev.local --node-ip <NODE_IP>
.venv/bin/python -m media_stack.cli.commands.install_main \
    --profile full --namespace media-stack-e2e --ingress-domain e2e.local --node-ip <NODE_IP>
```

Linux convenience wrapper: `bash bin/install/install.sh ...`.

### Safe Env-File Workflow

Use env templates to prevent namespace/domain drift and accidental teardown.

Templates:
- `examples/environments/media-dev.env.example`
- `examples/environments/media-stack.env.example`

Sourcing the env vars from the file is operating-system specific:

```bash
# Linux / macOS (POSIX shell):
set -a; source <ENV_FILE>; set +a
.venv/bin/python -m media_stack.cli.commands.install_main ...
.venv/bin/python -m media_stack.cli.commands.deploy_stack_main ...

# Windows PowerShell:
Get-Content <ENV_FILE> | ForEach-Object {
  if ($_ -match '^([^#=]+)=(.*)$') { Set-Item -Path "env:$($Matches[1])" -Value $Matches[2] }
}
.venv\Scripts\python -m media_stack.cli.commands.install_main ...
```

Linux convenience: `bash bin/with-env.sh <ENV_FILE> bash bin/install/install.sh`
(applies `DELETE_NAMESPACE=0` when unset so destructive rebuilds stay opt-in;
to allow teardown set both `DELETE_NAMESPACE=1` and
`DELETE_NAMESPACE_CONFIRM=<namespace-or-compose-project>`).

## DNS/Hosts Automation

The host-file and dnsmasq renderers are Linux-only — they depend on shell
text-processing and `/etc/hosts` / dnsmasq path conventions:

```bash
bash bin/utils/render-hosts-example.sh <NODE_IP> <NAMESPACE>
bash bin/utils/render-dnsmasq-snippet.sh <NODE_IP> <NAMESPACE>
```

For Windows / macOS, edit your host file directly (`C:\Windows\System32\drivers\etc\hosts`
on Windows, `/etc/hosts` on macOS) — the renderer just prints the lines you'd add.

## Smoke Validation

Cross-platform:
```bash
.venv/bin/python -m media_stack.cli.commands.microk8s_smoke_test_main <NODE_IP> [NAMESPACE]
```

Linux convenience: `bash bin/test/microk8s-smoke-test.sh ...`. The underlying cluster still has to be MicroK8s.

## Internal vs External Boundaries

- Internal service-to-service traffic should use cluster DNS (`service.namespace.svc` or short service names in namespace).
- External user access should go through ingress host routes.
- Downloader and control APIs should remain internal-only unless explicitly required.

Reference diagram:
- [`docs/diagrams/network-protocol-topology.svg`](diagrams/network-protocol-topology.svg)

![Network and protocol topology](diagrams/network-protocol-topology.png)

## Authentication

See [Authentication](auth.md) for Authelia/Authentik SSO setup, OIDC configuration, and per-service auth policies.

## TLS

Optional LAN TLS helper (cross-platform):
```bash
.venv/bin/python -m media_stack.cli.commands.setup_lan_tls_main
```

Linux convenience: `bash bin/utils/setup-lan-tls.sh`.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
