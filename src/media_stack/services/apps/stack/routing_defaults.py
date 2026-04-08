"""Default routing priority slugs for Envoy gateway redirect logic.

These define which service slugs get priority when choosing the default
HTML redirect target on a shared gateway host. Platform code imports
these instead of hardcoding service names.
"""

# Slug priority for the default HTML redirect on the gateway host.
# The first slug that matches a registered route wins; the second
# is used as fallback if the first is not present.
DEFAULT_REDIRECT_PRIORITY_SLUG = "jellyfin"
DEFAULT_REDIRECT_FALLBACK_SLUG = "homepage"

# Dashboard slug used for the bare app-root redirect (e.g. /app -> /app/homepage).
APP_ROOT_DASHBOARD_SLUG = "homepage"
