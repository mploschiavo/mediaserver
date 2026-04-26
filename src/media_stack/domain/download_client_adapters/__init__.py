"""Download-client-adapter domain types — pure protocols + value objects.

ADR-0002 Phase 16-E (cross-cutting download_client_adapters) — only the
I/O-free, framework-free parts of the download-client adapter port live
here. The :class:`DownloadClientAdapterBase` dataclass, the
:class:`DownloadClientAdapterContext` value object, and the
:class:`DownloadClientAdapterDependencies` callable bundle are the
contract every concrete adapter implementation must satisfy.

Concrete adapter implementations (qbittorrent, sabnzbd, transmission,
generic usenet derivatives) live under
``media_stack.adapters.download_client_adapters`` — they implement the
port and may import from this package freely. The factory / registry
that resolves a class by service id lives in
``media_stack.application.download_client_adapters``.

This package may be imported from ``application/`` and ``adapters/``
freely — it depends on nothing outside the standard library.
"""
