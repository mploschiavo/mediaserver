"""Phase 16-B migration shim — moved to media_stack.adapters.auth.provider_registry.

Re-exports preserve back-compat for callers that still import via
``media_stack.core.auth.provider_registry``. Removed in Phase 16-F.
"""

from media_stack.adapters.auth.provider_registry import *  # noqa: F401, F403
from media_stack.adapters.auth import provider_registry as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
