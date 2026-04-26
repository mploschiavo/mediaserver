"""Unit + property tests for the User-Agent device classifier.

Canonical strings here are sampled from real session logs (with IPs and
usernames scrubbed) so the fixtures double as a regression corpus: any
future refactor must keep classifying these the same way.
"""

from __future__ import annotations

import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

from media_stack.core.auth.users.device_classifier import (
    DeviceClass,
    DeviceFingerprint,
    classify,
    classify_class,
)


# --- Canonical samples -------------------------------------------------

TV_SAMPLES = [
    (
        "Mozilla/5.0 (Linux; Android 9; SHIELD Android TV "
        "Build/PPR1.180610.011) AppleWebKit/537.36",
        "Android TV",
    ),
    ("AppleTV11,1/16.6 (17M85)", "tvOS"),
    ("Roku/DVP-12.5 (12.5.0.4176-C4)", "Roku OS"),
    (
        "Mozilla/5.0 (Web0S; Linux/SmartTV) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/87.0",
        "webOS",
    ),
    (
        "Mozilla/5.0 (Linux; Tizen 6.0) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Version/4.0 TV Safari/537.36",
        "Tizen",
    ),
]

PHONE_SAMPLES = [
    (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
        "Mobile/15E148 Safari/604.1",
        "iOS",
        "Safari",
    ),
    (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Mobile Safari/537.36",
        "Android",
        "Chrome",
    ),
    (
        "Mozilla/5.0 (Windows Phone 10.0; Android 6.0.1; "
        "Microsoft; Lumia 950) AppleWebKit/537.36",
        "Windows Phone",
        "",
    ),
]

TABLET_SAMPLES = [
    (
        "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
        "Safari/604.1",
        "iPadOS",
        "Safari",
    ),
    (
        "Mozilla/5.0 (Linux; Android 13; SM-T970) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36",
        "Android",
        "Chrome",
    ),
    (
        "Mozilla/5.0 (Linux; U; Android 8.1.0; en-US; KFONWI "
        "Build/OPM1.171019.026) AppleWebKit/537.36 "
        "Chrome/70.0 Silk/88.3.9 like Chrome Safari/537.36",
        "Fire OS",
        "Chrome",
    ),
]

DESKTOP_SAMPLES = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36",
        "Windows",
        "Chrome",
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 Firefox/120.0",
        "macOS",
        "Firefox",
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Linux",
        "Chrome",
    ),
]

CLI_SAMPLES = [
    ("curl/7.88.1", "curl"),
    ("python-requests/2.31.0", "python-requests"),
    ("PostmanRuntime/7.26.8", "PostmanRuntime"),
    ("okhttp/4.12.0", "okhttp"),
]


# --- Hand-written unit tests ------------------------------------------


class TVClassificationTests(unittest.TestCase):
    def test_tv_samples_classify_as_tv(self) -> None:
        for ua, expected_os in TV_SAMPLES:
            with self.subTest(ua=ua):
                fp = classify(ua)
                self.assertEqual(fp.device_class, DeviceClass.TV)
                self.assertEqual(fp.os_family, expected_os)
                self.assertEqual(fp.raw_user_agent, ua)

    def test_additional_tv_tokens(self) -> None:
        # Cover the remaining TV token branches (smart-tv, smarttv,
        # googletv, bravia, hbbtv) so the table doesn't silently rot.
        cases = [
            ("SomeDevice Smart-TV/2.0", ""),
            ("Vendor SmartTV Browser", ""),
            ("Mozilla/5.0 (GoogleTV 4; Build/AAA)", "Android TV"),
            ("Mozilla/5.0 (Bravia 4K 2019)", "Android TV"),
            ("HbbTV/1.5.1 (+DRM; Vendor; Model)", "HbbTV"),
            # Android TV without SHIELD/Bravia markers -- exercises the
            # standalone "android tv" token path.
            ("Mozilla/5.0 (Android TV 11; MyTvBox)", "Android TV"),
        ]
        for ua, expected_os in cases:
            with self.subTest(ua=ua):
                fp = classify(ua)
                self.assertEqual(fp.device_class, DeviceClass.TV)
                self.assertEqual(fp.os_family, expected_os)

    def test_web0s_variant(self) -> None:
        # LG's real firmware uses "Web0S" (with a zero). Regression
        # test: we must not drift back to only matching "webos".
        fp = classify("Mozilla/5.0 (Web0S; Linux/SmartTV)")
        self.assertEqual(fp.device_class, DeviceClass.TV)
        self.assertEqual(fp.os_family, "webOS")


class PhoneClassificationTests(unittest.TestCase):
    def test_phone_samples_classify_as_phone(self) -> None:
        for ua, expected_os, expected_app in PHONE_SAMPLES:
            with self.subTest(ua=ua):
                fp = classify(ua)
                self.assertEqual(fp.device_class, DeviceClass.PHONE)
                self.assertEqual(fp.os_family, expected_os)
                self.assertEqual(fp.app_family, expected_app)
                self.assertEqual(fp.raw_user_agent, ua)


class TabletClassificationTests(unittest.TestCase):
    def test_tablet_samples_classify_as_tablet(self) -> None:
        for ua, expected_os, expected_app in TABLET_SAMPLES:
            with self.subTest(ua=ua):
                fp = classify(ua)
                self.assertEqual(fp.device_class, DeviceClass.TABLET)
                self.assertEqual(fp.os_family, expected_os)
                self.assertEqual(fp.app_family, expected_app)
                self.assertEqual(fp.raw_user_agent, ua)

    def test_additional_tablet_tokens(self) -> None:
        cases = [
            ("Kindle/3.0+ (Linux; U; en-US)", "Fire OS"),
            ("Mozilla/5.0 (Linux; U; Silk/3.8) AppleWebKit/537.36", "Fire OS"),
            (
                "Mozilla/5.0 (Linux; U; Android 4.0.4; GT-P3113 Build/IMM76D)",
                "Android",
            ),
            (
                "Mozilla/5.0 (Linux; Android 4.3; Nexus 7 Build/JSS15Q)",
                "Android",
            ),
            (
                "Mozilla/5.0 (Linux; Android 5.1.1; Nexus 10 Build/LMY49H)",
                "Android",
            ),
        ]
        for ua, expected_os in cases:
            with self.subTest(ua=ua):
                fp = classify(ua)
                self.assertEqual(fp.device_class, DeviceClass.TABLET)
                self.assertEqual(fp.os_family, expected_os)


class DesktopClassificationTests(unittest.TestCase):
    def test_desktop_samples_classify_as_desktop(self) -> None:
        for ua, expected_os, expected_app in DESKTOP_SAMPLES:
            with self.subTest(ua=ua):
                fp = classify(ua)
                self.assertEqual(fp.device_class, DeviceClass.DESKTOP)
                self.assertEqual(fp.os_family, expected_os)
                self.assertEqual(fp.app_family, expected_app)
                self.assertEqual(fp.raw_user_agent, ua)

    def test_edge_and_opera_detected_over_chrome(self) -> None:
        edge = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0"
        )
        opera = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 OPR/106.0"
        )
        self.assertEqual(classify(edge).app_family, "Edge")
        self.assertEqual(classify(opera).app_family, "Opera")

    def test_chromeos_detected(self) -> None:
        cros = (
            "Mozilla/5.0 (X11; CrOS x86_64 15786.80.0) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        fp = classify(cros)
        self.assertEqual(fp.device_class, DeviceClass.DESKTOP)
        self.assertEqual(fp.os_family, "ChromeOS")

    def test_windows_win32_branch(self) -> None:
        # Rare but real: 32-bit Windows UAs use Win32 rather than
        # ``Windows NT``. Exercises the or-branch in desktop detection.
        fp = classify(
            "Mozilla/5.0 (Win32) AppleWebKit/537.36 Chrome/100.0"
        )
        self.assertEqual(fp.device_class, DeviceClass.DESKTOP)
        self.assertEqual(fp.os_family, "Windows")

    def test_mac_os_x_without_macintosh_token(self) -> None:
        fp = classify("Mozilla/5.0 (Mac OS X 10_15_7) Firefox/120.0")
        self.assertEqual(fp.device_class, DeviceClass.DESKTOP)
        self.assertEqual(fp.os_family, "macOS")

    def test_opera_legacy_token(self) -> None:
        ua = "Opera/9.80 (Windows NT 10.0; U; en) Presto/2.12"
        fp = classify(ua)
        self.assertEqual(fp.app_family, "Opera")

    def test_edge_legacy_token(self) -> None:
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "Edge/18.17763"
        )
        fp = classify(ua)
        self.assertEqual(fp.app_family, "Edge")


class CLIClassificationTests(unittest.TestCase):
    def test_cli_samples_classify_as_cli(self) -> None:
        for ua, expected_app in CLI_SAMPLES:
            with self.subTest(ua=ua):
                fp = classify(ua)
                self.assertEqual(fp.device_class, DeviceClass.CLI)
                self.assertEqual(fp.os_family, "")
                self.assertEqual(fp.app_family, expected_app)
                self.assertEqual(fp.raw_user_agent, ua)

    def test_wget_and_httpie_detected(self) -> None:
        self.assertEqual(classify("Wget/1.21.2").device_class, DeviceClass.CLI)
        self.assertEqual(classify("HTTPie/3.2.1").app_family, "httpie")

    def test_cli_case_insensitive(self) -> None:
        self.assertEqual(classify("curl/7.88.1").device_class, DeviceClass.CLI)
        self.assertEqual(classify("CURL/7.88.1").device_class, DeviceClass.CLI)
        self.assertEqual(classify("Curl/7.88.1").device_class, DeviceClass.CLI)


class JellyfinAppTests(unittest.TestCase):
    def test_jellyfin_mobile_is_phone(self) -> None:
        fp = classify("Jellyfin Mobile 2.5.1 (iOS 17.0)")
        self.assertEqual(fp.device_class, DeviceClass.PHONE)
        self.assertEqual(fp.app_family, "Jellyfin")
        self.assertEqual(fp.os_family, "iOS")

    def test_jellyfin_android_tv_is_tv(self) -> None:
        fp = classify("Jellyfin for Android TV 0.16.0")
        self.assertEqual(fp.device_class, DeviceClass.TV)
        self.assertEqual(fp.app_family, "Jellyfin")
        self.assertEqual(fp.os_family, "Android TV")

    def test_jellyfin_media_player_is_desktop(self) -> None:
        fp = classify("Jellyfin Media Player/1.9.1 (Windows)")
        self.assertEqual(fp.device_class, DeviceClass.DESKTOP)
        self.assertEqual(fp.app_family, "Jellyfin")
        self.assertEqual(fp.os_family, "Windows")

    def test_jellyfin_media_player_mac_and_linux(self) -> None:
        mac = classify("Jellyfin Media Player/1.9.1 (macOS)")
        self.assertEqual(mac.os_family, "macOS")
        linux = classify("Jellyfin Media Player/1.9.1 (Linux)")
        self.assertEqual(linux.os_family, "Linux")

    def test_jellyfin_mobile_on_android(self) -> None:
        fp = classify("Jellyfin Mobile 2.5.1 (Android 14; Pixel 8)")
        self.assertEqual(fp.device_class, DeviceClass.PHONE)
        self.assertEqual(fp.os_family, "Android")

    def test_jellyfin_priority_over_generic_iphone_rule(self) -> None:
        # UA contains both "iPhone" and "Jellyfin Mobile" -- the more
        # specific Jellyfin rule must win and set app_family.
        ua = (
            "Jellyfin Mobile 2.5.1 (iOS 17.0; iPhone) "
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
        )
        fp = classify(ua)
        self.assertEqual(fp.device_class, DeviceClass.PHONE)
        self.assertEqual(fp.app_family, "Jellyfin")

    def test_jellyfin_mobile_unknown_os(self) -> None:
        # Exercise the fallback branch when os isn't identifiable.
        fp = classify("Jellyfin Mobile 2.5.1")
        self.assertEqual(fp.device_class, DeviceClass.PHONE)
        self.assertEqual(fp.os_family, "")
        self.assertEqual(fp.app_family, "Jellyfin")

    def test_jellyfin_media_player_unknown_os(self) -> None:
        fp = classify("Jellyfin Media Player/1.9.1")
        self.assertEqual(fp.device_class, DeviceClass.DESKTOP)
        self.assertEqual(fp.os_family, "")
        self.assertEqual(fp.app_family, "Jellyfin")


class UnknownAndEdgeCaseTests(unittest.TestCase):
    def test_empty_string_is_unknown_with_empty_raw(self) -> None:
        fp = classify("")
        self.assertEqual(fp.device_class, DeviceClass.UNKNOWN)
        self.assertEqual(fp.raw_user_agent, "")
        self.assertIsNotNone(fp.raw_user_agent)

    def test_none_is_unknown(self) -> None:
        fp = classify(None)
        self.assertEqual(fp.device_class, DeviceClass.UNKNOWN)
        self.assertEqual(fp.raw_user_agent, "")

    def test_garbage_is_unknown_but_preserved(self) -> None:
        fp = classify("random garbage")
        self.assertEqual(fp.device_class, DeviceClass.UNKNOWN)
        self.assertEqual(fp.raw_user_agent, "random garbage")

    def test_one_char_is_unknown(self) -> None:
        self.assertEqual(classify("a").device_class, DeviceClass.UNKNOWN)

    def test_whitespace_only_is_unknown(self) -> None:
        fp = classify("   \t  ")
        self.assertEqual(fp.device_class, DeviceClass.UNKNOWN)
        # Raw preserved for audit.
        self.assertEqual(fp.raw_user_agent, "   \t  ")

    def test_control_chars_only_is_unknown(self) -> None:
        fp = classify("\x00\x01\x02")
        self.assertEqual(fp.device_class, DeviceClass.UNKNOWN)

    def test_non_string_input_coerced_safely(self) -> None:
        # Defensive: we don't want a logging hot path to raise if it
        # somehow gets a non-string (e.g. an int from a buggy caller).
        fp = classify(12345)  # type: ignore[arg-type]
        self.assertIsInstance(fp, DeviceFingerprint)

    def test_classify_class_matches_full(self) -> None:
        for ua, _ in TV_SAMPLES:
            self.assertEqual(classify_class(ua), classify(ua).device_class)
        self.assertEqual(classify_class(""), DeviceClass.UNKNOWN)
        self.assertEqual(classify_class(None), DeviceClass.UNKNOWN)

    def test_device_class_json_serializes_as_string(self) -> None:
        import json

        blob = json.dumps({"class": DeviceClass.TV})
        self.assertIn('"TV"', blob)

    def test_fingerprint_is_frozen(self) -> None:
        fp = classify("curl/7.88.1")
        with self.assertRaises(Exception):
            fp.device_class = DeviceClass.UNKNOWN  # type: ignore[misc]


# --- Hypothesis property tests ----------------------------------------

# A strategy producing "any text a UA field could plausibly contain":
# printable + whitespace + a few control chars, bounded length to keep
# the test suite fast.
_TEXT_STRATEGY = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),  # no surrogate halves
    ),
    min_size=0,
    max_size=200,
)


class ClassifierPropertyTests(unittest.TestCase):
    @given(_TEXT_STRATEGY)
    @settings(max_examples=200, deadline=None)
    def test_never_raises_for_any_text(self, ua: str) -> None:
        fp = classify(ua)
        self.assertIsInstance(fp, DeviceFingerprint)
        self.assertIsInstance(fp.device_class, DeviceClass)

    @given(_TEXT_STRATEGY)
    @settings(max_examples=200, deadline=None)
    def test_raw_user_agent_preserved(self, ua: str) -> None:
        self.assertEqual(classify(ua).raw_user_agent, ua)

    @given(_TEXT_STRATEGY)
    @settings(max_examples=200, deadline=None)
    def test_classify_class_matches_full(self, ua: str) -> None:
        self.assertEqual(classify_class(ua), classify(ua).device_class)

    @given(_TEXT_STRATEGY)
    @settings(max_examples=200, deadline=None)
    def test_idempotent(self, ua: str) -> None:
        self.assertEqual(classify(ua), classify(ua))

    @given(
        st.text(
            alphabet=st.characters(whitelist_categories=("Zs", "Cc")),
            min_size=0,
            max_size=40,
        )
    )
    @settings(max_examples=100, deadline=None)
    def test_whitespace_or_control_only_is_unknown(self, ua: str) -> None:
        fp = classify(ua)
        self.assertEqual(fp.device_class, DeviceClass.UNKNOWN)

    @given(st.binary(min_size=0, max_size=200))
    @settings(max_examples=100, deadline=None)
    def test_bytes_decoded_strings_never_raise(self, blob: bytes) -> None:
        # Simulate a caller that decoded bytes with errors="replace".
        ua = blob.decode("utf-8", errors="replace")
        fp = classify(ua)
        self.assertIsInstance(fp, DeviceFingerprint)

    @given(_TEXT_STRATEGY)
    @settings(max_examples=100, deadline=None)
    def test_result_is_hashable(self, ua: str) -> None:
        # Frozen dataclass should be usable as a dict key / set member.
        fp = classify(ua)
        self.assertEqual({fp, fp}, {fp})


if __name__ == "__main__":
    unittest.main()
