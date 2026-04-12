"""Homepage config helpers."""

from __future__ import annotations

from typing import Dict, Iterable, List, Tuple
from urllib import parse

DEFAULT_HOSTS = [
    "traefik.local",
    "envoy.local",
    "homepage.local",
    "jellyfin.local",
    "jellyseerr.local",
    "sonarr.local",
    "radarr.local",
    "lidarr.local",
    "readarr.local",
    "bazarr.local",
    "prowlarr.local",
    "qbittorrent.local",
    "sabnzbd.local",
    "maintainerr.local",
    "tautulli.local",
    "flaresolverr.local",
    "recyclarr.local",
    "media-stack-controller.local",
    "authelia.local",
    "authentik.local",
]

SERVICE_CATALOG: Dict[str, Tuple[str, str]] = {
    "traefik": ("Traefik", "Edge router"),
    "envoy": ("Envoy", "Edge router"),
    "homepage": ("Homepage", "Dashboard"),
    "jellyfin": ("Jellyfin", "Primary media server"),
    "media": ("Jellyfin", "Primary media server"),
    "jellyseerr": ("Jellyseerr", "Request management"),
    "sonarr": ("Sonarr", "TV automation"),
    "radarr": ("Radarr", "Movie automation"),
    "lidarr": ("Lidarr", "Music automation"),
    "readarr": ("Readarr", "Books automation"),
    "bazarr": ("Bazarr", "Subtitle automation"),
    "prowlarr": ("Prowlarr", "Indexer manager"),
    "qbittorrent": ("qBittorrent", "Torrent downloader"),
    "sabnzbd": ("SABnzbd", "Usenet downloader"),
    "maintainerr": ("Maintainerr", "Retention policy UI"),
    "tautulli": ("Tautulli", "Plex analytics"),
    "plex": ("Plex", "Optional media server"),
    "flaresolverr": ("FlareSolverr", "Indexer helper"),
    "recyclarr": ("Recyclarr", "Sync policy automation"),
    "authelia": ("Authelia", "Authentication provider"),
    "authentik": ("Authentik", "Authentication provider"),
    "controller": ("Controller", "Stack configuration & status"),
    "media-stack-controller": ("Controller", "Stack configuration & status"),
}

PREFERRED_PREFIX_ORDER = [
    "traefik",
    "envoy",
    "homepage",
    "jellyfin",
    "media",
    "jellyseerr",
    "sonarr",
    "radarr",
    "lidarr",
    "readarr",
    "bazarr",
    "prowlarr",
    "qbittorrent",
    "sabnzbd",
    "maintainerr",
    "tautulli",
    "plex",
    "flaresolverr",
    "recyclarr",
    "media-stack-controller",
    "authelia",
    "authentik",
]


class HomepageAdapters:

    @staticmethod
    def _normalize_hosts(hosts: Iterable[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for raw in hosts:
            host = str(raw or "").strip().lower()
            if not host or host in seen:
                continue
            seen.add(host)
            out.append(host)
        return out

    @staticmethod
    def _prefix(host: str) -> str:
        return host.split(".", 1)[0].strip().lower()

    @staticmethod
    def _service_token(host_or_path: str) -> str:
        text = str(host_or_path or "").strip().lower()
        if not text:
            return ""
        parsed = parse.urlparse(text if "://" in text else f"http://{text}")
        path = str(parsed.path or "").strip("/")
        if path:
            parts = [part for part in path.split("/") if part]
            if parts:
                if len(parts) >= 2 and parts[0] == "app":
                    token = parts[1].strip().lower()
                else:
                    token = parts[-1].strip().lower()
                if token:
                    return token
        netloc = str(parsed.netloc or "").split(":", 1)[0].strip().lower()
        return HomepageAdapters._prefix(netloc)

    @staticmethod
    def _service_meta(host: str) -> Tuple[str, str]:
        token = HomepageAdapters._service_token(host)
        if token in SERVICE_CATALOG:
            return SERVICE_CATALOG[token]
        title = token.replace("-", " ").title() or host
        return title, f"{title} service"

    @staticmethod
    def _ordered_hosts(hosts: Iterable[str]) -> List[str]:
        normalized = HomepageAdapters._normalize_hosts(hosts)
        rank = {prefix: idx for idx, prefix in enumerate(PREFERRED_PREFIX_ORDER)}
        return sorted(
            normalized,
            key=lambda h: (
                rank.get(HomepageAdapters._service_token(h), len(PREFERRED_PREFIX_ORDER)),
                HomepageAdapters._service_token(h),
                h,
            ),
        )

    @staticmethod
    def _normalize_target_url(value: str, scheme: str, default_host: str) -> str:
        text = str(value or "").strip()
        if not text:
            return f"{scheme}://{default_host}"
        if "://" not in text:
            return f"{scheme}://{text}"
        return text

    @staticmethod
    def _yaml_quote(text: str) -> str:
        value = str(text or "")
        return "'" + value.replace("'", "''") + "'"

    @staticmethod
    def _qr_href(target_url: str, size: int) -> str:
        encoded = parse.quote(target_url, safe="")
        return f"https://api.qrserver.com/v1/create-qr-code/?size={size}x{size}&data={encoded}"

    @staticmethod
    def _short_link_label(target_url: str) -> str:
        parsed = parse.urlparse(target_url)
        return parsed.netloc or parsed.path or target_url

    @staticmethod
    def _default_onboarding_cards(
        onboarding_cfg: Dict[str, object], scheme: str
    ) -> List[Tuple[str, str, str]]:
        jellyfin_url = HomepageAdapters._normalize_target_url(
            str(onboarding_cfg.get("jellyfin_url") or ""),
            scheme,
            "jellyfin.local",
        )
        jellyseerr_url = HomepageAdapters._normalize_target_url(
            str(onboarding_cfg.get("jellyseerr_url") or ""),
            scheme,
            "jellyseerr.local",
        )
        qr_size = int(onboarding_cfg.get("qr_size") or 220)
        qr_size = max(120, min(qr_size, 480))
        jellyfin_short = str(
            onboarding_cfg.get("jellyfin_short_link") or HomepageAdapters._short_link_label(jellyfin_url)
        )
        jellyseerr_short = str(
            onboarding_cfg.get("jellyseerr_short_link") or HomepageAdapters._short_link_label(jellyseerr_url)
        )

        cards: List[Tuple[str, str, str]] = [
            (
                "Jellyfin Setup QR",
                HomepageAdapters._qr_href(jellyfin_url, qr_size),
                f"Scan to open {jellyfin_url} on TV/mobile (short link: {jellyfin_short}).",
            ),
            (
                "Jellyseerr Setup QR",
                HomepageAdapters._qr_href(jellyseerr_url, qr_size),
                "Scan to open " f"{jellyseerr_url} for requests (short link: {jellyseerr_short}).",
            ),
            (
                "Samsung TV Quick Start",
                "https://github.com/jellyfin/jellyfin-tizen",
                ("Smart Hub > Apps > install Jellyfin for Tizen, then connect to " f"{jellyfin_url}."),
            ),
            (
                "Vizio Quick Start",
                "https://jellyfin.org/docs/general/clients/",
                (
                    "Use built-in casting (Chromecast/AirPlay) from Jellyfin mobile/web "
                    f"to {jellyfin_url}."
                ),
            ),
            (
                "TCL Quick Start",
                "https://github.com/jellyfin/jellyfin-androidtv",
                (
                    "Open Google Play on TCL Google TV/Android TV, install Jellyfin for "
                    f"Android TV, then connect to {jellyfin_url}."
                ),
            ),
        ]
        return cards

    @staticmethod
    def _build_onboarding_cards(
        onboarding_cfg: Dict[str, object], scheme: str
    ) -> List[Tuple[str, str, str]]:
        enabled = bool(onboarding_cfg.get("enabled", False))
        if not enabled:
            return []

        cards = HomepageAdapters._default_onboarding_cards(onboarding_cfg, scheme)
        custom = onboarding_cfg.get("cards")
        if isinstance(custom, list) and custom:
            cards = []
            for entry in custom:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or "").strip()
                href = str(entry.get("href") or "").strip()
                description = str(entry.get("description") or "").strip()
                if not name or not href:
                    continue
                cards.append((name, href, description))
        return cards

    def render_services_yaml(
        self,
        hosts: Iterable[str],
        scheme: str = "http",
        onboarding: Dict[str, object] | None = None,
    ) -> str:
        ordered = self._ordered_hosts(hosts)
        if not ordered:
            ordered = list(DEFAULT_HOSTS)

        lines = ["---", "- Media Stack:"]
        for host in ordered:
            title, description = self._service_meta(host)
            href = self._normalize_target_url(host, scheme, host)
            lines.extend(
                [
                    f"    - {title}:",
                    f"        href: {self._yaml_quote(href)}",
                    f"        description: {self._yaml_quote(description)}",
                ]
            )

        onboarding_cfg: Dict[str, object] = onboarding if isinstance(onboarding, dict) else {}
        onboarding_cards = self._build_onboarding_cards(onboarding_cfg, scheme)
        if onboarding_cards:
            lines.append("- Device Onboarding:")
            for title, href, description in onboarding_cards:
                lines.extend(
                    [
                        f"    - {title}:",
                        f"        href: {self._yaml_quote(href)}",
                        f"        description: {self._yaml_quote(description)}",
                    ]
                )

        return "\n".join(lines) + "\n"


_instance = HomepageAdapters()
render_services_yaml = _instance.render_services_yaml
_build_onboarding_cards = _instance._build_onboarding_cards
_default_onboarding_cards = _instance._default_onboarding_cards
_normalize_hosts = _instance._normalize_hosts
_normalize_target_url = _instance._normalize_target_url
_ordered_hosts = _instance._ordered_hosts
_prefix = _instance._prefix
_qr_href = _instance._qr_href
_service_meta = _instance._service_meta
_service_token = _instance._service_token
_short_link_label = _instance._short_link_label
_yaml_quote = _instance._yaml_quote
