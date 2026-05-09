"""URL / path helpers shared across services that talk to upstream apps.

Extracted from three duplicate copies (AuthService,
ServarrProwlarrOps, ProwlarrRuntimeOps) so the normalization
logic can't drift between them.

ADR-0012: top-level FunctionDef count must stay at zero. The lone
``normalize_url_base`` helper is bundled on ``UrlUtils`` and re-exported
as a module-level alias so every existing
``from media_stack.core.url_utils import normalize_url_base`` keeps
working with the same signature.
"""

from __future__ import annotations


__all__ = ["UrlUtils", "normalize_url_base"]


class UrlUtils:
    """URL/path helpers bundled per ADR-0012.

    Plain instance methods — no ``@staticmethod`` — so the class is a
    legitimate dispatch surface. Module-level aliases below preserve
    the original free-function names so callers keep importing
    ``normalize_url_base`` without churn.
    """

    def normalize_url_base(self, value: object) -> str:
        """Return ``value`` as a leading-slash, no-trailing-slash URL
        base. Used to coerce operator-supplied path bases like
        ``"/sonarr"``, ``"sonarr/"``, ``" /sonarr/ "`` into the canonical
        ``"/sonarr"`` form expected by upstream HTTP clients. The lone-
        slash case (root) is preserved verbatim as ``"/"``.

        >>> UrlUtils().normalize_url_base("sonarr")
        '/sonarr'
        >>> UrlUtils().normalize_url_base("/sonarr/")
        '/sonarr'
        >>> UrlUtils().normalize_url_base("/")
        '/'
        >>> UrlUtils().normalize_url_base("")
        ''
        >>> UrlUtils().normalize_url_base(None)
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


_INSTANCE = UrlUtils()


# Module-level alias. Exists so callers keep writing
# ``from media_stack.core.url_utils import normalize_url_base`` with the
# same call signature as the legacy free function.
normalize_url_base = _INSTANCE.normalize_url_base
