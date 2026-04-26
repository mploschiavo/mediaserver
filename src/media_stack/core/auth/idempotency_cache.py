"""Phase 16-B migration shim — moved to media_stack.domain.auth.idempotency_cache.

Re-exports preserve back-compat for callers that still import via
``media_stack.core.auth.idempotency_cache``. Removed in Phase 16-F.
"""

from media_stack.domain.auth.idempotency_cache import *  # noqa: F401, F403
from media_stack.domain.auth import idempotency_cache as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
