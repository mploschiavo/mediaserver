"""Phase 16-B migration shim — moved to media_stack.domain.auth.rate_limiter.

Re-exports preserve back-compat for callers that still import via
``media_stack.core.auth.rate_limiter``. Removed in Phase 16-F.
"""

from media_stack.domain.auth.rate_limiter import *  # noqa: F401, F403
from media_stack.domain.auth import rate_limiter as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
