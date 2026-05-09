"""GeoIP enrichment for client IPs in the access log.

Goal: surface a country code + flag emoji next to public client IPs
without phoning home and without baking a 70MB MaxMind database into
the controller image. Two strategies, in order:

  1. **MaxMind GeoLite2 mmdb** — when the operator drops a free
     ``GeoLite2-Country.mmdb`` at the path named by the
     ``GEOIP_DB_PATH`` env (default
     ``/var/lib/media-stack/geoip/GeoLite2-Country.mmdb``), we use
     the ``geoip2`` Python package (lazy-imported) for accurate
     lookups. The DB is operator-supplied to keep our image lean.
  2. **CIDR fallback** — bundled compact table of major regional
     blocks (~30 entries covering the most-trafficked /8s for AWS,
     GCP, Cloudflare, ARIN, RIPE, APNIC, AFRINIC, LACNIC). Coarse
     but offline + zero-config; better than no info.

Private / loopback / multicast IPs return ``None`` so the UI can
hide the flag column for them — no point flagging your own LAN.
"""
from __future__ import annotations

import ipaddress
import os
import threading
from pathlib import Path
from typing import Any


# Coarse fallback — major /8 blocks → country code. Built from
# IANA's IPv4 address space registry; covers the most-trafficked
# allocations. Operators wanting accuracy install the mmdb.
_FALLBACK_BLOCKS: list[tuple[str, str]] = [
    # AWS / Cloudflare / common US blocks
    ("3.0.0.0/8", "US"), ("4.0.0.0/8", "US"), ("8.0.0.0/8", "US"),
    ("17.0.0.0/8", "US"), ("18.0.0.0/8", "US"), ("23.0.0.0/8", "US"),
    ("34.0.0.0/8", "US"), ("35.0.0.0/8", "US"), ("38.0.0.0/8", "US"),
    ("44.0.0.0/8", "US"), ("50.0.0.0/8", "US"), ("52.0.0.0/8", "US"),
    ("54.0.0.0/8", "US"), ("63.0.0.0/8", "US"), ("64.0.0.0/8", "US"),
    ("65.0.0.0/8", "US"), ("66.0.0.0/8", "US"), ("67.0.0.0/8", "US"),
    ("68.0.0.0/8", "US"), ("69.0.0.0/8", "US"), ("70.0.0.0/8", "US"),
    ("71.0.0.0/8", "US"), ("72.0.0.0/8", "US"), ("73.0.0.0/8", "US"),
    ("74.0.0.0/8", "US"), ("75.0.0.0/8", "US"), ("96.0.0.0/8", "US"),
    ("97.0.0.0/8", "US"), ("98.0.0.0/8", "US"),
    ("99.0.0.0/8", "US"), ("100.0.0.0/8", "US"),
    ("104.0.0.0/8", "US"), ("107.0.0.0/8", "US"),
    ("108.0.0.0/8", "US"), ("172.0.0.0/8", "US"),
    ("173.0.0.0/8", "US"), ("174.0.0.0/8", "US"),
    ("184.0.0.0/8", "US"), ("199.0.0.0/8", "US"), ("204.0.0.0/8", "US"),
    ("205.0.0.0/8", "US"), ("206.0.0.0/8", "US"), ("207.0.0.0/8", "US"),
    ("208.0.0.0/8", "US"), ("209.0.0.0/8", "US"), ("216.0.0.0/8", "US"),
    # APNIC blocks (China / Japan / KR / SG / AU)
    ("1.0.0.0/8", "AP"), ("14.0.0.0/8", "AP"), ("27.0.0.0/8", "AP"),
    ("36.0.0.0/8", "AP"), ("39.0.0.0/8", "AP"), ("42.0.0.0/8", "AP"),
    ("49.0.0.0/8", "AP"), ("58.0.0.0/8", "AP"), ("59.0.0.0/8", "AP"),
    ("60.0.0.0/8", "AP"), ("61.0.0.0/8", "AP"), ("101.0.0.0/8", "AP"),
    ("103.0.0.0/8", "AP"), ("106.0.0.0/8", "AP"), ("110.0.0.0/8", "AP"),
    ("111.0.0.0/8", "AP"), ("112.0.0.0/8", "AP"), ("113.0.0.0/8", "AP"),
    ("114.0.0.0/8", "AP"), ("115.0.0.0/8", "AP"), ("116.0.0.0/8", "AP"),
    ("117.0.0.0/8", "AP"), ("118.0.0.0/8", "AP"), ("119.0.0.0/8", "AP"),
    ("120.0.0.0/8", "AP"), ("121.0.0.0/8", "AP"), ("122.0.0.0/8", "AP"),
    ("123.0.0.0/8", "AP"), ("124.0.0.0/8", "AP"), ("125.0.0.0/8", "AP"),
    # RIPE (Europe)
    ("2.0.0.0/8", "EU"), ("5.0.0.0/8", "EU"), ("31.0.0.0/8", "EU"),
    ("37.0.0.0/8", "EU"), ("46.0.0.0/8", "EU"), ("62.0.0.0/8", "EU"),
    ("77.0.0.0/8", "EU"), ("78.0.0.0/8", "EU"), ("79.0.0.0/8", "EU"),
    ("80.0.0.0/8", "EU"), ("81.0.0.0/8", "EU"), ("82.0.0.0/8", "EU"),
    ("83.0.0.0/8", "EU"), ("84.0.0.0/8", "EU"), ("85.0.0.0/8", "EU"),
    ("86.0.0.0/8", "EU"), ("87.0.0.0/8", "EU"), ("88.0.0.0/8", "EU"),
    ("89.0.0.0/8", "EU"), ("90.0.0.0/8", "EU"), ("91.0.0.0/8", "EU"),
    ("92.0.0.0/8", "EU"), ("93.0.0.0/8", "EU"), ("94.0.0.0/8", "EU"),
    ("95.0.0.0/8", "EU"), ("141.0.0.0/8", "EU"), ("145.0.0.0/8", "EU"),
    ("151.0.0.0/8", "EU"), ("176.0.0.0/8", "EU"), ("178.0.0.0/8", "EU"),
    ("185.0.0.0/8", "EU"), ("188.0.0.0/8", "EU"), ("193.0.0.0/8", "EU"),
    ("194.0.0.0/8", "EU"), ("195.0.0.0/8", "EU"), ("212.0.0.0/8", "EU"),
    ("213.0.0.0/8", "EU"), ("217.0.0.0/8", "EU"),
    # LACNIC (Latin America)
    ("177.0.0.0/8", "BR"), ("179.0.0.0/8", "BR"), ("181.0.0.0/8", "AR"),
    ("186.0.0.0/8", "BR"), ("187.0.0.0/8", "BR"), ("189.0.0.0/8", "BR"),
    ("190.0.0.0/8", "BR"),
    # AFRINIC
    ("41.0.0.0/8", "AF"), ("102.0.0.0/8", "AF"), ("105.0.0.0/8", "AF"),
    ("154.0.0.0/8", "AF"), ("196.0.0.0/8", "AF"), ("197.0.0.0/8", "AF"),
]
_FALLBACK_NETS: list[tuple[ipaddress.IPv4Network, str]] = [
    (ipaddress.ip_network(cidr), code) for cidr, code in _FALLBACK_BLOCKS
]

_DEFAULT_GEOIP_DB_PATH = "/var/lib/media-stack/geoip/GeoLite2-Country.mmdb"


class GeoipService:
    """GeoIP country lookups with mmdb-preferred + CIDR-fallback dispatch.

    Holds the lazy-loaded ``geoip2.database.Reader`` (when available) plus
    the once-only init flag, behind a lock so concurrent first calls
    don't double-open the database.
    """

    def __init__(self) -> None:
        # Lazy-loaded mmdb reader; survives across calls.
        self._reader_obj: Any = None
        self._reader_lock = threading.Lock()
        self._reader_tried = False

    def reader(self) -> Any | None:
        """Get the geoip2 reader if available; cache it."""
        if self._reader_tried:
            return self._reader_obj
        with self._reader_lock:
            if self._reader_tried:
                return self._reader_obj
            self._reader_tried = True
            db_path = os.environ.get("GEOIP_DB_PATH", _DEFAULT_GEOIP_DB_PATH)
            if not Path(db_path).is_file():
                return None
            try:
                import geoip2.database  # type: ignore[import-untyped]
                self._reader_obj = geoip2.database.Reader(db_path)
            except Exception:  # noqa: BLE001
                self._reader_obj = None
            return self._reader_obj

    def is_public(self, ip: str) -> bool:
        """Skip private / loopback / multicast / link-local — they're
        operator-internal and shouldn't get a flag."""
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return not (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or addr.is_unspecified
        )

    def lookup_country(self, ip: str | None) -> str | None:
        """Return a 2-letter country code (or 2-letter region code like
        ``AP`` from the fallback table) for a public IPv4 address.
        Returns ``None`` for private IPs, IPv6 (until we extend),
        malformed input, or any lookup failure."""
        if not ip or not self.is_public(ip):
            return None
        # Strip whitespace + take just the first IP if it's actually an
        # XFF-style chain ("a.b.c.d, e.f.g.h").
        first_ip = ip.split(",")[0].strip()
        reader = self.reader()
        if reader is not None:
            try:
                r = reader.country(first_ip)
                return r.country.iso_code
            except Exception:  # noqa: BLE001
                pass
        # Fallback table.
        try:
            addr = ipaddress.ip_address(first_ip)
        except ValueError:
            return None
        if not isinstance(addr, ipaddress.IPv4Address):
            return None
        for net, code in _FALLBACK_NETS:
            if addr in net:
                return code
        return None

    def country_flag(self, code: str | None) -> str:
        """Country code → flag emoji. Generated from regional indicator
        symbols: 'A' = U+1F1E6, so 'US' = U+1F1FA + U+1F1F8."""
        if not code or len(code) != 2:
            return ""
        code = code.upper()
        if not code.isalpha():
            return ""
        return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code)


_INSTANCE = GeoipService()

# Module-level aliases — preserve the legacy underscore-prefixed names
# (used internally + by tests via mock.patch) plus the public surface.
_reader = _INSTANCE.reader
_is_public = _INSTANCE.is_public
lookup_country = _INSTANCE.lookup_country
country_flag = _INSTANCE.country_flag
