"""Phase 16-B migration shim — moved to media_stack.domain.auth.users.visibility_protocols."""

from media_stack.domain.auth.users.visibility_protocols import *  # noqa: F401, F403
from media_stack.domain.auth.users import visibility_protocols as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
