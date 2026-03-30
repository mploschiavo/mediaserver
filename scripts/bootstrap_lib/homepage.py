"""Homepage config helpers."""

from __future__ import annotations

from typing import Dict, Iterable, List, Tuple
from urllib import parse

DEFAULT_HOSTS = [
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
    "tautulli.local",
]

SERVICE_CATALOG: Dict[str, Tuple[str, str]] = {
    "homepage": ("Homepage", "Dashboard"),
    "jellyfin": ("Jellyfin", "Primary media server"),
    "jellyseerr": ("Jellyseerr", "Request management"),
    "sonarr": ("Sonarr", "TV automation"),
    "radarr": ("Radarr", "Movie automation"),
    "lidarr": ("Lidarr", "Music automation"),
    "readarr": ("Readarr", "Books automation"),
    "bazarr": ("Bazarr", "Subtitle automation"),
    "prowlarr": ("Prowlarr", "Indexer manager"),
    "qbittorrent": ("qBittorrent", "Torrent downloader"),
    "sabnzbd": ("SABnzbd", "Usenet downloader"),
    "tautulli": ("Tautulli", "Plex analytics"),
    "plex": ("Plex", "Optional media server"),
    "flaresolverr": ("FlareSolverr", "Indexer helper"),
    "traefik": ("Traefik", "Ingress dashboard"),
}

PREFERRED_PREFIX_ORDER = [
    "homepage",
    "jellyfin",
    "jellyseerr",
    "sonarr",
    "radarr",
    "lidarr",
    "readarr",
    "bazarr",
    "prowlarr",
    "qbittorrent",
    "sabnzbd",
    "tautulli",
    "plex",
    "flaresolverr",
    "traefik",
]


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


def _prefix(host: str) -> str:
    return host.split(".", 1)[0].strip().lower()


def _service_meta(host: str) -> Tuple[str, str]:
    prefix = _prefix(host)
    if prefix in SERVICE_CATALOG:
        return SERVICE_CATALOG[prefix]
    title = prefix.replace("-", " ").title() or host
    return title, f"{title} service"


def _ordered_hosts(hosts: Iterable[str]) -> List[str]:
    normalized = _normalize_hosts(hosts)
    rank = {prefix: idx for idx, prefix in enumerate(PREFERRED_PREFIX_ORDER)}
    return sorted(
        normalized,
        key=lambda h: (rank.get(_prefix(h), len(PREFERRED_PREFIX_ORDER)), _prefix(h), h),
    )


def _normalize_target_url(value: str, scheme: str, default_host: str) -> str:
    text = str(value or "").strip()
    if not text:
        return f"{scheme}://{default_host}"
    if "://" not in text:
        return f"{scheme}://{text}"
    return text


def _yaml_quote(text: str) -> str:
    value = str(text or "")
    return "'" + value.replace("'", "''") + "'"


def _qr_href(target_url: str, size: int) -> str:
    encoded = parse.quote(target_url, safe="")
    return f"https://api.qrserver.com/v1/create-qr-code/?size={size}x{size}&data={encoded}"


def _short_link_label(target_url: str) -> str:
    parsed = parse.urlparse(target_url)
    return parsed.netloc or parsed.path or target_url


def _default_onboarding_cards(
    onboarding_cfg: Dict[str, object], scheme: str
) -> List[Tuple[str, str, str]]:
    jellyfin_url = _normalize_target_url(
        str(onboarding_cfg.get("jellyfin_url") or ""),
        scheme,
        "jellyfin.local",
    )
    jellyseerr_url = _normalize_target_url(
        str(onboarding_cfg.get("jellyseerr_url") or ""),
        scheme,
        "jellyseerr.local",
    )
    qr_size = int(onboarding_cfg.get("qr_size") or 220)
    qr_size = max(120, min(qr_size, 480))
    jellyfin_short = str(
        onboarding_cfg.get("jellyfin_short_link") or _short_link_label(jellyfin_url)
    )
    jellyseerr_short = str(
        onboarding_cfg.get("jellyseerr_short_link") or _short_link_label(jellyseerr_url)
    )

    cards: List[Tuple[str, str, str]] = [
        (
            "Jellyfin Setup QR",
            _qr_href(jellyfin_url, qr_size),
            f"Scan to open {jellyfin_url} on TV/mobile (short link: {jellyfin_short}).",
        ),
        (
            "Jellyseerr Setup QR",
            _qr_href(jellyseerr_url, qr_size),
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


def _build_onboarding_cards(
    onboarding_cfg: Dict[str, object], scheme: str
) -> List[Tuple[str, str, str]]:
    enabled = bool(onboarding_cfg.get("enabled", False))
    if not enabled:
        return []

    cards = _default_onboarding_cards(onboarding_cfg, scheme)
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
    hosts: Iterable[str],
    scheme: str = "http",
    onboarding: Dict[str, object] | None = None,
) -> str:
    ordered = _ordered_hosts(hosts)
    if not ordered:
        ordered = list(DEFAULT_HOSTS)

    lines = ["---", "- Media Stack:"]
    for host in ordered:
        title, description = _service_meta(host)
        href = f"{scheme}://{host}"
        lines.extend(
            [
                f"    - {title}:",
                f"        href: {_yaml_quote(href)}",
                f"        description: {_yaml_quote(description)}",
            ]
        )

    onboarding_cfg: Dict[str, object] = onboarding if isinstance(onboarding, dict) else {}
    onboarding_cards = _build_onboarding_cards(onboarding_cfg, scheme)
    if onboarding_cards:
        lines.append("- Device Onboarding:")
        for title, href, description in onboarding_cards:
            lines.extend(
                [
                    f"    - {title}:",
                    f"        href: {_yaml_quote(href)}",
                    f"        description: {_yaml_quote(description)}",
                ]
            )

    return "\n".join(lines) + "\n"
