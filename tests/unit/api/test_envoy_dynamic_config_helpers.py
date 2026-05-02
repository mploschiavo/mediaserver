"""Unit tests for module-level helper functions in envoy dynamic_config.py."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

# Helpers were extracted to a sibling module
# (adapters.compose.edge.providers.envoy.helpers) during the
# Phase 16-C refactor. The old core/platforms shim does a star-import
# from dynamic_config which drops underscore-prefixed names; import
# from the canonical helpers module instead.
from media_stack.adapters.compose.edge.providers.envoy.helpers import (  # noqa: E402
    _cluster_name,
    _extract_backtick_tokens,
    _path_prefix_app_slug,
    _path_prefix_root,
    _rule_hosts,
    _rule_path_prefix,
    _session_cookie_name,
    _strip_prefix_value,
    _tokenize,
    _virtual_host_name,
)


class TestTokenize(unittest.TestCase):
    def test_simple_string(self):
        self.assertEqual(_tokenize("sonarr"), "sonarr")

    def test_mixed_case_and_spaces(self):
        self.assertEqual(_tokenize("  My Service  "), "my_service")

    def test_special_characters_replaced(self):
        self.assertEqual(_tokenize("app.media-dev.local"), "app_media_dev_local")

    def test_empty_string(self):
        self.assertEqual(_tokenize(""), "")

    def test_none_value(self):
        self.assertEqual(_tokenize(None), "")

    def test_numeric_input(self):
        self.assertEqual(_tokenize(12345), "12345")

    def test_leading_trailing_special_chars_stripped(self):
        self.assertEqual(_tokenize("---hello---"), "hello")

    def test_consecutive_special_chars_collapse(self):
        self.assertEqual(_tokenize("a!!!b@@@c"), "a_b_c")

    def test_only_special_chars(self):
        self.assertEqual(_tokenize("!!!"), "")


class TestExtractBacktickTokens(unittest.TestCase):
    def test_single_token(self):
        self.assertEqual(_extract_backtick_tokens("`hello`"), ("hello",))

    def test_multiple_tokens(self):
        self.assertEqual(
            _extract_backtick_tokens("`foo`, `bar`"),
            ("foo", "bar"),
        )

    def test_no_backticks(self):
        self.assertEqual(_extract_backtick_tokens("no backticks here"), ())

    def test_empty_string(self):
        self.assertEqual(_extract_backtick_tokens(""), ())

    def test_none_value(self):
        self.assertEqual(_extract_backtick_tokens(None), ())

    def test_empty_backtick_pair_skipped(self):
        # `` contains empty string which should be stripped and filtered
        # The regex `([^`]+)` requires at least one char, so `` won't match
        self.assertEqual(_extract_backtick_tokens("``"), ())

    def test_whitespace_inside_backticks_stripped(self):
        self.assertEqual(_extract_backtick_tokens("` hello `"), ("hello",))

    def test_mixed_backtick_and_plain_text(self):
        self.assertEqual(
            _extract_backtick_tokens("Host(`example.com`) && PathPrefix(`/app`)"),
            ("example.com", "/app"),
        )


class TestRuleHosts(unittest.TestCase):
    def test_single_host(self):
        self.assertEqual(
            _rule_hosts("Host(`apps.media-dev.local`)"),
            ("apps.media-dev.local",),
        )

    def test_multiple_hosts(self):
        self.assertEqual(
            _rule_hosts("Host(`host1.local`, `host2.local`)"),
            ("host1.local", "host2.local"),
        )

    def test_host_with_path_prefix(self):
        self.assertEqual(
            _rule_hosts("Host(`example.com`) && PathPrefix(`/app/sonarr`)"),
            ("example.com",),
        )

    def test_no_host_rule(self):
        self.assertEqual(_rule_hosts("PathPrefix(`/app/sonarr`)"), ())

    def test_empty_string(self):
        self.assertEqual(_rule_hosts(""), ())

    def test_none_value(self):
        self.assertEqual(_rule_hosts(None), ())

    def test_case_insensitive_host(self):
        self.assertEqual(
            _rule_hosts("host(`myhost.local`)"),
            ("myhost.local",),
        )

    def test_host_with_empty_parens(self):
        self.assertEqual(_rule_hosts("Host()"), ())


class TestRulePathPrefix(unittest.TestCase):
    def test_single_path_prefix(self):
        self.assertEqual(
            _rule_path_prefix("PathPrefix(`/app/sonarr`)"),
            "/app/sonarr",
        )

    def test_combined_with_host(self):
        self.assertEqual(
            _rule_path_prefix("Host(`example.com`) && PathPrefix(`/app/radarr`)"),
            "/app/radarr",
        )

    def test_no_path_prefix(self):
        self.assertEqual(_rule_path_prefix("Host(`example.com`)"), "")

    def test_empty_string(self):
        self.assertEqual(_rule_path_prefix(""), "")

    def test_none_value(self):
        self.assertEqual(_rule_path_prefix(None), "")

    def test_case_insensitive(self):
        self.assertEqual(
            _rule_path_prefix("pathprefix(`/test`)"),
            "/test",
        )

    def test_path_without_leading_slash_gets_one(self):
        self.assertEqual(
            _rule_path_prefix("PathPrefix(`app/sonarr`)"),
            "/app/sonarr",
        )

    def test_empty_backticks(self):
        self.assertEqual(_rule_path_prefix("PathPrefix(``)"), "")

    def test_empty_parens(self):
        self.assertEqual(_rule_path_prefix("PathPrefix()"), "")


class TestStripPrefixValue(unittest.TestCase):
    def test_prefixes_list(self):
        cfg = {"stripPrefix": {"prefixes": ["/app/homepage"]}}
        self.assertEqual(_strip_prefix_value(cfg), "/app/homepage")

    def test_prefix_string(self):
        cfg = {"stripPrefix": {"prefix": "/app/sonarr"}}
        self.assertEqual(_strip_prefix_value(cfg), "/app/sonarr")

    def test_prefixes_takes_first(self):
        cfg = {"stripPrefix": {"prefixes": ["/first", "/second"]}}
        self.assertEqual(_strip_prefix_value(cfg), "/first")

    def test_no_strip_prefix_key(self):
        cfg = {"redirectRegex": {"regex": ".*"}}
        self.assertEqual(_strip_prefix_value(cfg), "")

    def test_empty_dict(self):
        self.assertEqual(_strip_prefix_value({}), "")

    def test_strip_prefix_not_dict(self):
        cfg = {"stripPrefix": "not-a-dict"}
        self.assertEqual(_strip_prefix_value(cfg), "")

    def test_empty_prefixes_list(self):
        cfg = {"stripPrefix": {"prefixes": []}}
        self.assertEqual(_strip_prefix_value(cfg), "")

    def test_prefix_without_leading_slash(self):
        cfg = {"stripPrefix": {"prefix": "app/bazarr"}}
        self.assertEqual(_strip_prefix_value(cfg), "/app/bazarr")

    def test_none_prefix_value(self):
        cfg = {"stripPrefix": {"prefix": None}}
        self.assertEqual(_strip_prefix_value(cfg), "")

    def test_prefixes_with_none_first_element(self):
        cfg = {"stripPrefix": {"prefixes": [None]}}
        self.assertEqual(_strip_prefix_value(cfg), "")


class TestClusterName(unittest.TestCase):
    def test_normal_service(self):
        self.assertEqual(_cluster_name("homepage"), "service_homepage")

    def test_hyphenated_service(self):
        self.assertEqual(_cluster_name("my-service"), "service_my_service")

    def test_empty_string_fallback(self):
        self.assertEqual(_cluster_name(""), "service_app")

    def test_none_fallback(self):
        self.assertEqual(_cluster_name(None), "service_app")

    def test_special_chars(self):
        self.assertEqual(_cluster_name("app.media-dev"), "service_app_media_dev")


class TestVirtualHostName(unittest.TestCase):
    def test_normal_host(self):
        self.assertEqual(
            _virtual_host_name("apps.media-dev.local"),
            "vhost_apps_media_dev_local",
        )

    def test_empty_string_fallback(self):
        self.assertEqual(_virtual_host_name(""), "vhost_default")

    def test_none_fallback(self):
        self.assertEqual(_virtual_host_name(None), "vhost_default")

    def test_simple_host(self):
        self.assertEqual(_virtual_host_name("localhost"), "vhost_localhost")


class TestPathPrefixAppSlug(unittest.TestCase):
    def test_two_segment_path(self):
        self.assertEqual(_path_prefix_app_slug("/app/bazarr"), "bazarr")

    def test_single_segment_path(self):
        self.assertEqual(_path_prefix_app_slug("/app"), "app")

    def test_trailing_slash(self):
        self.assertEqual(_path_prefix_app_slug("/app/sonarr/"), "sonarr")

    def test_empty_string(self):
        self.assertEqual(_path_prefix_app_slug(""), "")

    def test_none_value(self):
        self.assertEqual(_path_prefix_app_slug(None), "")

    def test_root_path(self):
        # "/" → stripped trailing "/" → empty → returns ""
        self.assertEqual(_path_prefix_app_slug("/"), "")

    def test_deep_path(self):
        self.assertEqual(_path_prefix_app_slug("/a/b/jellyfin"), "jellyfin")

    def test_uppercase_normalized(self):
        self.assertEqual(_path_prefix_app_slug("/app/Sonarr"), "sonarr")

    def test_special_chars_in_slug(self):
        self.assertEqual(_path_prefix_app_slug("/app/my-service"), "my_service")


class TestSessionCookieName(unittest.TestCase):
    def test_normal_prefix(self):
        self.assertEqual(
            _session_cookie_name("/app/homepage"),
            "media_stack_app_homepage",
        )

    def test_empty_prefix_fallback(self):
        self.assertEqual(_session_cookie_name(""), "media_stack_app")

    def test_none_fallback(self):
        self.assertEqual(_session_cookie_name(None), "media_stack_app")

    def test_root_prefix_fallback(self):
        self.assertEqual(_session_cookie_name("/"), "media_stack_app")

    def test_deep_path_uses_last_segment(self):
        self.assertEqual(
            _session_cookie_name("/app/jellyfin"),
            "media_stack_app_jellyfin",
        )

    def test_hyphenated_app_slug(self):
        self.assertEqual(
            _session_cookie_name("/app/my-service"),
            "media_stack_app_my_service",
        )


class TestPathPrefixRoot(unittest.TestCase):
    def test_two_segment_path(self):
        self.assertEqual(_path_prefix_root("/app/bazarr"), "/app")

    def test_single_segment_path(self):
        # "/app" → parent is "" → returns "/"
        self.assertEqual(_path_prefix_root("/app"), "/")

    def test_empty_string(self):
        self.assertEqual(_path_prefix_root(""), "/")

    def test_none_value(self):
        self.assertEqual(_path_prefix_root(None), "/")

    def test_root_slash(self):
        self.assertEqual(_path_prefix_root("/"), "/")

    def test_trailing_slash(self):
        self.assertEqual(_path_prefix_root("/app/sonarr/"), "/app")

    def test_deep_path(self):
        self.assertEqual(_path_prefix_root("/a/b/c"), "/a/b")

    def test_no_leading_slash(self):
        # "app/sonarr" → prepend "/" → "/app/sonarr" → parent is "/app"
        self.assertEqual(_path_prefix_root("app/sonarr"), "/app")

    def test_parent_no_leading_slash_gets_one(self):
        # "x/y" → prepend "/" → "/x/y" → parent is "/x"
        self.assertEqual(_path_prefix_root("x/y"), "/x")


if __name__ == "__main__":
    unittest.main()
