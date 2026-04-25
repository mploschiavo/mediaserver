"""Cross-service ``SessionAdminProvider`` implementations.

These providers are dedicated to live-session visibility: they query
each backend's session API, filter by username, and emit
``ExternalSession`` rows the ``SessionAggregator`` can dedupe and
render.

They live in ``services.security.providers`` (rather than alongside
each backend's ``UserProvider``) because:

- The ``UserProvider`` impls already in ``services.apps.<svc>.user_provider``
  filter ``list_sessions`` by the backend's *external_id* (Jellyfin's
  ``UserId``, Jellyseerr's numeric id). The ``SessionAggregator``
  passes the controller-side **username** instead — the two ID
  spaces are different, so without a translation layer those rows
  silently drop.
- Each provider here knows how to map ``username -> backend rows``
  in one place (typically a ``GET /Users`` lookup or a name field
  on the session row itself), keeping the existing user_provider
  contracts unchanged.
- Construction is best-effort: a provider that can't reach its
  backend at startup is silently skipped by the singleton wiring
  rather than crashing the controller.
"""

from media_stack.services.security.providers.authelia_session_provider import (
    AutheliaSessionProvider,
)
from media_stack.services.security.providers.jellyfin_session_provider import (
    JellyfinSessionProvider,
)
from media_stack.services.security.providers.jellyseerr_session_provider import (
    JellyseerrSessionProvider,
)

__all__ = [
    "AutheliaSessionProvider",
    "JellyfinSessionProvider",
    "JellyseerrSessionProvider",
]
