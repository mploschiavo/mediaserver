"""Phase 16-B migration shim — moved to media_stack.application.auth.admin_bootstrap.

Re-exports preserve back-compat for callers that still import via
``media_stack.core.auth.admin_bootstrap``. Removed in Phase 16-F.
"""

from media_stack.application.auth.admin_bootstrap import *  # noqa: F401, F403
from media_stack.application.auth import admin_bootstrap as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
