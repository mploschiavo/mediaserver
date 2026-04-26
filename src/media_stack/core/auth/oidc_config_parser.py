"""Phase 16-B migration shim — moved to media_stack.domain.auth.oidc_config_parser.

Re-exports preserve back-compat for callers that still import via
``media_stack.core.auth.oidc_config_parser``. Removed in Phase 16-F.
"""

from media_stack.domain.auth.oidc_config_parser import *  # noqa: F401, F403
from media_stack.domain.auth import oidc_config_parser as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
