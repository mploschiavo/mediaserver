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

# Reverse-proxy trust settings the controller writes into qBittorrent
# at every deploy. Without these, qBittorrent enforces its own WebUI
# login on every request — even ones that already passed Authelia
# SSO at Envoy — which produces a second login prompt at
# ``apps.<domain>/app/qbittorrent/`` and breaks the "one login"
# operator expectation that every other service satisfies.
#
# The values mirror what qBittorrent's WebUI auto-populates when an
# operator manually ticks Settings → Web UI → Authentication →
# "Bypass authentication for clients in whitelisted IP subnets":
# the three RFC1918 ranges + IPv4/IPv6 loopback. That set covers
# every realistic reverse-proxy source on both compose and k8s
# (compose default ``172.18.0.0/16`` ⊂ ``172.16.0.0/12``; k3s pod
# CIDR ``10.42.0.0/16`` ⊂ ``10.0.0.0/8``; kubeadm-default
# ``10.244.0.0/16`` ⊂ ``10.0.0.0/8``; LAN ``192.168.x.x`` ⊂
# ``192.168.0.0/16``). A narrower "just the compose subnet"
# whitelist would lock out localhost probes and any operator
# tooling that talks to qB from outside the platform's container
# network.
#
# CSRF + HostHeader validation are disabled because both check the
# request's Origin/Host headers against qBittorrent's own bind
# address ("qbittorrent" or "127.0.0.1"), not against the
# Envoy-injected ``apps.<domain>/app/qbittorrent/`` rewrite. With
# them ON, the WebUI fails on legitimate post-Authelia traffic.
#
# Field names match the qBittorrent ``setPreferences`` API keys
# (see https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-Documentation
# ``Set application preferences``). The corresponding
# ``qBittorrent.conf`` keys are: ``WebUI\LocalHostAuth`` (inverse
# of ``bypass_local_auth``), ``WebUI\AuthSubnetWhitelistEnabled``,
# ``WebUI\AuthSubnetWhitelist``, ``WebUI\HostHeaderValidation``,
# ``WebUI\CSRFProtection``.
QBITTORRENT_REVERSE_PROXY_TRUST_PREFS: dict[str, object] = {
    "bypass_local_auth": True,
    "bypass_auth_subnet_whitelist_enabled": True,
    "bypass_auth_subnet_whitelist": (
        "10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.1/32, ::1/128"
    ),
    "web_ui_host_header_validation_enabled": False,
    "web_ui_csrf_protection_enabled": False,
}
