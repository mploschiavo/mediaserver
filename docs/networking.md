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
- `tautulli.<domain>`

## Namespace-Aware Domains

For parallel environments, use unique namespace + domain suffix pairs.

Example:
```bash
bash scripts/install.sh --profile full --namespace media-stack-dev --ingress-domain dev.local --node-ip <NODE_IP>
bash scripts/install.sh --profile full --namespace media-stack-e2e --ingress-domain e2e.local --node-ip <NODE_IP>
```

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

## TLS

Optional LAN TLS helper:
```bash
bash scripts/setup-lan-tls.sh
```
