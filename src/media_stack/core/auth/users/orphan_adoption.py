"""Phase 16-B migration shim — moved to media_stack.application.auth.users.orphan_adoption."""

from media_stack.application.auth.users.orphan_adoption import *  # noqa: F401, F403
from media_stack.application.auth.users import orphan_adoption as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
