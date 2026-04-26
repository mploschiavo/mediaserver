"""Phase 16-B migration shim — moved to media_stack.infrastructure.auth.users.user_store."""

from media_stack.infrastructure.auth.users.user_store import *  # noqa: F401, F403
from media_stack.infrastructure.auth.users import user_store as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
