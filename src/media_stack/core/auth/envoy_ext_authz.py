"""Phase 16-B migration shim — moved to media_stack.application.auth.envoy_ext_authz.

Re-exports preserve back-compat for callers that still import via
``media_stack.core.auth.envoy_ext_authz``. Removed in Phase 16-F.
"""

from media_stack.application.auth.envoy_ext_authz import *  # noqa: F401, F403
from media_stack.application.auth import envoy_ext_authz as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
