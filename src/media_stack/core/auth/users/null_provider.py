"""Phase 16-B migration shim — moved to media_stack.infrastructure.auth.users.null_provider."""

from media_stack.infrastructure.auth.users.null_provider import *  # noqa: F401, F403
from media_stack.infrastructure.auth.users import null_provider as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
