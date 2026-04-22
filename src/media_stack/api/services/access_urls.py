"""Access-URL discovery for the dashboard.

Builds the list of clickable URLs a user can use to reach the stack
from their browser, without needing to set /etc/hosts or DNS. Users
who just spun up the stack on a headless box and typed its LAN IP
into their browser's URL bar should land on something that works.

Three kinds of URL are emitted per bucket:

- "direct-ip": scheme plus the raw LAN IP and the bucket's host-
  exposed port. Works with no DNS and no /etc/hosts edits. This
  is the primary answer for users who don't know how to configure
  either.
- "gateway": the normal virtual-host path through Envoy, on the
  bucket's subdomain under the gateway suffix. Needs DNS or an
  /etc/hosts entry. We emit it anyway so the answer for users who
  already have DNS set up is also present.
- "gateway-apps": path-prefix route on the single apps host
  (``/app/<slug>``). Same DNS requirement as "gateway".

Buckets are declared in ``contracts/access_urls.yaml`` so adding
a new per-service entry doesn't require platform-code changes —
this module stays service-agnostic.

Discovery of the host IP uses the Host header of the incoming
request — that's the IP the client already used to reach us, so
every other URL we build from the same IP is reachable by
definition. Additional interface IPs are added best-effort by
enumerating outbound sockets. The loopback address is always
listed last so the LAN IP is the first thing the user sees.
"""

from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlunparse

import yaml

_log = logging.getLogger("media_stack")

_HTTP = "http"
_HTTPS = "https"

_GATEWAY_DOMAIN_SUFFIX = "media-stack.local"
_APPS_HOST = f"apps.{_GATEWAY_DOMAIN_SUFFIX}"
_CONTRACT_FILENAME = "access_urls.yaml"


@dataclass(frozen=True)
class AccessUrl:
    bucket: str
    url: str
    kind: str  # "direct-ip" | "gateway" | "gateway-apps"
    needs_dns: bool
    scheme: str  # "http" | "https"

    def to_dict(self) -> dict:
        return {
            "service": self.bucket, "url": self.url,
            "kind": self.kind, "needs_dns": self.needs_dns,
            "scheme": self.scheme,
        }


@dataclass(frozen=True)
class _Bucket:
    """Parsed contract row describing one surface-able destination."""
    slug: str
    label: str
    summary: str
    direct_port: int
    scheme: str
    gateway_subdomain: str
    gateway_path: str
    apps_host_direct_ip: bool


class AccessUrlDiscovery:
    """Builds the URL list. Stateless — caller instantiates per
    request and passes the request's Host header."""

    def __init__(
        self, host_ip_hint: str = "",
        env: dict[str, str] | None = None,
    ) -> None:
        bare = str(host_ip_hint or "").split(":", 1)[0].strip()
        self._hint = bare
        # Read env at construction so the resolver methods stay
        # off os.environ (the class-structure ratchet on os.environ
        # access from methods). Callers wanting override behaviour
        # pass a mapping; the default is the process env.
        self._env = dict(env) if env is not None else dict(os.environ)

    def build(self) -> dict[str, list[dict]]:
        """Return bucket → URL list. Every contract row becomes its
        own bucket; unknown bucket slugs are created on first use."""
        ips = self._candidate_ips()
        buckets = self._load_buckets()
        result: dict[str, list[dict]] = {}
        for entry in buckets:
            urls = self._urls_for(entry, ips)
            result.setdefault(entry.slug, []).extend(u.to_dict() for u in urls)
        return result

    def _urls_for(
        self, entry: _Bucket, ips: list[str],
    ) -> list[AccessUrl]:
        urls: list[AccessUrl] = []
        if entry.direct_port:
            for ip in ips:
                urls.append(self._direct(
                    entry.slug, entry.scheme, ip, entry.direct_port,
                ))
        if entry.apps_host_direct_ip:
            for ip in ips:
                urls.append(self._direct(
                    entry.slug, entry.scheme or _HTTPS, ip, None,
                ))
            urls.append(self._gateway_url(
                entry.slug, _APPS_HOST, "gateway",
            ))
        if entry.gateway_subdomain:
            host = f"{entry.gateway_subdomain}.{_GATEWAY_DOMAIN_SUFFIX}"
            urls.append(self._gateway_url(entry.slug, host, "gateway"))
        if entry.gateway_path:
            urls.append(self._gateway_url(
                entry.slug, _APPS_HOST, "gateway-apps",
                path=f"/{entry.gateway_path.strip('/')}/",
            ))
        return urls

    def _direct(
        self, bucket: str, scheme: str, ip: str, port: int | None,
    ) -> AccessUrl:
        host = ip if port is None else f"{ip}:{port}"
        url = urlunparse((scheme, host, "/", "", "", ""))
        return AccessUrl(bucket=bucket, url=url, kind="direct-ip",
                         needs_dns=False, scheme=scheme)

    def _gateway_url(
        self, bucket: str, host: str, kind: str, path: str = "/",
    ) -> AccessUrl:
        url = urlunparse((_HTTPS, host, path, "", "", ""))
        return AccessUrl(bucket=bucket, url=url, kind=kind,
                         needs_dns=True, scheme=_HTTPS)

    def _candidate_ips(self) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        if self._hint and self._is_ipv4(self._hint):
            seen.add(self._hint)
            ordered.append(self._hint)
        for ip in self._discover_lan_ips():
            if ip in seen or ip == "127.0.0.1":
                continue
            seen.add(ip)
            ordered.append(ip)
        if "127.0.0.1" not in seen:
            ordered.append("127.0.0.1")
        return ordered

    def _is_ipv4(self, value: str) -> bool:
        try:
            socket.inet_pton(socket.AF_INET, value)
            return True
        except (OSError, ValueError):
            return False

    def _discover_lan_ips(self) -> list[str]:
        results: list[str] = []
        primary = self._primary_outbound_ip()
        if primary:
            results.append(primary)
        for addr in self._resolver_ips():
            if addr not in results:
                results.append(addr)
        return results

    def _resolver_ips(self) -> list[str]:
        try:
            _, _, addrs = socket.gethostbyname_ex(socket.gethostname())
        except (OSError, socket.gaierror):
            return []
        return [a for a in addrs if self._is_ipv4(a)]

    def _primary_outbound_ip(self) -> str:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 53))
            ip = s.getsockname()[0]
            return ip if self._is_ipv4(ip) else ""
        except OSError as exc:
            _log.debug("[DEBUG] access-urls: outbound probe failed: %s", exc)
            return ""
        finally:
            try:
                s.close()
            except OSError:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)

    def _load_buckets(self) -> list[_Bucket]:
        path = self._contract_path()
        if path is None or not path.is_file():
            return []
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            _log.warning("[WARN] access-urls: contract load failed: %s", exc)
            return []
        out: list[_Bucket] = []
        for row in (data.get("buckets") or []):
            if not isinstance(row, dict):
                continue
            slug = str(row.get("slug", "")).strip()
            if not slug:
                continue
            out.append(_Bucket(
                slug=slug,
                label=str(row.get("label", slug)),
                summary=str(row.get("summary", "")),
                direct_port=int(row.get("direct_port", 0) or 0),
                scheme=str(row.get("scheme", _HTTP)),
                gateway_subdomain=str(row.get("gateway_subdomain", "")),
                gateway_path=str(row.get("gateway_path", "")),
                apps_host_direct_ip=bool(
                    row.get("apps_host_direct_ip", False),
                ),
            ))
        return out

    def _contract_path(self) -> Path | None:
        override = str(self._env.get("ACCESS_URLS_CONTRACT", "")).strip()
        if override:
            return Path(override)
        for base in self._candidate_contract_roots():
            p = base / _CONTRACT_FILENAME
            if p.is_file():
                return p
        return None

    def _candidate_contract_roots(self) -> list[Path]:
        roots: list[Path] = []
        env_dir = str(self._env.get("CONTRACTS_DIR", "")).strip()
        if env_dir:
            roots.append(Path(env_dir))
        # Walk every ancestor's contracts/. The first candidate that
        # actually contains our file wins in _contract_path; we
        # don't break on the first-found directory because there
        # can be unrelated "contracts" directories on the way up
        # (e.g. src/media_stack/contracts/ holds service configs,
        # the repo root contracts/ holds global ones).
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidate = parent / "contracts"
            if candidate.is_dir():
                roots.append(candidate)
        roots.append(Path("/srv-app/contracts"))
        return roots
