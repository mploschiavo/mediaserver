"""Download-client-adapter port implementations.

ADR-0002 Phase 16-E (cross-cutting download_client_adapters). Concrete
adapter classes that implement the
:class:`media_stack.domain.download_client_adapters.base.DownloadClientAdapterBase`
port live here. App-specific HTTP adapters (qbittorrent, sabnzbd) are
already migrated and live under ``media_stack.adapters.<svc>``; this
package holds the cross-cutting fallbacks (generic / generic-usenet)
plus the trivial usenet derivatives (grabit, jdownloader, nzbget) and
the future-ready transmission stub.
"""
