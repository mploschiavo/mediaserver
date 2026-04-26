"""Proxy operations for Prowlarr."""

from __future__ import annotations

from typing import Any
from media_stack.api.services.registry import service_internal_url
from media_stack.core.logging_utils import log_swallowed


class ProwlarrProxyOps:

    def ensure_flaresolverr_proxy(self,
        service,
        prowlarr_url: str,
        prowlarr_key: str,
        flaresolverr_cfg: dict[str, Any] | None = None,
    ) -> int | None:
        """Returns the Prowlarr indexerProxy ID of the FlareSolverr
        entry (so callers can attach it to indexers that hit
        CloudFlare). Returns ``None`` only when the proxy upsert
        succeeded but Prowlarr didn't echo back an id — caller
        should treat that as "not available" and skip attachment."""
        cfg = dict(flaresolverr_cfg or {})
        proxy_name = str(cfg.get("proxy_name") or "FlareSolverr").strip() or "FlareSolverr"
        host = str(cfg.get("url") or service_internal_url("flaresolverr")).strip()
        if not host:
            raise RuntimeError("Prowlarr: FlareSolverr URL must be non-empty.")
        host = host.rstrip("/") + "/"
        try:
            request_timeout = int(cfg.get("request_timeout_seconds", 60))
        except (TypeError, ValueError):
            request_timeout = 60
        request_timeout = max(1, request_timeout)
        tags_raw = cfg.get("tags")
        tags: list[int] = []
        if isinstance(tags_raw, list):
            for tag in tags_raw:
                text = str(tag).strip()
                if not text:
                    continue
                try:
                    tags.append(int(text))
                except ValueError:
                    continue
        # Auto-attach to every sync-* tag so FlareSolverr applies to
        # every indexer the controller pushes to a *arr. Without
        # tags, Prowlarr never invokes FlareSolverr, and any
        # CloudFlare-protected indexer (Knaben, ThePirateBay,
        # TorrentDownload, Uindex, etc.) returns the CloudFlare
        # challenge HTML to Sonarr/Radarr, which then can't parse
        # it as a .torrent ("MonoTorrent.TorrentException: Invalid
        # torrent file"). End-user symptom: Sonarr queue fills with
        # ``downloadClientUnavailable`` and qBit stays at 0
        # downloads. (v1.0.130 — discovered in live diagnosis.)
        if not tags:
            try:
                _status, _tags, _body = service.http_request(
                    prowlarr_url, "/api/v1/tag", api_key=prowlarr_key,
                )
                if _status == 200 and isinstance(_tags, list):
                    for t in _tags:
                        label = str(t.get("label", "")).strip().lower()
                        if label.startswith("sync-") and t.get("id") is not None:
                            try:
                                tags.append(int(t["id"]))
                            except (TypeError, ValueError) as exc:
                                # Prowlarr occasionally returns tag
                                # ids as strings; skip the row and
                                # keep the rest of the tag list.
                                log_swallowed(exc)
            except Exception:
                # Tag lookup is best-effort. If it fails, the proxy
                # ships with empty tags (current behaviour) — no
                # regression vs v1.0.129.
                tags = []
        test_connection = bool(cfg.get("test_connection", True))

        status, schema_list, body = service.http_request(
            prowlarr_url,
            "/api/v1/indexerProxy/schema",
            api_key=prowlarr_key,
        )
        if status != 200 or not isinstance(schema_list, list):
            raise RuntimeError(f"Prowlarr: failed to read indexer proxy schema (HTTP {status}): {body}")

        schema = next(
            (item for item in schema_list if item.get("implementation") == "FlareSolverr"), None
        )
        if not schema:
            raise RuntimeError("Prowlarr: FlareSolverr proxy schema not available.")

        status, proxies, body = service.http_request(
            prowlarr_url,
            "/api/v1/indexerProxy",
            api_key=prowlarr_key,
        )
        if status != 200 or not isinstance(proxies, list):
            raise RuntimeError(f"Prowlarr: failed to list indexer proxies (HTTP {status}): {body}")
        current = next(
            (
                item
                for item in proxies
                if item.get("implementation") == "FlareSolverr"
                or str(item.get("name") or "").strip().lower() == proxy_name.lower()
            ),
            None,
        )

        fields = service.field_map(schema.get("fields"))
        fields["host"] = host
        if "requestTimeout" in fields:
            fields["requestTimeout"] = request_timeout
        payload = {
            "name": proxy_name,
            "implementation": "FlareSolverr",
            "configContract": schema.get("configContract", "FlareSolverrSettings"),
            "enable": True,
            "tags": tags,
            "fields": service.field_list(fields),
        }

        if current:
            payload["id"] = current.get("id")
            status, response_data, body = service.http_request(
                prowlarr_url,
                f"/api/v1/indexerProxy/{current.get('id')}",
                api_key=prowlarr_key,
                method="PUT",
                payload=payload,
            )
            if status not in (200, 201, 202):
                raise RuntimeError(
                    f"Prowlarr: failed updating FlareSolverr proxy (HTTP {status}): {body}"
                )
            resolved_proxy = response_data if isinstance(response_data, dict) else dict(payload)
            service.log(f"[OK] Prowlarr: updated FlareSolverr proxy '{proxy_name}' ({host})")
        else:
            status, response_data, body = service.http_request(
                prowlarr_url,
                "/api/v1/indexerProxy",
                api_key=prowlarr_key,
                method="POST",
                payload=payload,
            )
            if status not in (200, 201, 202):
                raise RuntimeError(
                    f"Prowlarr: failed creating FlareSolverr proxy (HTTP {status}): {body}"
                )
            resolved_proxy = response_data if isinstance(response_data, dict) else dict(payload)
            service.log(f"[OK] Prowlarr: created FlareSolverr proxy '{proxy_name}' ({host})")

        # Resolve proxy_id from response → payload → current, in
        # that order. Prowlarr's PUT/POST sometimes returns an empty
        # body or ``{}``; without the fallback chain, callers see a
        # successful "updated FlareSolverr proxy" log followed by
        # ``no FlareSolverr proxy configured`` rejections on every
        # CF-protected indexer in the same bootstrap. (v1.0.108
        # 04:27:32-38 incident.)
        def _coerce_int(v):
            try:
                return int(v) if v is not None else None
            except (TypeError, ValueError):
                return None
        proxy_id_int = None
        if isinstance(resolved_proxy, dict):
            proxy_id_int = _coerce_int(resolved_proxy.get("id"))
        if proxy_id_int is None:
            proxy_id_int = _coerce_int(payload.get("id"))
        if proxy_id_int is None and current:
            proxy_id_int = _coerce_int(current.get("id"))
        if proxy_id_int is None:
            # Last resort: re-list and find by implementation/name.
            st, plist, _ = service.http_request(
                prowlarr_url, "/api/v1/indexerProxy",
                api_key=prowlarr_key,
            )
            if st == 200 and isinstance(plist, list):
                match = next(
                    (p for p in plist
                     if p.get("implementation") == "FlareSolverr"
                     or str(p.get("name") or "").strip().lower()
                     == proxy_name.lower()),
                    None,
                )
                if match:
                    proxy_id_int = _coerce_int(match.get("id"))

        if not test_connection:
            return proxy_id_int

        status, _, body = service.http_request(
            prowlarr_url,
            "/api/v1/indexerProxy/test",
            api_key=prowlarr_key,
            method="POST",
            payload=resolved_proxy,
        )
        if status in (200, 201, 202):
            service.log("[OK] Prowlarr: FlareSolverr proxy connection test passed")
            return proxy_id_int
        raise RuntimeError(f"Prowlarr: FlareSolverr proxy test failed (HTTP {status}): {body}")


_instance = ProwlarrProxyOps()
ensure_flaresolverr_proxy = _instance.ensure_flaresolverr_proxy
