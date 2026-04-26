"""Phase 16-B migration shim — moved to media_stack.infrastructure.auth.authelia_oidc_crypto.

Re-exports preserve back-compat for callers that still import via
``media_stack.core.auth.authelia_oidc_crypto``. Removed in Phase 16-F.
"""

from media_stack.infrastructure.auth.authelia_oidc_crypto import *  # noqa: F401, F403
from media_stack.infrastructure.auth import authelia_oidc_crypto as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
