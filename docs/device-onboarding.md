# Device Onboarding

This stack ships homepage onboarding cards and QR links by default when `homepage.device_onboarding.enabled=true` in bootstrap config.

Primary entrypoints:
- Jellyfin playback: `http://jellyfin.<domain>`
- Jellyseerr requests: `http://jellyseerr.<domain>`

## Samsung TV (Tizen)

1. Open Apps on TV and search `Jellyfin`.
2. Install and connect to `http://jellyfin.<domain>`.
3. If unavailable in your region, use the official Tizen project path:
- `https://github.com/jellyfin/jellyfin-tizen`

## Vizio (SmartCast)

Vizio commonly uses casting/AirPlay workflows.

1. Open Jellyfin on mobile/web.
2. Cast/AirPlay to Vizio device.
3. For richer app UX, use external Android TV/Google TV/Roku client devices.

## TCL

TCL model behavior depends on OS family:
- Roku-based TCL: use Jellyfin Roku client.
- Google TV/Android TV TCL: install Jellyfin Android TV app.

## DNS and Access from Other Devices

Use namespace-aware host mapping:
```bash
bash scripts/render-hosts-example.sh <NODE_IP> <NAMESPACE>
bash scripts/render-dnsmasq-snippet.sh <NODE_IP> <NAMESPACE>
```

## Validate Reachability

```bash
bash scripts/microk8s-smoke-test.sh <NODE_IP> <NAMESPACE>
```

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
