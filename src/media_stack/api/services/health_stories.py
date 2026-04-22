"""Composite health "stories" — user-facing impact statements
derived from per-service signals.

The four signals the dashboard already collects are great for an
operator who knows the architecture, but useless for a user who
just wants to know "did my new movie download last night?". This
module composes them into short, plain-language stories:

- "Downloads broken — Prowlarr's config is corrupt; auto-heal in
  progress."
- "Playback broken — Jellyfin is down."
- "Sign-in broken — Authelia is unreachable; SSO-protected apps
  will show a generic browser error."
- "All systems healthy."

Every rule returns a ``Story`` (or ``None`` if it doesn't fire).
The active stories are sorted ``critical → warn → info`` so the
banner shows the worst thing first. ``ok`` stories are kept in
the response so the dashboard can show a green "downloads OK"
chip when the rule is positively satisfied — the absence of a
story would be ambiguous (rule didn't fire vs. signal missing).

This is deliberately a flat rule set, not a DSL. Five rules
covering downloads, playback, search, auth, and auto-heal status
is enough for the failure modes we've actually seen, and adding
the sixth is a 10-line code change."""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict, field
from typing import Iterable


_DOWNLOAD_PATH = ("prowlarr", "sonarr", "radarr", "lidarr", "readarr",
                  "qbittorrent", "sabnzbd")
_PLAYBACK_APPS = ("jellyfin", "plex")
_SEARCH_APPS = ("jellyseerr", "overseerr", "openseerr")
_AUTH_APPS = ("authelia", "authentik")


@dataclass
class Story:
    """One composite health story. The dashboard renders the
    headline as a banner, the description as a tooltip / detail
    pane, and ``next_action`` as a CTA button or status pill."""

    id: str
    severity: str          # "critical" | "warn" | "info" | "ok"
    headline: str
    description: str
    affected_services: list[str] = field(default_factory=list)
    cause: str = ""
    auto_heal_status: str = "n/a"  # "healing" | "healed_recently" | "needs_manual" | "n/a"
    next_action: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------
# Helpers — keep rule code declarative
# ----------------------------------------------------------------------


def _is_down(health: dict, svc_id: str) -> bool:
    """A service is "down" if the HTTP probe says ``error``. ``warn``
    is treated as still functional — typically a slow probe. The
    crashloop signal is consulted separately."""
    entry = health.get(svc_id) or {}
    return str(entry.get("status", "")).lower() == "error"


def _is_corrupt(integrity: dict, svc_id: str) -> bool:
    entry = integrity.get(svc_id) or {}
    return str(entry.get("status", "")).lower() == "corrupt"


def _crashloop_cause(crashloops: dict, svc_id: str) -> str:
    entry = crashloops.get(svc_id) or {}
    cause = str(entry.get("cause", "")).lower()
    if cause and cause != "healthy":
        return cause
    return ""


def _heal_status_for(
    heal_events: Iterable[dict],
    affected: Iterable[str],
    *,
    now_ts: float,
    fresh_window_s: float = 5 * 60,
) -> str:
    """Look at recent heal events. If one is for an affected
    service and was attempted within the last 5 minutes, report:

    - ``healing`` — the most recent attempt was a restore (the pod
      may still be restarting); user should expect recovery soon.
    - ``healed_recently`` — restore succeeded, restart fired.
    - ``needs_manual`` — a heal was attempted but skipped
      (no snapshot) or failed.
    - ``n/a`` — no recent heal touching these services.
    """
    affected_set = set(affected)
    for event in heal_events:
        if event.get("service_id") not in affected_set:
            continue
        if (now_ts - float(event.get("timestamp") or 0)) > fresh_window_s:
            continue
        action = str(event.get("action") or "")
        restarted = bool(event.get("restarted"))
        if action == "restored" and restarted:
            return "healed_recently"
        if action == "restored":
            return "healing"
        return "needs_manual"
    return "n/a"


# ----------------------------------------------------------------------
# Rules
# ----------------------------------------------------------------------


def _rule_downloads(
    *,
    health: dict, integrity: dict, crashloops: dict,
    heal_events: list[dict], now_ts: float,
) -> Story | None:
    """A "downloads broken" story fires when ANY of:

    - Prowlarr is down/crashlooping/corrupt → no indexers feed the
      *arrs.
    - All download clients (qBit + SAB) are down → nothing to
      receive new releases.
    - Both Sonarr AND Radarr are down → no orchestration pulling
      new content even if indexers exist.

    On healthy, returns the green ok story so the dashboard can
    affirmatively show "downloads OK"."""
    affected: list[str] = []
    cause_parts: list[str] = []

    prowlarr_corrupt = _is_corrupt(integrity, "prowlarr")
    prowlarr_crash = _crashloop_cause(crashloops, "prowlarr")
    prowlarr_down = _is_down(health, "prowlarr")
    if prowlarr_corrupt or prowlarr_crash or prowlarr_down:
        affected.append("prowlarr")
        if prowlarr_corrupt:
            cause_parts.append("Prowlarr's config file is corrupt")
        elif prowlarr_crash:
            cause_parts.append(
                f"Prowlarr is crashlooping ({prowlarr_crash})")
        else:
            cause_parts.append("Prowlarr is unreachable")

    qbit_down = _is_down(health, "qbittorrent")
    sab_down = _is_down(health, "sabnzbd")
    if qbit_down and sab_down:
        affected.extend(["qbittorrent", "sabnzbd"])
        cause_parts.append("Both download clients are down")

    sonarr_down = _is_down(health, "sonarr")
    radarr_down = _is_down(health, "radarr")
    if sonarr_down and radarr_down:
        affected.extend(["sonarr", "radarr"])
        cause_parts.append("Sonarr and Radarr are both down")

    if not affected:
        return Story(
            id="downloads_ok",
            severity="ok",
            headline="Downloads are working",
            description="Indexers, the *arrs, and download clients are all responding.",
            affected_services=[s for s in _DOWNLOAD_PATH if (health.get(s) or {}).get("status") == "ok"],
        )

    heal_status = _heal_status_for(
        heal_events, affected, now_ts=now_ts,
    )
    cause = "; ".join(cause_parts)
    headline = "Downloads are broken — " + cause_parts[0].lower().rstrip(".") + "."
    if heal_status == "healing":
        next_action = "Auto-heal is restoring config — typically recovers in 60s."
    elif heal_status == "healed_recently":
        next_action = "Auto-heal just ran. Refresh in a moment to confirm."
    elif heal_status == "needs_manual":
        next_action = "Auto-heal couldn't fix this on its own. Restore from a backup or check the service logs."
    else:
        next_action = "Check the affected services in the table below."
    return Story(
        id="downloads_broken",
        severity="critical",
        headline=headline,
        description=cause + ".",
        affected_services=affected,
        cause=cause,
        auto_heal_status=heal_status,
        next_action=next_action,
    )


def _rule_playback(
    *, health: dict, integrity: dict, crashloops: dict,
    heal_events: list[dict], now_ts: float,
) -> Story | None:
    affected = [
        sid for sid in _PLAYBACK_APPS
        if _is_down(health, sid)
        or _is_corrupt(integrity, sid)
        or _crashloop_cause(crashloops, sid)
    ]
    media_servers_present = [
        sid for sid in _PLAYBACK_APPS if sid in health
    ]
    if not media_servers_present:
        return None
    if not affected:
        return Story(
            id="playback_ok",
            severity="ok",
            headline="Playback is working",
            description="Your media server is responding to play requests.",
            affected_services=media_servers_present,
        )
    cause = ", ".join(affected) + " unreachable"
    heal_status = _heal_status_for(heal_events, affected, now_ts=now_ts)
    return Story(
        id="playback_broken",
        severity="critical",
        headline="Playback is broken — your media server is down",
        description=(
            f"{cause}. Existing client sessions may keep playing "
            "from buffer; new playback requests will fail."
        ),
        affected_services=affected,
        cause=cause,
        auto_heal_status=heal_status,
        next_action=(
            "Auto-heal is restoring config." if heal_status == "healing"
            else "Open the service to check container logs."
        ),
    )


def _rule_auth(
    *, health: dict, integrity: dict, crashloops: dict,
    heal_events: list[dict], now_ts: float,
) -> Story | None:
    affected = [
        sid for sid in _AUTH_APPS
        if sid in health and (
            _is_down(health, sid) or _is_corrupt(integrity, sid)
        )
    ]
    auth_present = [sid for sid in _AUTH_APPS if sid in health]
    if not auth_present:
        return None
    if not affected:
        return Story(
            id="auth_ok",
            severity="ok",
            headline="Sign-in is working",
            description="The SSO provider is responding.",
            affected_services=auth_present,
        )
    heal_status = _heal_status_for(heal_events, affected, now_ts=now_ts)
    return Story(
        id="auth_broken",
        severity="critical",
        headline="Sign-in is broken — SSO provider is down",
        description=(
            "Browsers will see a generic gateway error when trying "
            "to reach SSO-protected apps. Direct-access endpoints "
            "(like the controller dashboard) keep working."
        ),
        affected_services=affected,
        cause=", ".join(affected) + " unreachable",
        auto_heal_status=heal_status,
        next_action=(
            "Auto-heal is restoring config." if heal_status == "healing"
            else "Check the SSO container's logs and config."
        ),
    )


def _rule_search(
    *, health: dict, integrity: dict, crashloops: dict,
    heal_events: list[dict], now_ts: float,
) -> Story | None:
    affected = [
        sid for sid in _SEARCH_APPS
        if sid in health and (
            _is_down(health, sid) or _is_corrupt(integrity, sid)
            or _crashloop_cause(crashloops, sid)
        )
    ]
    seerr_present = [sid for sid in _SEARCH_APPS if sid in health]
    if not seerr_present:
        return None
    if not affected:
        return Story(
            id="search_ok",
            severity="ok",
            headline="Content search is working",
            description="Users can search and request new content.",
            affected_services=seerr_present,
        )
    heal_status = _heal_status_for(heal_events, affected, now_ts=now_ts)
    return Story(
        id="search_broken",
        severity="warn",
        headline="Content search is degraded — request UI is down",
        description=(
            f"{', '.join(affected)} can't be reached. Existing "
            "downloads are unaffected; users won't be able to "
            "request new titles until this is back."
        ),
        affected_services=affected,
        cause=", ".join(affected) + " unreachable",
        auto_heal_status=heal_status,
    )


def _rule_auto_heal_busy(
    *, health: dict, integrity: dict, crashloops: dict,
    heal_events: list[dict], now_ts: float,
) -> Story | None:
    """Surface a quiet-info story if auto-heal recently took an
    action — useful even if the other rules are green, so users
    see "the system fixed itself" rather than wondering."""
    fresh = [
        e for e in heal_events
        if (now_ts - float(e.get("timestamp") or 0)) <= 5 * 60
    ]
    if not fresh:
        return None
    restored = [e for e in fresh if e.get("action") == "restored"]
    if not restored:
        return None
    affected = sorted({e.get("service_id") for e in restored if e.get("service_id")})
    return Story(
        id="auto_heal_active",
        severity="info",
        headline=f"Auto-heal restored {len(restored)} service(s) recently",
        description=(
            "A corrupt config was detected and replaced from the "
            "most recent healthy snapshot. Pods were restarted; "
            "verify each service in the table below."
        ),
        affected_services=list(affected),
        auto_heal_status="healed_recently",
        next_action="No action required.",
    )


_RULES = [
    _rule_downloads,
    _rule_playback,
    _rule_auth,
    _rule_search,
    _rule_auto_heal_busy,
]


_SEVERITY_ORDER = {"critical": 0, "warn": 1, "info": 2, "ok": 3}


def compose(
    *,
    health: dict,
    integrity: dict,
    crashloops: dict,
    heal_events: list[dict],
    now_ts: float | None = None,
) -> list[dict]:
    """Run all rules; return the resulting stories sorted by
    severity (worst first)."""
    import time as _time
    ts = now_ts if now_ts is not None else _time.time()
    stories: list[Story] = []
    for rule in _RULES:
        try:
            s = rule(
                health=health,
                integrity=integrity,
                crashloops=crashloops,
                heal_events=heal_events,
                now_ts=ts,
            )
        except Exception as exc:
            # A buggy rule must not take the whole story layer down.
            logging.getLogger("media_stack").debug(
                "[DEBUG] story rule failed: %s", exc,
            )
            continue
        if s is not None:
            stories.append(s)
    stories.sort(key=lambda s: _SEVERITY_ORDER.get(s.severity, 9))
    return [s.to_dict() for s in stories]


# ----------------------------------------------------------------------
# Convenience: pull from the live services so the GET handler can
# call one function.
# ----------------------------------------------------------------------


def compose_live() -> dict:
    """Hit each underlying probe and return the stories. Slow
    enough that the dashboard should poll this every 15-30s, not
    on every render."""
    from media_stack.api.cache import api_cache
    from . import health as health_svc
    from . import config_integrity as integrity_svc
    from . import crashloop as crashloop_svc
    from . import auto_heal as autoheal_svc

    health_result = health_svc.probe_services(api_cache).get("services", {})
    integrity_result = integrity_svc.check_all()
    crashloop_result = crashloop_svc.check_all()
    heal_events = autoheal_svc.default().recent_events(limit=20)
    stories = compose(
        health=health_result,
        integrity=integrity_result,
        crashloops=crashloop_result,
        heal_events=heal_events,
    )
    return {
        "stories": stories,
        "checked_at": __import__("time").time(),
    }
