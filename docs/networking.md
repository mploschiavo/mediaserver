# Networking Model

## Ingress and Hostnames

The stack exposes services through Kubernetes ingress host rules.

Default hostnames (domain suffix is configurable):
- `homepage.<domain>`
- `jellyfin.<domain>`
- `jellyseerr.<domain>`
- `sonarr.<domain>`
- `radarr.<domain>`
- `lidarr.<domain>`
- `readarr.<domain>`
- `bazarr.<domain>`
- `prowlarr.<domain>`
- `qbittorrent.<domain>`
- `sabnzbd.<domain>`
- `maintainerr.<domain>`
- `tautulli.<domain>`

## Namespace-Aware Domains

For parallel environments, use unique namespace + domain suffix pairs.

Example:
```bash
bash scripts/install.sh --profile full --namespace media-stack-dev --ingress-domain dev.local --node-ip <NODE_IP>
bash scripts/install.sh --profile full --namespace media-stack-e2e --ingress-domain e2e.local --node-ip <NODE_IP>
```

### Safe Env-File Workflow

Use env templates to prevent namespace/domain drift and accidental teardown.

Templates:
- `examples/environments/media-dev.env.example`
- `examples/environments/media-stack.env.example`

Run with env file:
```bash
bash scripts/with-env.sh <ENV_FILE> bash scripts/install.sh
bash scripts/with-env.sh <ENV_FILE> bash scripts/rebuild-and-bootstrap.sh
```

`scripts/with-env.sh` applies `DELETE_NAMESPACE=0` when unset, so destructive rebuilds stay opt-in.

## DNS/Hosts Automation

Render local hosts entries:
```bash
bash scripts/render-hosts-example.sh <NODE_IP> <NAMESPACE>
```

Render dnsmasq/AdGuard snippets:
```bash
bash scripts/render-dnsmasq-snippet.sh <NODE_IP> <NAMESPACE>
```

## Smoke Validation

```bash
bash scripts/microk8s-smoke-test.sh <NODE_IP> [NAMESPACE]
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
bash scripts/setup-lan-tls.sh
```

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
