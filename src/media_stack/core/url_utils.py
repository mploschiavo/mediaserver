"""URL / path helpers shared across services that talk to upstream apps.

Extracted from three duplicate copies (AuthService,
ServarrProwlarrOps, ProwlarrRuntimeOps) so the normalization
logic can't drift between them.
"""

from __future__ import annotations


def normalize_url_base(value: object) -> str:
    """Return ``value`` as a leading-slash, no-trailing-slash URL
    base. Used to coerce operator-supplied path bases like
    ``"/sonarr"``, ``"sonarr/"``, ``" /sonarr/ "`` into the canonical
    ``"/sonarr"`` form expected by upstream HTTP clients. The lone-
    slash case (root) is preserved verbatim as ``"/"``.

    >>> normalize_url_base("sonarr")
    '/sonarr'
    >>> normalize_url_base("/sonarr/")
    '/sonarr'
    >>> normalize_url_base("/")
    '/'
    >>> normalize_url_base("")
    ''
    >>> normalize_url_base(None)
    ''
    """
    token = str(value or "").strip()
    if not token:
        return ""
    if not token.startswith("/"):
        token = f"/{token}"
    if token != "/":
        token = token.rstrip("/")
    return token
