import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.controller_profile import (  # noqa: E402
    ControllerChaosSettings,
    ControllerExposureSettings,
    ControllerProfileCatalog,
    ControllerProfileConfig,
    _as_bool,
    _as_bool_with_tokens,
    _coerce_url_list,
    _install_apps_for_profile,
    _join_host,
    _normalize_app_name,
    _normalize_app_token,
    _normalize_chaos_actions,
    _normalize_deployment_target,
    _normalize_host,
    _normalize_optional_port,
    _normalize_purpose,
    _normalize_route_strategy,
    _normalize_string_list,
    _normalize_string_list_allow_empty,
    _parse_private_network_cidr,
    _parse_storage_gb,
    _resolve_install_profile,
    _split_app_csv,
    _to_positive_int,
    load_bootstrap_profile_catalog,
    maybe_load_bootstrap_profile,
)


def _make_catalog(**overrides):
    """Build a minimal ControllerProfileCatalog for tests."""
    defaults = dict(
        deployment_aliases={"compose": "compose", "k8s": "k8s", "docker-compose": "compose"},
        purpose_values=("dev", "test", "prod"),
        route_strategy_aliases={
            "subdomain": "subdomain",
            "path-prefix": "path-prefix",
            "hybrid": "hybrid",
            "local": "subdomain",
        },
        auth_providers=("none", "authelia", "authentik"),
        auth_disabled_provider="none",
        auth_provider_middleware_defaults={
            "none": "",
            "authelia": "authelia@docker",
            "authentik": "authentik@docker",
        },
        app_keys=("jellyfin", "sonarr", "radarr", "prowlarr", "sabnzbd", "envoy", "traefik"),
        app_aliases={"jf": "jellyfin", "jelly": "jellyfin"},
        install_profiles={
            "minimal": ("jellyfin", "traefik"),
            "standard": ("jellyfin", "sonarr", "radarr", "prowlarr", "envoy"),
            "full": ("jellyfin", "sonarr", "radarr", "prowlarr", "sabnzbd", "envoy", "traefik"),
        },
        bool_true_tokens=("1", "true", "yes", "on"),
        bool_false_tokens=("0", "false", "no", "off"),
        chaos_default_enabled=False,
        chaos_default_duration_minutes=5,
        chaos_default_interval_seconds=60,
        chaos_allowed_actions=("restart_container", "pause_container", "network_disconnect"),
        chaos_default_actions=("restart_container", "pause_container", "network_disconnect"),
        live_tv_tuner_urls=("https://example.com/tv.m3u",),
        live_tv_guide_urls=("https://example.com/guide.xml",),
        live_tv_default_program_icon_url="https://example.com/icon.png",
    )
    defaults.update(overrides)
    return ControllerProfileCatalog(**defaults)


CATALOG = _make_catalog()


def _minimal_profile_dict(**overrides):
    """Build a valid minimal profile dict for from_dict tests."""
    base = {
        "metadata": {
            "name": "test-stack",
            "platform": "compose",
            "purpose": "dev",
        },
        "resources": {
            "disk_space_gb": 100,
            "network_cidr": "192.168.1.0/24",
        },
        "install_profile": "minimal",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# ControllerProfileCatalog
# ---------------------------------------------------------------------------
class TestControllerProfileCatalog(unittest.TestCase):
    def test_app_key_set_returns_set_of_app_keys(self):
        cat = _make_catalog(app_keys=("jellyfin", "sonarr", "radarr"))
        self.assertEqual(cat.app_key_set, {"jellyfin", "sonarr", "radarr"})

    def test_app_key_set_is_empty_for_empty_keys(self):
        cat = _make_catalog(app_keys=())
        self.assertEqual(cat.app_key_set, set())

    def test_catalog_is_frozen(self):
        with self.assertRaises(AttributeError):
            CATALOG.chaos_default_enabled = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ControllerExposureSettings
# ---------------------------------------------------------------------------
class TestControllerExposureSettings(unittest.TestCase):
    def test_ingress_domain_normal(self):
        exp = ControllerExposureSettings(stack_subdomain="media", base_domain="example.com")
        self.assertEqual(exp.ingress_domain, "media.example.com")

    def test_ingress_domain_empty_subdomain(self):
        exp = ControllerExposureSettings(stack_subdomain="", base_domain="example.com")
        self.assertEqual(exp.ingress_domain, "example.com")

    def test_ingress_domain_empty_domain(self):
        exp = ControllerExposureSettings(stack_subdomain="media", base_domain="")
        self.assertEqual(exp.ingress_domain, "media")

    def test_ingress_domain_both_empty(self):
        exp = ControllerExposureSettings(stack_subdomain="", base_domain="")
        self.assertEqual(exp.ingress_domain, "")

    def test_normalized_app_path_prefix_slash_app(self):
        exp = ControllerExposureSettings(app_path_prefix="/app")
        self.assertEqual(exp.normalized_app_path_prefix, "/app")

    def test_normalized_app_path_prefix_empty_string(self):
        exp = ControllerExposureSettings(app_path_prefix="")
        self.assertEqual(exp.normalized_app_path_prefix, "/app")

    def test_normalized_app_path_prefix_no_leading_slash(self):
        exp = ControllerExposureSettings(app_path_prefix="services")
        self.assertEqual(exp.normalized_app_path_prefix, "/services")

    def test_normalized_app_path_prefix_trailing_slash(self):
        exp = ControllerExposureSettings(app_path_prefix="/app/")
        self.assertEqual(exp.normalized_app_path_prefix, "/app")

    def test_normalized_app_path_prefix_none(self):
        exp = ControllerExposureSettings(app_path_prefix=None)
        self.assertEqual(exp.normalized_app_path_prefix, "/app")

    def test_normalized_app_path_prefix_whitespace(self):
        exp = ControllerExposureSettings(app_path_prefix="  ")
        self.assertEqual(exp.normalized_app_path_prefix, "/app")

    def test_normalized_app_path_prefix_deep_path(self):
        exp = ControllerExposureSettings(app_path_prefix="/a/b/c")
        self.assertEqual(exp.normalized_app_path_prefix, "/a/b/c")

    def test_defaults(self):
        exp = ControllerExposureSettings()
        self.assertFalse(exp.internet_exposed)
        self.assertEqual(exp.route_strategy, "subdomain")
        self.assertEqual(exp.base_domain, "local")
        self.assertEqual(exp.stack_subdomain, "media-stack")
        self.assertEqual(exp.app_path_prefix, "/app")

    def test_exposure_is_frozen(self):
        exp = ControllerExposureSettings()
        with self.assertRaises(AttributeError):
            exp.base_domain = "other.com"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ControllerChaosSettings
# ---------------------------------------------------------------------------
class TestControllerChaosSettings(unittest.TestCase):
    def test_defaults(self):
        cs = ControllerChaosSettings()
        self.assertFalse(cs.enabled)
        self.assertEqual(cs.duration_minutes, 5)
        self.assertEqual(cs.interval_seconds, 60)
        self.assertIn("restart_container", cs.actions)

    def test_custom_values(self):
        cs = ControllerChaosSettings(enabled=True, duration_minutes=10, interval_seconds=30,
                                     actions=("restart_container",))
        self.assertTrue(cs.enabled)
        self.assertEqual(cs.duration_minutes, 10)
        self.assertEqual(cs.actions, ("restart_container",))


# ---------------------------------------------------------------------------
# _join_host
# ---------------------------------------------------------------------------
class TestJoinHost(unittest.TestCase):
    def test_normal_join(self):
        self.assertEqual(_join_host("media", "example.com"), "media.example.com")

    def test_empty_first_part(self):
        self.assertEqual(_join_host("", "example.com"), "example.com")

    def test_empty_second_part(self):
        self.assertEqual(_join_host("media", ""), "media")

    def test_both_empty(self):
        self.assertEqual(_join_host("", ""), "")

    def test_strips_dots(self):
        self.assertEqual(_join_host(".media.", ".example.com."), "media.example.com")

    def test_three_parts(self):
        self.assertEqual(_join_host("apps", "media", "example.com"), "apps.media.example.com")

    def test_lowercases(self):
        self.assertEqual(_join_host("MEDIA", "Example.COM"), "media.example.com")


# ---------------------------------------------------------------------------
# _as_bool / _as_bool_with_tokens
# ---------------------------------------------------------------------------
class TestAsBool(unittest.TestCase):
    def test_true_bool(self):
        self.assertTrue(_as_bool(True, default=False, catalog=CATALOG))

    def test_false_bool(self):
        self.assertFalse(_as_bool(False, default=True, catalog=CATALOG))

    def test_none_uses_default_true(self):
        self.assertTrue(_as_bool(None, default=True, catalog=CATALOG))

    def test_none_uses_default_false(self):
        self.assertFalse(_as_bool(None, default=False, catalog=CATALOG))

    def test_true_token_string(self):
        for token in ("1", "true", "yes", "on", "True", "YES"):
            self.assertTrue(_as_bool(token, default=False, catalog=CATALOG), f"Failed for {token}")

    def test_false_token_string(self):
        for token in ("0", "false", "no", "off", "False", "NO"):
            self.assertFalse(_as_bool(token, default=True, catalog=CATALOG), f"Failed for {token}")

    def test_empty_string_uses_default(self):
        self.assertTrue(_as_bool("", default=True, catalog=CATALOG))
        self.assertFalse(_as_bool("", default=False, catalog=CATALOG))

    def test_invalid_token_raises(self):
        with self.assertRaises(ValueError):
            _as_bool("maybe", default=False, catalog=CATALOG)

    def test_int_nonzero_is_true(self):
        self.assertTrue(_as_bool(1, default=False, catalog=CATALOG))
        self.assertTrue(_as_bool(42, default=False, catalog=CATALOG))

    def test_int_zero_is_false(self):
        self.assertFalse(_as_bool(0, default=True, catalog=CATALOG))

    def test_float_nonzero_is_true(self):
        self.assertTrue(_as_bool(1.5, default=False, catalog=CATALOG))


class TestAsBoolWithTokens(unittest.TestCase):
    def test_custom_tokens(self):
        self.assertTrue(
            _as_bool_with_tokens("oui", default=False, true_tokens=("oui",), false_tokens=("non",))
        )
        self.assertFalse(
            _as_bool_with_tokens("non", default=True, true_tokens=("oui",), false_tokens=("non",))
        )


# ---------------------------------------------------------------------------
# _normalize_app_token / _normalize_app_name
# ---------------------------------------------------------------------------
class TestNormalizeAppToken(unittest.TestCase):
    def test_lowercases_and_strips(self):
        self.assertEqual(_normalize_app_token("  JellyFin  "), "jellyfin")

    def test_removes_non_alphanumeric(self):
        self.assertEqual(_normalize_app_token("my-app_v2!"), "myappv2")

    def test_empty_returns_empty(self):
        self.assertEqual(_normalize_app_token(""), "")
        self.assertEqual(_normalize_app_token(None), "")


class TestNormalizeAppName(unittest.TestCase):
    def test_resolves_alias(self):
        self.assertEqual(_normalize_app_name("jf", CATALOG), "jellyfin")
        self.assertEqual(_normalize_app_name("jelly", CATALOG), "jellyfin")

    def test_canonical_name_passthrough(self):
        self.assertEqual(_normalize_app_name("sonarr", CATALOG), "sonarr")

    def test_empty_returns_empty(self):
        self.assertEqual(_normalize_app_name("", CATALOG), "")


# ---------------------------------------------------------------------------
# _normalize_host
# ---------------------------------------------------------------------------
class TestNormalizeHost(unittest.TestCase):
    def test_strips_and_lowercases(self):
        self.assertEqual(_normalize_host("  Media.Example.COM.  "), "media.example.com")

    def test_empty(self):
        self.assertEqual(_normalize_host(""), "")
        self.assertEqual(_normalize_host(None), "")


# ---------------------------------------------------------------------------
# _normalize_string_list
# ---------------------------------------------------------------------------
class TestNormalizeStringList(unittest.TestCase):
    def test_normal_list(self):
        result = _normalize_string_list(["Dev", "Test", "Prod"], field_name="test")
        self.assertEqual(result, ("dev", "test", "prod"))

    def test_deduplicates(self):
        result = _normalize_string_list(["a", "b", "A", "B", "c"], field_name="test")
        self.assertEqual(result, ("a", "b", "c"))

    def test_empty_list_raises(self):
        with self.assertRaises(ValueError):
            _normalize_string_list([], field_name="test")

    def test_non_list_raises(self):
        with self.assertRaises(ValueError):
            _normalize_string_list("not-a-list", field_name="test")

    def test_none_raises(self):
        with self.assertRaises(ValueError):
            _normalize_string_list(None, field_name="test")


# ---------------------------------------------------------------------------
# _normalize_string_list_allow_empty
# ---------------------------------------------------------------------------
class TestNormalizeStringListAllowEmpty(unittest.TestCase):
    def test_none_uses_default(self):
        result = _normalize_string_list_allow_empty(None, field_name="f", default=("a", "b"))
        self.assertEqual(result, ("a", "b"))

    def test_normal_list(self):
        result = _normalize_string_list_allow_empty(["X", "Y"], field_name="f", default=("z",))
        self.assertEqual(result, ("x", "y"))

    def test_empty_list_uses_default(self):
        result = _normalize_string_list_allow_empty([], field_name="f", default=("fallback",))
        self.assertEqual(result, ("fallback",))

    def test_non_list_raises(self):
        with self.assertRaises(ValueError):
            _normalize_string_list_allow_empty("oops", field_name="f", default=())


# ---------------------------------------------------------------------------
# _to_positive_int
# ---------------------------------------------------------------------------
class TestToPositiveInt(unittest.TestCase):
    def test_none_uses_default(self):
        self.assertEqual(_to_positive_int(None, default=42, field_name="x", minimum=0, maximum=100), 42)

    def test_empty_string_uses_default(self):
        self.assertEqual(_to_positive_int("", default=7, field_name="x", minimum=0, maximum=100), 7)

    def test_valid_int(self):
        self.assertEqual(_to_positive_int(50, default=0, field_name="x", minimum=0, maximum=100), 50)

    def test_valid_string_int(self):
        self.assertEqual(_to_positive_int("25", default=0, field_name="x", minimum=0, maximum=100), 25)

    def test_below_minimum_raises(self):
        with self.assertRaises(ValueError):
            _to_positive_int(-1, default=0, field_name="x", minimum=0, maximum=100)

    def test_above_maximum_raises(self):
        with self.assertRaises(ValueError):
            _to_positive_int(101, default=0, field_name="x", minimum=0, maximum=100)

    def test_non_numeric_raises(self):
        with self.assertRaises(ValueError):
            _to_positive_int("abc", default=0, field_name="x", minimum=0, maximum=100)


# ---------------------------------------------------------------------------
# _normalize_optional_port
# ---------------------------------------------------------------------------
class TestNormalizeOptionalPort(unittest.TestCase):
    def test_none_returns_empty(self):
        self.assertEqual(_normalize_optional_port(None, field_name="p"), "")

    def test_empty_returns_empty(self):
        self.assertEqual(_normalize_optional_port("", field_name="p"), "")

    def test_valid_port(self):
        self.assertEqual(_normalize_optional_port(8080, field_name="p"), "8080")
        self.assertEqual(_normalize_optional_port("443", field_name="p"), "443")

    def test_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            _normalize_optional_port(70000, field_name="p")
        with self.assertRaises(ValueError):
            _normalize_optional_port(0, field_name="p")


# ---------------------------------------------------------------------------
# _parse_storage_gb
# ---------------------------------------------------------------------------
class TestParseStorageGb(unittest.TestCase):
    def test_plain_int(self):
        self.assertEqual(_parse_storage_gb(500), 500)

    def test_gb_suffix(self):
        self.assertEqual(_parse_storage_gb("500GB"), 500)
        self.assertEqual(_parse_storage_gb("500g"), 500)

    def test_tb_suffix(self):
        self.assertEqual(_parse_storage_gb("1TB"), 1000)
        self.assertEqual(_parse_storage_gb("2t"), 2000)

    def test_fractional_tb(self):
        self.assertEqual(_parse_storage_gb("1.5TB"), 1500)

    def test_no_unit_defaults_gb(self):
        self.assertEqual(_parse_storage_gb("200"), 200)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            _parse_storage_gb("")
        with self.assertRaises(ValueError):
            _parse_storage_gb(None)

    def test_invalid_string_raises(self):
        with self.assertRaises(ValueError):
            _parse_storage_gb("abc")


# ---------------------------------------------------------------------------
# _parse_private_network_cidr
# ---------------------------------------------------------------------------
class TestParsePrivateNetworkCidr(unittest.TestCase):
    def test_valid_private_cidr(self):
        self.assertEqual(_parse_private_network_cidr("192.168.1.0/24"), "192.168.1.0/24")
        self.assertEqual(_parse_private_network_cidr("10.0.0.0/8"), "10.0.0.0/8")
        self.assertEqual(_parse_private_network_cidr("172.16.0.0/12"), "172.16.0.0/12")

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            _parse_private_network_cidr("")

    def test_public_cidr_raises(self):
        with self.assertRaises(ValueError):
            _parse_private_network_cidr("8.8.8.0/24")

    def test_invalid_cidr_raises(self):
        with self.assertRaises(ValueError):
            _parse_private_network_cidr("not-a-cidr")


# ---------------------------------------------------------------------------
# _coerce_url_list
# ---------------------------------------------------------------------------
class TestCoerceUrlList(unittest.TestCase):
    def test_none_returns_empty_tuple(self):
        self.assertEqual(_coerce_url_list(None), ())

    def test_string_returns_tuple(self):
        self.assertEqual(_coerce_url_list("https://x.com/a.m3u"), ("https://x.com/a.m3u",))

    def test_empty_string_returns_empty(self):
        self.assertEqual(_coerce_url_list("  "), ())

    def test_list_of_strings(self):
        self.assertEqual(_coerce_url_list(["a", "b"]), ("a", "b"))

    def test_list_with_empty_entries(self):
        self.assertEqual(_coerce_url_list(["a", "", None, "b"]), ("a", "b"))

    def test_non_list_non_string_returns_empty(self):
        self.assertEqual(_coerce_url_list(42), ())


# ---------------------------------------------------------------------------
# _normalize_deployment_target
# ---------------------------------------------------------------------------
class TestNormalizeDeploymentTarget(unittest.TestCase):
    def test_known_alias(self):
        self.assertEqual(_normalize_deployment_target("compose", CATALOG), "compose")
        self.assertEqual(_normalize_deployment_target("k8s", CATALOG), "k8s")
        self.assertEqual(_normalize_deployment_target("docker-compose", CATALOG), "compose")

    def test_case_insensitive(self):
        self.assertEqual(_normalize_deployment_target("COMPOSE", CATALOG), "compose")

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            _normalize_deployment_target("podman", CATALOG)


# ---------------------------------------------------------------------------
# _normalize_purpose
# ---------------------------------------------------------------------------
class TestNormalizePurpose(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(_normalize_purpose("dev", CATALOG), "dev")
        self.assertEqual(_normalize_purpose("PROD", CATALOG), "prod")

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            _normalize_purpose("staging", CATALOG)


# ---------------------------------------------------------------------------
# _normalize_route_strategy
# ---------------------------------------------------------------------------
class TestNormalizeRouteStrategy(unittest.TestCase):
    def test_known_strategy(self):
        self.assertEqual(_normalize_route_strategy("subdomain", CATALOG), "subdomain")
        self.assertEqual(_normalize_route_strategy("path-prefix", CATALOG), "path-prefix")

    def test_alias(self):
        self.assertEqual(_normalize_route_strategy("local", CATALOG), "subdomain")

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            _normalize_route_strategy("round-robin", CATALOG)


# ---------------------------------------------------------------------------
# _resolve_install_profile
# ---------------------------------------------------------------------------
class TestResolveInstallProfile(unittest.TestCase):
    def test_known_profile(self):
        self.assertEqual(_resolve_install_profile("minimal", CATALOG), "minimal")
        self.assertEqual(_resolve_install_profile("standard", CATALOG), "standard")
        self.assertEqual(_resolve_install_profile("full", CATALOG), "full")

    def test_case_insensitive(self):
        self.assertEqual(_resolve_install_profile("MINIMAL", CATALOG), "minimal")

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            _resolve_install_profile("ultra", CATALOG)


# ---------------------------------------------------------------------------
# _install_apps_for_profile
# ---------------------------------------------------------------------------
class TestInstallAppsForProfile(unittest.TestCase):
    def test_minimal_profile(self):
        apps = _install_apps_for_profile("minimal", CATALOG)
        self.assertTrue(apps["jellyfin"])
        self.assertTrue(apps["traefik"])
        self.assertFalse(apps["sonarr"])
        self.assertFalse(apps["envoy"])

    def test_full_profile(self):
        apps = _install_apps_for_profile("full", CATALOG)
        for key in CATALOG.app_keys:
            self.assertTrue(apps[key], f"{key} should be enabled in full profile")

    def test_unknown_profile_returns_all_false(self):
        apps = _install_apps_for_profile("nonexistent", CATALOG)
        for key in CATALOG.app_keys:
            self.assertFalse(apps[key])


# ---------------------------------------------------------------------------
# _split_app_csv
# ---------------------------------------------------------------------------
class TestSplitAppCsv(unittest.TestCase):
    def test_normal_csv(self):
        result = _split_app_csv("jellyfin,sonarr,radarr", CATALOG)
        self.assertEqual(result, ("jellyfin", "sonarr", "radarr"))

    def test_with_aliases(self):
        result = _split_app_csv("jf,sonarr", CATALOG)
        self.assertEqual(result, ("jellyfin", "sonarr"))

    def test_deduplicates(self):
        result = _split_app_csv("jellyfin,jf,jellyfin", CATALOG)
        self.assertEqual(result, ("jellyfin",))

    def test_empty_string(self):
        result = _split_app_csv("", CATALOG)
        self.assertEqual(result, ())


# ---------------------------------------------------------------------------
# _normalize_chaos_actions
# ---------------------------------------------------------------------------
class TestNormalizeChaosActions(unittest.TestCase):
    def test_none_uses_default(self):
        result = _normalize_chaos_actions(
            None,
            allowed=("restart_container", "pause_container"),
            default=("restart_container",),
        )
        self.assertEqual(result, ("restart_container",))

    def test_valid_list(self):
        result = _normalize_chaos_actions(
            ["restart_container", "pause_container"],
            allowed=("restart_container", "pause_container", "network_disconnect"),
            default=("restart_container",),
        )
        self.assertEqual(result, ("restart_container", "pause_container"))

    def test_invalid_action_raises(self):
        with self.assertRaises(ValueError):
            _normalize_chaos_actions(
                ["delete_container"],
                allowed=("restart_container",),
                default=("restart_container",),
            )

    def test_empty_list_uses_default(self):
        result = _normalize_chaos_actions(
            [],
            allowed=("restart_container",),
            default=("restart_container",),
        )
        self.assertEqual(result, ("restart_container",))

    def test_deduplicates(self):
        result = _normalize_chaos_actions(
            ["restart_container", "restart_container"],
            allowed=("restart_container",),
            default=("restart_container",),
        )
        self.assertEqual(result, ("restart_container",))

    def test_empty_allowed_raises(self):
        with self.assertRaises(ValueError):
            _normalize_chaos_actions(None, allowed=(), default=())


# ---------------------------------------------------------------------------
# ControllerProfileConfig.from_dict (integration-level with catalog)
# ---------------------------------------------------------------------------
class TestControllerProfileConfigFromDict(unittest.TestCase):
    """Tests that use from_dict with mocked external dependencies."""

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_minimal_valid_profile(self, _mock_edge):
        profile = ControllerProfileConfig.from_dict(
            _minimal_profile_dict(),
            catalog=CATALOG,
        )
        self.assertEqual(profile.deployment_target, "compose")
        self.assertEqual(profile.purpose, "dev")
        self.assertEqual(profile.stack_name, "test-stack")
        self.assertEqual(profile.disk_allocation_gb, 100)
        self.assertEqual(profile.network_cidr, "192.168.1.0/24")
        self.assertEqual(profile.install_profile, "minimal")
        self.assertTrue(profile.install_apps["jellyfin"])
        self.assertTrue(profile.preconfigure_apps)
        self.assertTrue(profile.preconfigure_api_keys)

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_exposure_defaults(self, _mock_edge):
        profile = ControllerProfileConfig.from_dict(
            _minimal_profile_dict(),
            catalog=CATALOG,
        )
        self.assertFalse(profile.exposure.internet_exposed)
        self.assertEqual(profile.exposure.route_strategy, "subdomain")
        self.assertEqual(profile.exposure.base_domain, "local")
        # stack_subdomain derived from metadata.name
        self.assertEqual(profile.exposure.stack_subdomain, "test-stack")
        self.assertEqual(profile.exposure.auth_provider, "none")

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_chaos_defaults(self, _mock_edge):
        profile = ControllerProfileConfig.from_dict(
            _minimal_profile_dict(),
            catalog=CATALOG,
        )
        self.assertFalse(profile.chaos.enabled)
        self.assertEqual(profile.chaos.duration_minutes, 5)
        self.assertEqual(profile.chaos.interval_seconds, 60)

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_chaos_override(self, _mock_edge):
        payload = _minimal_profile_dict(chaos={
            "enabled": True,
            "duration_minutes": 10,
            "interval_seconds": 30,
            "actions": ["restart_container"],
        })
        profile = ControllerProfileConfig.from_dict(payload, catalog=CATALOG)
        self.assertTrue(profile.chaos.enabled)
        self.assertEqual(profile.chaos.duration_minutes, 10)
        self.assertEqual(profile.chaos.interval_seconds, 30)
        self.assertEqual(profile.chaos.actions, ("restart_container",))

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_app_override(self, _mock_edge):
        payload = _minimal_profile_dict(apps={"jellyfin": False})
        profile = ControllerProfileConfig.from_dict(payload, catalog=CATALOG)
        self.assertFalse(profile.install_apps["jellyfin"])

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_unknown_app_override_raises(self, _mock_edge):
        payload = _minimal_profile_dict(apps={"unknownapp": True})
        with self.assertRaisesRegex(ValueError, "Unsupported app key"):
            ControllerProfileConfig.from_dict(payload, catalog=CATALOG)

    def test_missing_metadata_raises(self):
        with self.assertRaisesRegex(ValueError, "metadata must be an object"):
            ControllerProfileConfig.from_dict({}, catalog=CATALOG)

    def test_missing_resources_raises(self):
        with self.assertRaisesRegex(ValueError, "resources must be an object"):
            ControllerProfileConfig.from_dict(
                {"metadata": {"name": "s", "platform": "compose", "purpose": "dev"}},
                catalog=CATALOG,
            )

    def test_missing_name_raises(self):
        with self.assertRaisesRegex(ValueError, "metadata.name is required"):
            ControllerProfileConfig.from_dict(
                {
                    "metadata": {"platform": "compose", "purpose": "dev"},
                    "resources": {"disk_space_gb": 100, "network_cidr": "10.0.0.0/8"},
                    "install_profile": "minimal",
                },
                catalog=CATALOG,
            )

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_small_disk_raises(self, _mock_edge):
        payload = _minimal_profile_dict()
        payload["resources"]["disk_space_gb"] = 10
        with self.assertRaisesRegex(ValueError, "at least 20GB"):
            ControllerProfileConfig.from_dict(payload, catalog=CATALOG)

    def test_non_dict_root_raises(self):
        with self.assertRaisesRegex(ValueError, "Bootstrap profile root must be an object"):
            ControllerProfileConfig.from_dict("not a dict", catalog=CATALOG)

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_enabled_apps_property(self, _mock_edge):
        profile = ControllerProfileConfig.from_dict(
            _minimal_profile_dict(),
            catalog=CATALOG,
        )
        enabled = profile.enabled_apps
        self.assertIn("jellyfin", enabled)
        self.assertIn("traefik", enabled)
        self.assertNotIn("sonarr", enabled)

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_selected_apps_csv_property(self, _mock_edge):
        profile = ControllerProfileConfig.from_dict(
            _minimal_profile_dict(),
            catalog=CATALOG,
        )
        csv = profile.selected_apps_csv
        self.assertIn("jellyfin", csv)
        self.assertIn(",", csv)

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_live_tv_defaults_from_catalog(self, _mock_edge):
        profile = ControllerProfileConfig.from_dict(
            _minimal_profile_dict(),
            catalog=CATALOG,
        )
        self.assertEqual(profile.live_tv_tuner_urls, ("https://example.com/tv.m3u",))
        self.assertEqual(profile.live_tv_guide_urls, ("https://example.com/guide.xml",))
        self.assertEqual(profile.live_tv_default_program_icon_url, "https://example.com/icon.png")

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_live_tv_override(self, _mock_edge):
        payload = _minimal_profile_dict(live_tv_defaults={
            "tuner_urls": ["https://custom.com/tv.m3u"],
            "guide_urls": ["https://custom.com/guide.xml"],
            "default_program_icon_url": "https://custom.com/icon.png",
        })
        profile = ControllerProfileConfig.from_dict(payload, catalog=CATALOG)
        self.assertEqual(profile.live_tv_tuner_urls, ("https://custom.com/tv.m3u",))
        self.assertEqual(profile.live_tv_guide_urls, ("https://custom.com/guide.xml",))
        self.assertEqual(profile.live_tv_default_program_icon_url, "https://custom.com/icon.png")

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_routing_with_explicit_subdomain(self, _mock_edge):
        payload = _minimal_profile_dict(routing={
            "base_domain": "my.lan",
            "stack_subdomain": "ms",
        })
        profile = ControllerProfileConfig.from_dict(payload, catalog=CATALOG)
        self.assertEqual(profile.exposure.base_domain, "my.lan")
        self.assertEqual(profile.exposure.stack_subdomain, "ms")
        self.assertEqual(profile.exposure.ingress_domain, "ms.my.lan")

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_routing_empty_subdomain_allowed(self, _mock_edge):
        payload = _minimal_profile_dict(routing={
            "base_domain": "example.com",
            "stack_subdomain": "",
        })
        profile = ControllerProfileConfig.from_dict(payload, catalog=CATALOG)
        self.assertEqual(profile.exposure.stack_subdomain, "")
        self.assertEqual(profile.exposure.ingress_domain, "example.com")

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_full_profile_auto_download_true(self, _mock_edge):
        payload = _minimal_profile_dict(install_profile="full")
        profile = ControllerProfileConfig.from_dict(payload, catalog=CATALOG)
        self.assertTrue(profile.auto_download_content)

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_non_full_profile_auto_download_false(self, _mock_edge):
        payload = _minimal_profile_dict(install_profile="minimal")
        profile = ControllerProfileConfig.from_dict(payload, catalog=CATALOG)
        self.assertFalse(profile.auto_download_content)


# ---------------------------------------------------------------------------
# ControllerProfileConfig.from_yaml_file
# ---------------------------------------------------------------------------
class TestControllerProfileConfigFromYaml(unittest.TestCase):
    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_load_valid_yaml(self, _mock_edge):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "profile.yaml"
            p.write_text(
                "\n".join([
                    "metadata:",
                    "  name: yaml-test",
                    "  platform: k8s",
                    "  purpose: test",
                    "resources:",
                    "  disk_space_gb: 200",
                    "  network_cidr: 10.20.0.0/24",
                    "install_profile: standard",
                ]),
                encoding="utf-8",
            )
            profile = ControllerProfileConfig.from_yaml_file(p, catalog=CATALOG)
        self.assertEqual(profile.deployment_target, "k8s")
        self.assertEqual(profile.purpose, "test")
        self.assertEqual(profile.stack_name, "yaml-test")
        self.assertEqual(profile.disk_allocation_gb, 200)
        self.assertIsNotNone(profile.source_path)

    def test_load_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            ControllerProfileConfig.from_yaml_file(
                Path("/tmp/nonexistent_profile_xyz.yaml"),
                catalog=CATALOG,
            )

    def test_load_invalid_yaml_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.yaml"
            p.write_text("- - - [invalid", encoding="utf-8")
            with self.assertRaises(Exception):
                ControllerProfileConfig.from_yaml_file(p, catalog=CATALOG)

    def test_load_non_dict_yaml_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "list.yaml"
            p.write_text("- item1\n- item2\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "object at root"):
                ControllerProfileConfig.from_yaml_file(p, catalog=CATALOG)

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_load_empty_yaml_treated_as_empty_dict(self, _mock_edge):
        """An empty YAML file yields payload={}, which should fail on metadata check."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "empty.yaml"
            p.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "metadata must be an object"):
                ControllerProfileConfig.from_yaml_file(p, catalog=CATALOG)


# ---------------------------------------------------------------------------
# maybe_load_bootstrap_profile
# ---------------------------------------------------------------------------
class TestMaybeLoadBootstrapProfile(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(maybe_load_bootstrap_profile(None))

    def test_missing_file_raises(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            maybe_load_bootstrap_profile(Path("/tmp/does_not_exist_xyz.yaml"))

    @mock.patch(
        "media_stack.core.controller_profile.parser.load_builtin_edge_router_provider_specs",
        return_value=(),
    )
    def test_valid_file_returns_config(self, _mock_edge):
        # Clear lru_cache to avoid cross-test contamination from registry tests
        from media_stack.core.controller_profile import _load_bootstrap_profile_catalog_cached
        _load_bootstrap_profile_catalog_cached.cache_clear()
        # Reload registry to restore canonical services (other tests may have mutated it)
        from media_stack.api.services import registry as reg_mod
        reg_mod.reload_registry()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "profile.yaml"
            p.write_text(
                "\n".join([
                    "metadata:",
                    "  name: maybe-test",
                    "  platform: compose",
                    "  purpose: dev",
                    "resources:",
                    "  disk_space_gb: 50",
                    "  network_cidr: 192.168.1.0/24",
                    "install_profile: minimal",
                ]),
                encoding="utf-8",
            )
            result = maybe_load_bootstrap_profile(p)
        self.assertIsNotNone(result)
        self.assertEqual(result.stack_name, "maybe-test")


# ---------------------------------------------------------------------------
# ControllerProfileConfig properties
# ---------------------------------------------------------------------------
class TestControllerProfileConfigProperties(unittest.TestCase):
    def test_enabled_apps_with_explicit_install_apps(self):
        config = ControllerProfileConfig(
            deployment_target="compose",
            purpose="dev",
            stack_name="test",
            disk_allocation_gb=100,
            network_cidr="192.168.1.0/24",
            install_profile="minimal",
            install_apps={"jellyfin": True, "sonarr": False, "radarr": True},
            app_catalog=("jellyfin", "sonarr", "radarr"),
        )
        self.assertEqual(config.enabled_apps, ("jellyfin", "radarr"))

    def test_enabled_apps_respects_app_catalog_order(self):
        config = ControllerProfileConfig(
            deployment_target="compose",
            purpose="dev",
            stack_name="test",
            disk_allocation_gb=100,
            network_cidr="192.168.1.0/24",
            install_profile="minimal",
            install_apps={"radarr": True, "jellyfin": True, "sonarr": False},
            app_catalog=("sonarr", "radarr", "jellyfin"),
        )
        # app_catalog order is used, enabled_apps should be radarr then jellyfin
        self.assertEqual(config.enabled_apps, ("radarr", "jellyfin"))

    def test_selected_apps_csv(self):
        config = ControllerProfileConfig(
            deployment_target="compose",
            purpose="dev",
            stack_name="test",
            disk_allocation_gb=100,
            network_cidr="192.168.1.0/24",
            install_profile="minimal",
            install_apps={"a": True, "b": False, "c": True},
            app_catalog=("a", "b", "c"),
        )
        self.assertEqual(config.selected_apps_csv, "a,c")

    def test_enabled_apps_empty_catalog_uses_install_apps_keys(self):
        config = ControllerProfileConfig(
            deployment_target="compose",
            purpose="dev",
            stack_name="test",
            disk_allocation_gb=100,
            network_cidr="192.168.1.0/24",
            install_profile="minimal",
            install_apps={"x": True, "y": False},
            app_catalog=(),
        )
        enabled = config.enabled_apps
        self.assertIn("x", enabled)
        self.assertNotIn("y", enabled)


if __name__ == "__main__":
    unittest.main()
