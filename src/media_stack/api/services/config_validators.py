"""Semantic config validators.

The integrity probe in ``config_integrity`` answers "does this
file parse?" That's necessary but not sufficient: Authelia 4.38
crashlooped on 2026-04-20 with a perfectly well-formed YAML file
whose ``session.cookies[0].domain`` was the bare ``"local"`` —
syntactically fine, semantically rejected by Authelia.

Each validator here takes the parsed config object (already
loaded by the integrity probe) and returns a list of
``ValidationError`` describing semantic problems. An empty list
means the config is good. The integrity probe surfaces these as
``status="invalid"`` (distinct from ``status="corrupt"`` for
parse failures), so the dashboard can tell the user "config
parses but Authelia will reject it" instead of either lying or
showing a generic error.

These validators encode rules we've actually been bitten by, not
the full upstream config schema. The point is to catch the
specific shapes that previously crashlooped a real container —
not to reimplement every validator the app already has."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class ValidationError:
    """One semantic problem found in a config. ``rule`` is a stable
    snake_case identifier the auto-heal job and tests can key off."""

    rule: str
    message: str


class ConfigValidators:
    """Semantic validators for service configs.

    Per ADR-0012, helpers live as plain instance methods; module-level
    aliases below preserve the public API surface."""

    def validate_authelia_config(self, config: Any) -> list[ValidationError]:
        """Authelia 4.38+ rules we've been bitten by.

        Validates the *output* of ``yaml.safe_load`` on
        ``configuration.yml``. Returns an empty list on success.

        Rules:

        - ``session.cookies[*].domain`` must contain a period or be an
          IP — otherwise Authelia logs ``"is not a valid cookie domain"``
          and crashloops. (2026-04-20 incident.)
        - ``session.cookies[*].authelia_url`` host must be under the
          same cookie domain — otherwise the login portal redirect
          lands outside the cookie scope and the user loops at sign-in.
        - ``session.cookies[*].default_redirection_url`` host must be
          under the same cookie domain — same failure mode.
        - ``access_control.rules[*].domain`` entries can't contain
          consecutive dots (e.g. ``"*..local"``) — typo from a
          generator that joined ``"*"`` and ``".local"``.
        """
        errors: list[ValidationError] = []
        if not isinstance(config, dict):
            return [ValidationError(
                "authelia_root_not_object",
                "Authelia configuration must be a YAML mapping at the root.",
            )]

        session = config.get("session") or {}
        cookies = session.get("cookies") if isinstance(session, dict) else None
        if isinstance(cookies, list):
            for idx, cookie in enumerate(cookies):
                errors.extend(
                    self._validate_cookie(
                        idx, cookie if isinstance(cookie, dict) else {}
                    )
                )

        ac = config.get("access_control") or {}
        rules = ac.get("rules") if isinstance(ac, dict) else None
        if isinstance(rules, list):
            for idx, rule in enumerate(rules):
                if not isinstance(rule, dict):
                    continue
                for d in (rule.get("domain") or []):
                    if not isinstance(d, str):
                        continue
                    if ".." in d:
                        errors.append(ValidationError(
                            "authelia_access_control_double_dot",
                            f"access_control.rules[{idx}].domain contains "
                            f"consecutive dots: {d!r}. This is the classic "
                            f"'*..local' artifact from a generator that joined "
                            f"a wildcard with a leading-dot domain.",
                        ))

        return errors

    def _validate_cookie(
        self, idx: int, cookie: dict
    ) -> list[ValidationError]:
        errors: list[ValidationError] = []
        domain = str(cookie.get("domain") or "").strip()
        if not domain:
            errors.append(ValidationError(
                "authelia_cookie_domain_empty",
                f"session.cookies[{idx}].domain is empty.",
            ))
            return errors
        if domain.startswith(".") or domain.endswith("."):
            errors.append(ValidationError(
                "authelia_cookie_domain_dot_edge",
                f"session.cookies[{idx}].domain has a leading or trailing "
                f"dot: {domain!r}. Authelia rejects these.",
            ))
        is_ip = self._looks_like_ip(domain)
        if "." not in domain and not is_ip:
            errors.append(ValidationError(
                "authelia_cookie_domain_single_label",
                f"session.cookies[{idx}].domain={domain!r} is a single "
                "label — Authelia 4.38 requires a period or an IP. "
                "This is the 2026-04-20 production crashloop shape.",
            ))

        # The portal and default_redirection_url must be under the cookie
        # scope. If they aren't, the post-login redirect lands outside the
        # cookie's eTLD+1 and the browser drops the session cookie.
        for url_key in ("authelia_url", "default_redirection_url"):
            url = str(cookie.get(url_key) or "").strip()
            if not url:
                continue
            host = self._host_from_url(url)
            if not host:
                continue
            if not self._host_under_domain(host, domain):
                errors.append(ValidationError(
                    "authelia_url_outside_cookie_scope",
                    f"session.cookies[{idx}].{url_key}={url!r} (host {host!r}) "
                    f"is not under cookie domain {domain!r}. After sign-in "
                    f"the browser will drop the session cookie and the user "
                    f"loops at the portal.",
                ))
        return errors

    def _looks_like_ip(self, s: str) -> bool:
        parts = s.split(".")
        return len(parts) == 4 and all(
            p.isdigit() and 0 <= int(p) <= 255 for p in parts
        )

    def _host_from_url(self, url: str) -> str:
        if "://" not in url:
            return ""
        rest = url.split("://", 1)[1]
        return rest.split("/", 1)[0].split(":", 1)[0].lower()

    def _host_under_domain(self, host: str, domain: str) -> bool:
        """Strict ``host == domain`` or ``host endswith "." + domain``.
        Avoids the ``a.com`` matching ``a.coma`` confusion."""
        host = host.lower()
        domain = domain.lower()
        return host == domain or host.endswith("." + domain)

    def get_validator(
        self,
        service_id: str,
    ) -> Callable[[Any], list[ValidationError]] | None:
        return _VALIDATORS.get(service_id)

    def validators_for(
        self,
        service_ids: Iterable[str],
    ) -> dict[str, Callable[[Any], list[ValidationError]]]:
        return {
            sid: _VALIDATORS[sid]
            for sid in service_ids
            if sid in _VALIDATORS
        }


_INSTANCE = ConfigValidators()

# Public API aliases — preserve the module-level call sites.
validate_authelia_config = _INSTANCE.validate_authelia_config
get_validator = _INSTANCE.get_validator
validators_for = _INSTANCE.validators_for


# ----------------------------------------------------------------------
# Registry — services with semantic validators beyond their parser.
# ----------------------------------------------------------------------


_VALIDATORS: dict[str, Callable[[Any], list[ValidationError]]] = {
    "authelia": validate_authelia_config,
}
