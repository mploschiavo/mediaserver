"""Phase 16-B migration shim — moved to media_stack.domain.auth.gateway_policy.

Re-exports preserve back-compat for callers that still import via
``media_stack.core.auth.gateway_policy``. Removed in Phase 16-F.
"""

from media_stack.domain.auth.gateway_policy import *  # noqa: F401, F403
from media_stack.domain.auth import gateway_policy as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
