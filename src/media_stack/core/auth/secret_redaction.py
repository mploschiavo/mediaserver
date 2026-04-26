"""Phase 16-B migration shim — moved to media_stack.domain.auth.secret_redaction.

Re-exports preserve back-compat for callers that still import via
``media_stack.core.auth.secret_redaction``. Removed in Phase 16-F.
"""

from media_stack.domain.auth.secret_redaction import *  # noqa: F401, F403
from media_stack.domain.auth import secret_redaction as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
