"""Coarse-grained device classifier for session-visibility surfaces.

The admin session-visibility UI needs to render a tiny, stable icon per
session -- "TV", "Phone", "Tablet", "Desktop", "CLI" -- rather than the
raw User-Agent string. Raw UAs are long, user-hostile, change with every
browser point release, and are trivially spoofed; a coarse class is
stable enough to be useful while making no security claims.

WHY this module exists as its own unit:
- It is the *only* place that maps UA substrings to classes, so admin UI,
  audit log rendering, and rate-limit heuristics all agree.
- It is deliberately dependency-free (pure stdlib) so it can be imported
  from hot paths (session writes) without pulling in a UA parser that
  ships megabytes of regex tables.
- It is pessimistic on purpose: when in doubt we return UNKNOWN rather
  than guessing. The UI prefers showing "Unknown device" over lying.

Detection is ordered-rule, first-match-wins. The order encodes priority:
CLI tools first (they often embed other substrings like "Mozilla" via
libraries), then TV (because many TVs are Android and would otherwise
match as phones), then Jellyfin native apps (they know what they are),
then phone/tablet/desktop heuristics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class DeviceClass(str, Enum):
    """Coarse device class surfaced to the admin UI.

    Subclasses ``str`` so ``json.dumps({"class": DeviceClass.TV})`` yields
    ``"TV"`` without a custom encoder -- important because this value
    flows through the session store, audit log, and JSON API boundaries
    without a central serializer.
    """

    TV = "TV"
    PHONE = "PHONE"
    TABLET = "TABLET"
    DESKTOP = "DESKTOP"
    CLI = "CLI"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DeviceFingerprint:
    """Result of classifying a single User-Agent string.

    ``raw_user_agent`` is kept verbatim so audit surfaces can show the
    original string on hover/expand -- the coarse class is for glanceable
    listing, not forensics. Frozen so instances are safely shareable
    across threads and hashable for caching.
    """

    device_class: DeviceClass
    os_family: str
    app_family: str
    raw_user_agent: str


# CLI rule table: substring -> app_family label.
# These are checked first because e.g. ``python-requests`` UAs sometimes
# include words like "Mozilla" in downstream libraries, and we want to
# attribute them as CLI regardless.
_CLI_TOOLS: tuple[tuple[str, str], ...] = (
    ("curl/", "curl"),
    ("wget/", "wget"),
    ("python-requests/", "python-requests"),
    ("httpie", "httpie"),
    ("postmanruntime", "PostmanRuntime"),
    ("okhttp", "okhttp"),
)


# TV rule table: substring -> (os_family, app_family).
# app_family is "" when we can't reasonably infer it; the UI shows the
# raw UA on hover in that case.
_TV_TOKENS: tuple[tuple[str, str, str], ...] = (
    ("appletv", "tvOS", ""),
    # LG's real UA uses "Web0S" with a zero digit; we match both forms
    # because both appear in the wild on different firmware versions.
    ("web0s", "webOS", ""),
    ("webos", "webOS", ""),
    ("tizen", "Tizen", ""),
    ("roku", "Roku OS", "Roku"),
    ("shield", "Android TV", ""),
    ("googletv", "Android TV", ""),
    ("android tv", "Android TV", ""),
    ("bravia", "Android TV", ""),
    ("hbbtv", "HbbTV", ""),
    ("smart-tv", "", ""),
    ("smarttv", "", ""),
)


# Browser rule table for desktop UAs. Order matters: Edge/Opera ship UA
# strings that also contain "Chrome" and "Safari", so they must be
# checked first. Chromium-based Edge uses "Edg/" (no trailing e) and
# Opera uses "OPR/".
_BROWSER_TOKENS: tuple[tuple[str, str], ...] = (
    ("edg/", "Edge"),
    ("edge/", "Edge"),
    ("opr/", "Opera"),
    ("opera/", "Opera"),
    ("firefox/", "Firefox"),
    ("chrome/", "Chrome"),
    ("safari/", "Safari"),
)


# Android-phone detection: "Android" AND "Mobile" both present. We use a
# compiled regex because substring ``"mobile"`` alone is too broad (it
# appears in "Mobile Safari" on tablets too -- but Android tablets
# historically omit the "Mobile" token, which is the actual signal).
_ANDROID_MOBILE_RE = re.compile(r"android", re.IGNORECASE)
_MOBILE_TOKEN_RE = re.compile(r"\bmobile\b", re.IGNORECASE)


class DeviceClassifier:
    """Stateless rule-driven User-Agent -> ``DeviceFingerprint`` classifier.

    Holds the ten match/dispatch helpers as plain instance methods so
    the public ``classify`` / ``classify_class`` module aliases bind to
    a singleton instance rather than loose top-level functions. The
    class itself has no state -- a single shared instance
    (``_CLASSIFIER``) is reused for every call.
    """

    def lower(self, user_agent: str) -> str:
        """Return a lowercased UA for case-insensitive substring checks.

        WHY a helper: UA strings from the wild have wildly inconsistent
        casing ("iPhone" vs "IPhone" vs "iphone" all occur in real logs),
        and doing ``.lower()`` at each call site made the rules hard to
        scan. Centralizing it makes the rule tables the source of truth.
        """

        return user_agent.lower()

    def match_cli(self, ua_lower: str) -> tuple[str, str] | None:
        """Return (os_family, app_family) if UA is a CLI tool, else None.

        CLI tools don't have a meaningful OS family from the UA alone
        (``curl/7.88.1`` tells us nothing about the host), so os_family is
        always "". The app_family is the tool name, which is what admins
        actually want to see.
        """

        for token, app in _CLI_TOOLS:
            if token in ua_lower:
                return ("", app)
        return None

    def match_tv(self, ua_lower: str) -> tuple[str, str] | None:
        """Return (os_family, app_family) if UA looks like a TV, else None."""

        for token, os_family, app in _TV_TOKENS:
            if token in ua_lower:
                return (os_family, app)
        return None

    def match_jellyfin(self, ua_lower: str) -> tuple[DeviceClass, str, str] | None:
        """Detect Jellyfin native apps and return (class, os_family, app).

        Jellyfin native apps self-identify with predictable prefixes, and
        they know their form factor better than we do -- so we trust them
        over the generic phone/tablet heuristics. This rule fires *before*
        the generic ones so a UA like ``"Jellyfin Mobile 2.5 (iOS 17)"``
        classifies as PHONE / Jellyfin and not as the generic iPhone rule.
        """

        if "jellyfin for android tv" in ua_lower:
            return (DeviceClass.TV, "Android TV", "Jellyfin")
        if "jellyfin mobile" in ua_lower:
            if "android" in ua_lower:
                os_family = "Android"
            elif "ios" in ua_lower or "iphone" in ua_lower or "ipad" in ua_lower:
                os_family = "iOS"
            else:
                os_family = ""
            return (DeviceClass.PHONE, os_family, "Jellyfin")
        if "jellyfin media player" in ua_lower:
            if "windows" in ua_lower:
                os_family = "Windows"
            elif "mac" in ua_lower or "darwin" in ua_lower:
                os_family = "macOS"
            elif "linux" in ua_lower:
                os_family = "Linux"
            else:
                os_family = ""
            return (DeviceClass.DESKTOP, os_family, "Jellyfin")
        return None

    def match_phone(self, ua_lower: str) -> tuple[str, str] | None:
        """Return (os_family, app_family) if UA is a phone, else None.

        app_family is best-effort: we look for a browser token in the same
        UA. It's normal for this to be "" on niche mobile browsers.
        """

        os_family: str | None = None
        if "iphone" in ua_lower:
            os_family = "iOS"
        elif "windows phone" in ua_lower:
            os_family = "Windows Phone"
        elif _ANDROID_MOBILE_RE.search(ua_lower) and _MOBILE_TOKEN_RE.search(ua_lower):
            os_family = "Android"
        if os_family is None:
            return None
        return (os_family, self.detect_browser(ua_lower))

    def match_tablet(self, ua_lower: str) -> tuple[str, str] | None:
        """Return (os_family, app_family) if UA is a tablet, else None.

        Android tablets are detected by the *absence* of the "Mobile" token
        in an Android UA -- that's the historical signal Google asks vendors
        to use. Kindle Fire / Silk is its own OS family because Amazon forks
        Android heavily enough that it's worth distinguishing in the UI.
        """

        os_family: str | None = None
        if "ipad" in ua_lower:
            os_family = "iPadOS"
        elif "kindle" in ua_lower or "silk" in ua_lower or "kfonwi" in ua_lower:
            os_family = "Fire OS"
        elif "gt-p" in ua_lower or "nexus 7" in ua_lower or "nexus 10" in ua_lower:
            os_family = "Android"
        elif _ANDROID_MOBILE_RE.search(ua_lower) and not _MOBILE_TOKEN_RE.search(ua_lower):
            os_family = "Android"
        if os_family is None:
            return None
        return (os_family, self.detect_browser(ua_lower))

    def match_desktop(self, ua_lower: str) -> tuple[str, str] | None:
        """Return (os_family, app_family) if UA is a desktop, else None.

        "Macintosh" is the canonical macOS token in UA strings (Apple has
        never updated this despite the OS rename). X11 + Linux is the common
        Linux desktop signal. We intentionally do *not* treat bare "Linux"
        as desktop because Android UAs also contain "Linux".
        """

        os_family: str | None = None
        if "windows nt" in ua_lower or "win64" in ua_lower or "win32" in ua_lower:
            os_family = "Windows"
        elif "macintosh" in ua_lower or "mac os x" in ua_lower:
            os_family = "macOS"
        elif "x11" in ua_lower and "linux" in ua_lower:
            os_family = "Linux"
        elif "cros" in ua_lower:
            os_family = "ChromeOS"
        if os_family is None:
            return None
        return (os_family, self.detect_browser(ua_lower))

    def detect_browser(self, ua_lower: str) -> str:
        """Best-effort browser name from a lowercased UA.

        Returns "" for UAs we don't recognize. The order in
        ``_BROWSER_TOKENS`` matters because Chromium-family browsers
        (Edge/Opera) re-use ``Chrome/`` and ``Safari/`` substrings.
        """

        for token, app in _BROWSER_TOKENS:
            if token in ua_lower:
                return app
        return ""

    def classify(self, user_agent: str | None) -> DeviceFingerprint:
        """Classify a User-Agent string into a stable coarse device class.

        Empty, None, or whitespace/control-char-only input returns an
        UNKNOWN fingerprint with ``raw_user_agent`` set to the empty string
        (never ``None``) so downstream code can rely on the field being a
        string. Any unexpected input type is coerced to ``str`` defensively
        -- we would rather classify garbage as UNKNOWN than raise from a
        logging hot path.

        The returned fingerprint is frozen and safe to cache.
        """

        if user_agent is None:
            return DeviceFingerprint(DeviceClass.UNKNOWN, "", "", "")
        if not isinstance(user_agent, str):
            try:
                user_agent = str(user_agent)
            except Exception:
                return DeviceFingerprint(DeviceClass.UNKNOWN, "", "", "")

        raw = user_agent
        stripped = user_agent.strip()
        if not stripped:
            # Preserve the original raw string (which might be "   ") so the
            # audit trail reflects what the client actually sent.
            return DeviceFingerprint(DeviceClass.UNKNOWN, "", "", raw)

        # If the UA is entirely control characters or non-printable junk,
        # treat as UNKNOWN. We still keep the raw string for audit.
        if not any(ch.isprintable() and not ch.isspace() for ch in stripped):
            return DeviceFingerprint(DeviceClass.UNKNOWN, "", "", raw)

        ua_lower = self.lower(stripped)

        cli = self.match_cli(ua_lower)
        if cli is not None:
            os_family, app = cli
            return DeviceFingerprint(DeviceClass.CLI, os_family, app, raw)

        # Jellyfin native apps are checked before generic TV/phone rules
        # because they self-identify their form factor (``Jellyfin for
        # Android TV`` embeds the TV-ish token ``android tv`` but we want
        # to attribute ``app_family="Jellyfin"``, not leave it blank from
        # the generic TV rule). This intentionally overrides the literal
        # priority order in the spec: the Jellyfin rule is strictly more
        # specific than any generic device heuristic.
        jf = self.match_jellyfin(ua_lower)
        if jf is not None:
            cls, os_family, app = jf
            return DeviceFingerprint(cls, os_family, app, raw)

        tv = self.match_tv(ua_lower)
        if tv is not None:
            os_family, app = tv
            return DeviceFingerprint(DeviceClass.TV, os_family, app, raw)

        phone = self.match_phone(ua_lower)
        if phone is not None:
            os_family, app = phone
            return DeviceFingerprint(DeviceClass.PHONE, os_family, app, raw)

        tablet = self.match_tablet(ua_lower)
        if tablet is not None:
            os_family, app = tablet
            return DeviceFingerprint(DeviceClass.TABLET, os_family, app, raw)

        desktop = self.match_desktop(ua_lower)
        if desktop is not None:
            os_family, app = desktop
            return DeviceFingerprint(DeviceClass.DESKTOP, os_family, app, raw)

        return DeviceFingerprint(DeviceClass.UNKNOWN, "", "", raw)

    def classify_class(self, user_agent: str | None) -> DeviceClass:
        """Return just the device class for callers that don't need metadata.

        Convenience wrapper -- exists because most call sites in the admin
        UI only want the icon and discarding the rest of the fingerprint at
        the call site is noisy. Equivalent to ``classify(ua).device_class``.
        """

        return self.classify(user_agent).device_class


# Singleton instance: the classifier is stateless, so a single shared
# instance is sufficient for the whole process. Module-level aliases
# below preserve the historical ``classify`` / ``classify_class`` import
# surface used by ``infrastructure/auth/session_store.py`` and friends.
_CLASSIFIER = DeviceClassifier()

# Public module aliases bound to bound-methods of the singleton. These
# replace the previous loose top-level helpers; the underscored names
# remain as private aliases so the rule-table comments above continue
# to read naturally and any in-tree internal callers keep working.
_lower = _CLASSIFIER.lower
_match_cli = _CLASSIFIER.match_cli
_match_tv = _CLASSIFIER.match_tv
_match_jellyfin = _CLASSIFIER.match_jellyfin
_match_phone = _CLASSIFIER.match_phone
_match_tablet = _CLASSIFIER.match_tablet
_match_desktop = _CLASSIFIER.match_desktop
_detect_browser = _CLASSIFIER.detect_browser
classify = _CLASSIFIER.classify
classify_class = _CLASSIFIER.classify_class
