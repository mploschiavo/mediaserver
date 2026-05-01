"""Migration shim — see ``media_stack.domain.download_client_adapters.base``.

Same content as the canonical module under another tree (the
ADR-0002 migration left two parallel homes for the same code).
Re-exported here so ``isinstance`` / ``issubclass`` checks against
``services.download_client_adapters.base.DownloadClientAdapterBase``
agree with checks against the canonical
``domain.download_client_adapters.base.DownloadClientAdapterBase``.
Without this re-export, every adapter that imports its base from
``adapters.download_client_adapters.usenet`` (which pulls the
domain copy) was failing the factory's
``must inherit from DownloadClientAdapterBase`` check.

Delete this shim once nothing under ``src/`` or ``tests/`` imports
from ``media_stack.services.download_client_adapters.base`` directly.
"""

from media_stack.domain.download_client_adapters.base import *  # noqa: F401, F403
from media_stack.domain.download_client_adapters.base import (  # noqa: F401
    BoolCfgFn,
    DownloadClientAdapterBase,
    DownloadClientAdapterContext,
    DownloadClientAdapterDependencies,
    InvokeHandlerFn,
    LogFn,
    NormalizeUrlFn,
    WaitForServiceFn,
)
