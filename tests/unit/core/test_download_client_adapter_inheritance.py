"""Regression tests for download-client adapter class identity.

The factory at ``services.adapter_factory.build_adapter_registry`` does
``issubclass(adapter_cls, base_class)`` where ``base_class`` is
``services.download_client_adapters.base.DownloadClientAdapterBase``.

For the check to pass, every concrete adapter (Grabit, qBittorrent,
SABnzbd, Transmission, generic torrent/usenet) must transitively
inherit from THAT exact class object — not a sibling copy with the
same name in a different module. The repo's ADR-0002 migration left
two parallel base modules (``services.base`` and ``domain.base``)
with byte-identical content; importing one and inheriting via the
other yields a ``must inherit from DownloadClientAdapterBase`` error
even though the names match. The fix is for ``services.base`` to
re-export ``DownloadClientAdapterBase`` from ``domain.base`` so they
are literally the same class object.

This test file pins that invariant — adding a third copy or breaking
the shim re-export would regress the legacy bootstrap pipeline.
"""

from __future__ import annotations

import pytest


def test_services_and_domain_base_are_the_same_class() -> None:
    from media_stack.services.download_client_adapters.base import (
        DownloadClientAdapterBase as services_base,
    )
    from media_stack.domain.download_client_adapters.base import (
        DownloadClientAdapterBase as domain_base,
    )
    assert services_base is domain_base, (
        "services.download_client_adapters.base.DownloadClientAdapterBase "
        "must BE (not just equal) "
        "domain.download_client_adapters.base.DownloadClientAdapterBase. "
        "If two modules ship parallel definitions, issubclass() fails for "
        "any adapter inheriting via the wrong path."
    )


@pytest.mark.parametrize(
    "module_path,class_name",
    [
        (
            "media_stack.services.download_client_adapters.grabit",
            "GrabitDownloadClientAdapter",
        ),
        (
            "media_stack.adapters.download_client_adapters.grabit",
            "GrabitDownloadClientAdapter",
        ),
        (
            "media_stack.adapters.qbittorrent.download_client_adapter",
            "QbittorrentDownloadClientAdapter",
        ),
        (
            "media_stack.adapters.sabnzbd.download_client_adapter",
            "SabnzbdDownloadClientAdapter",
        ),
        (
            "media_stack.adapters.download_client_adapters.transmission",
            "TransmissionDownloadClientAdapter",
        ),
        (
            "media_stack.adapters.download_client_adapters.generic",
            "GenericDownloadClientAdapter",
        ),
    ],
)
def test_concrete_adapters_inherit_from_factory_base(
    module_path: str, class_name: str,
) -> None:
    """Every adapter the contract registers must subclass the SAME
    ``DownloadClientAdapterBase`` that ``adapter_factory`` checks
    against. Catches the class-identity drift the Grabit error
    surfaced before the shim was applied."""
    import importlib

    from media_stack.services.download_client_adapters.base import (
        DownloadClientAdapterBase,
    )

    module = importlib.import_module(module_path)
    adapter_cls = getattr(module, class_name)
    assert issubclass(adapter_cls, DownloadClientAdapterBase), (
        f"{module_path}:{class_name} does not inherit from "
        f"the factory's DownloadClientAdapterBase. "
        f"This will reproduce the legacy-pipeline error: "
        f"'must inherit from DownloadClientAdapterBase'."
    )
