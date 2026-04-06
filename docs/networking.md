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

Example:
```bash
bash bin/install.sh --profile full --namespace media-stack-dev --ingress-domain dev.local --node-ip <NODE_IP>
bash bin/install.sh --profile full --namespace media-stack-e2e --ingress-domain e2e.local --node-ip <NODE_IP>
```

### Safe Env-File Workflow

Use env templates to prevent namespace/domain drift and accidental teardown.

Templates:
- `examples/environments/media-dev.env.example`
- `examples/environments/media-stack.env.example`

Run with env file:
```bash
bash bin/with-env.sh <ENV_FILE> bash bin/install.sh
bash bin/with-env.sh <ENV_FILE> bash bin/deploy-stack.sh
```

`bin/with-env.sh` applies `DELETE_NAMESPACE=0` when unset, so destructive rebuilds stay opt-in.
To allow teardown, set both `DELETE_NAMESPACE=1` and
`DELETE_NAMESPACE_CONFIRM=<namespace-or-compose-project>` (or `I_UNDERSTAND`).

## DNS/Hosts Automation

Render local hosts entries:
```bash
bash bin/render-hosts-example.sh <NODE_IP> <NAMESPACE>
```

Render dnsmasq/AdGuard snippets:
```bash
bash bin/render-dnsmasq-snippet.sh <NODE_IP> <NAMESPACE>
```

## Smoke Validation

```bash
bash bin/microk8s-smoke-test.sh <NODE_IP> [NAMESPACE]
```

## Internal vs External Boundaries

- Internal service-to-service traffic should use cluster DNS (`service.namespace.svc` or short service names in namespace).
- External user access should go through ingress host routes.
- Downloader and control APIs should remain internal-only unless explicitly required.

Reference diagram:
- [`docs/diagrams/network-protocol-topology.svg`](diagrams/network-protocol-topology.svg)

![Network and protocol topology](diagrams/network-protocol-topology.png)

## TLS

Optional LAN TLS helper:
```bash
bash bin/setup-lan-tls.sh
```

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
