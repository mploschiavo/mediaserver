# Automation Best Practices

This stack now supports end-to-end bootstrap automation, but these community patterns can take it further.

## High-impact patterns

1. Keep Prowlarr as the single source of truth for indexers.
   - Prowlarr is designed to sync indexers into Arr apps so you avoid per-app duplicate setup.
   - Source: https://github.com/Prowlarr/Prowlarr

2. Use Recyclarr for Sonarr/Radarr quality profile and custom-format sync.
   - This is the common "quality profile as code" path used with TRaSH-style setups.
   - Source: https://recyclarr.dev/guide/installation/

3. Use Buildarr when you want broader declarative config over time.
   - Buildarr supports plugin-based declarative management and can complement bootstrap scripts.
   - Source: https://buildarr.github.io/

4. Keep path mappings consistent across Arr + downloader.
   - Shared volumes and coherent paths prevent import failures and improve hardlink/rename behavior.
   - Related guidance is emphasized in Servarr/Prowlarr docs and Docker setup docs.
   - Source: https://prowlarr.com/docs/

5. Be careful with scale-to-zero for interactive media UX.
   - KEDA supports scale-to-zero, but cold starts can degrade user request flows.
   - Favor always-on for latency-sensitive apps (Jellyfin/Jellyseerr/Arr), and scale-to-zero only for non-interactive workers.
   - Source: https://keda.sh/docs/
   - HTTP-specific add-on reference: https://kedacore.github.io/http-add-on/faq.html

6. If adopting Envoy, prefer Gateway API-driven management.
   - Envoy Gateway provides a Kubernetes-native control plane over Envoy Proxy with Gateway API resources.
   - Source: https://gateway.envoyproxy.io/docs/

## Practical recommendation order

1. Keep using `scripts/rebuild-and-bootstrap.sh` for baseline automation.
2. Add Recyclarr for quality profile consistency.
3. Introduce Buildarr only when you need full declarative lifecycle management for additional apps.
4. Test any scale-to-zero strategy on non-critical services first.
