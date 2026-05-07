"""qBittorrent-specific infrastructure.

ADR-0002 Phase 16-D batch 3 (download clients — qbittorrent) —
tech-specific I/O: HTTP preflight, compose preflight (docker exec),
admin password recovery, and kubectl-driven CLI helpers for
credential reconciliation.
"""

# qBittorrent's factory-default WebUI password since v4.0+. Hardcoded
# upstream in the binary; the controller's preflight uses it as one
# of the candidate passwords when reconciling stack admin credentials
# on a fresh install (alongside the temp password printed to stdout
# by linuxserver/qbittorrent and any operator-provided
# QBITTORRENT_PASSWORD env). Not a "default we should move to config"
# in the sense the ratchet flags — it's a property of the upstream
# binary, not a configurable choice.
QBITTORRENT_FACTORY_DEFAULT_USERNAME = "admin"
QBITTORRENT_FACTORY_DEFAULT_PASSWORD = "adminadmin"  # noqa: S105

# qBittorrent's WebUI default port (linuxserver/qbittorrent and the
# upstream binary both bind to 8080). Lives here so adapters that
# need to point an *arr's download-client config at the qBit
# WebUI can import the canonical port instead of inlining the
# literal — same allowlist rationale as the factory password
# above (it's an upstream binary property, not an operator-tunable
# default we should move to profile YAML).
QBITTORRENT_DEFAULT_WEBUI_PORT = 8080
QBITTORRENT_DEFAULT_HOST = "qbittorrent"
