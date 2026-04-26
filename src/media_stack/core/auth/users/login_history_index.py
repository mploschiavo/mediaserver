"""Phase 16-B migration shim — moved to media_stack.application.auth.users.login_history_index."""

from media_stack.application.auth.users.login_history_index import *  # noqa: F401, F403
from media_stack.application.auth.users import login_history_index as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
