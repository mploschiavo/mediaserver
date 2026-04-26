import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.maintainerr.rule_translation_service import (  # noqa: E402
    MaintainerrRuleTranslationDependencies,
    MaintainerrRuleTranslationService,
)


def _make_service(*, log=None, request=None, resolve_path=None):
    deps = MaintainerrRuleTranslationDependencies(
        log=log or MagicMock(),
        request=request or MagicMock(return_value=(200, [], "")),
        resolve_path=resolve_path or MagicMock(return_value=None),
    )
    return MaintainerrRuleTranslationService(deps=deps)


class TestText(unittest.TestCase):
    def test_strips_whitespace(self):
        self.assertEqual(MaintainerrRuleTranslationService._text("  hello  "), "hello")

    def test_none_returns_empty(self):
        self.assertEqual(MaintainerrRuleTranslationService._text(None), "")

    def test_numeric_input(self):
        self.assertEqual(MaintainerrRuleTranslationService._text(42), "42")

    def test_empty_string(self):
        self.assertEqual(MaintainerrRuleTranslationService._text(""), "")

    def test_zero_is_falsy_but_returns_empty(self):
        # 0 is falsy so (value or "") yields ""
        self.assertEqual(MaintainerrRuleTranslationService._text(0), "")


class TestToken(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(MaintainerrRuleTranslationService._token("MOVIE"), "movie")

    def test_strips_and_lowercases(self):
        self.assertEqual(MaintainerrRuleTranslationService._token("  Show  "), "show")

    def test_none_returns_empty(self):
        self.assertEqual(MaintainerrRuleTranslationService._token(None), "")


class TestAsInt(unittest.TestCase):
    def test_valid_int_string(self):
        self.assertEqual(MaintainerrRuleTranslationService._as_int("42"), 42)

    def test_whitespace_padded(self):
        self.assertEqual(MaintainerrRuleTranslationService._as_int("  7  "), 7)

    def test_invalid_returns_default(self):
        self.assertEqual(MaintainerrRuleTranslationService._as_int("abc", 99), 99)

    def test_none_returns_default(self):
        self.assertEqual(MaintainerrRuleTranslationService._as_int(None, -1), -1)

    def test_float_string_returns_default(self):
        self.assertEqual(MaintainerrRuleTranslationService._as_int("3.14", 0), 0)


class TestAsFloat(unittest.TestCase):
    def test_valid_float_string(self):
        self.assertAlmostEqual(MaintainerrRuleTranslationService._as_float("3.14"), 3.14)

    def test_integer_string(self):
        self.assertAlmostEqual(MaintainerrRuleTranslationService._as_float("5"), 5.0)

    def test_invalid_returns_default(self):
        self.assertAlmostEqual(MaintainerrRuleTranslationService._as_float("nope", 1.5), 1.5)

    def test_none_returns_default(self):
        self.assertAlmostEqual(MaintainerrRuleTranslationService._as_float(None, 0.0), 0.0)


class TestIsoDaysAgo(unittest.TestCase):
    def test_zero_days_is_today(self):
        result = MaintainerrRuleTranslationService._iso_days_ago(0)
        now = datetime.now(timezone.utc)
        self.assertTrue(result.startswith(now.strftime("%Y-%m-%d")))
        self.assertTrue(result.endswith("Z"))

    def test_positive_days(self):
        result = MaintainerrRuleTranslationService._iso_days_ago(30)
        expected_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        self.assertTrue(result.startswith(expected_date))

    def test_negative_days_clamped_to_zero(self):
        result = MaintainerrRuleTranslationService._iso_days_ago(-5)
        now = datetime.now(timezone.utc)
        self.assertTrue(result.startswith(now.strftime("%Y-%m-%d")))

    def test_format_is_iso_with_z(self):
        result = MaintainerrRuleTranslationService._iso_days_ago(1)
        # Should match YYYY-MM-DDTHH:MM:SSZ
        self.assertRegex(result, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class TestCoerceList(unittest.TestCase):
    def test_none_returns_empty_list(self):
        self.assertEqual(MaintainerrRuleTranslationService._coerce_list(None), [])

    def test_list_returned_as_is(self):
        self.assertEqual(MaintainerrRuleTranslationService._coerce_list([1, 2]), [1, 2])

    def test_scalar_wrapped_in_list(self):
        self.assertEqual(MaintainerrRuleTranslationService._coerce_list("hello"), ["hello"])

    def test_integer_wrapped_in_list(self):
        self.assertEqual(MaintainerrRuleTranslationService._coerce_list(42), [42])

    def test_empty_list_returned_as_is(self):
        self.assertEqual(MaintainerrRuleTranslationService._coerce_list([]), [])


class TestResolveRelativeTimeToken(unittest.TestCase):
    def test_days_ago_template_syntax(self):
        svc = _make_service()
        result = svc._resolve_relative_time_token("{{ days_ago:10 }}")
        expected_date = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
        self.assertTrue(result.startswith(expected_date))

    def test_days_ago_plain_syntax(self):
        svc = _make_service()
        result = svc._resolve_relative_time_token("days_ago:5")
        expected_date = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
        self.assertTrue(result.startswith(expected_date))

    def test_now_minus_syntax(self):
        svc = _make_service()
        result = svc._resolve_relative_time_token("now-7d")
        expected_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        self.assertTrue(result.startswith(expected_date))

    def test_non_string_passthrough(self):
        svc = _make_service()
        self.assertEqual(svc._resolve_relative_time_token(42), 42)

    def test_unrecognized_string_passthrough(self):
        svc = _make_service()
        self.assertEqual(svc._resolve_relative_time_token("some value"), "some value")

    def test_empty_string_passthrough(self):
        svc = _make_service()
        self.assertEqual(svc._resolve_relative_time_token(""), "")


class TestNormalizeMediaType(unittest.TestCase):
    def test_movie_variants(self):
        svc = _make_service()
        for val in ("movie", "Movie", "MOVIES", "movies"):
            self.assertEqual(svc._normalize_media_type(val), "movie", f"Failed for {val!r}")

    def test_show_variants(self):
        svc = _make_service()
        for val in ("show", "shows", "series", "tv", "TV", "tvshows", "tv_show", "tv-shows"):
            self.assertEqual(svc._normalize_media_type(val), "show", f"Failed for {val!r}")

    def test_unknown_returns_empty(self):
        svc = _make_service()
        self.assertEqual(svc._normalize_media_type("podcast"), "")


class TestMapArrAction(unittest.TestCase):
    def test_arr_unmonitor_true(self):
        svc = _make_service()
        self.assertEqual(svc._map_arr_action({"arr_unmonitor": True}), 3)

    def test_arr_delete_or_unmonitor_unmonitor(self):
        svc = _make_service()
        self.assertEqual(svc._map_arr_action({"arr_delete_or_unmonitor": "unmonitor"}), 3)

    def test_arr_delete_or_unmonitor_delete(self):
        svc = _make_service()
        self.assertEqual(svc._map_arr_action({"arr_delete_or_unmonitor": "delete"}), 0)

    def test_delete_item_true(self):
        svc = _make_service()
        self.assertEqual(svc._map_arr_action({"delete_item": True}), 0)

    def test_no_action_returns_4(self):
        svc = _make_service()
        self.assertEqual(svc._map_arr_action({}), 4)


class TestBuildRuleConditions(unittest.TestCase):
    def test_empty_conditions_returns_empty(self):
        svc = _make_service()
        result = svc._build_rule_conditions(conditions={}, data_type="movie")
        self.assertEqual(result, [])

    def test_watched_true_movie(self):
        svc = _make_service()
        result = svc._build_rule_conditions(conditions={"watched": True}, data_type="movie")
        self.assertEqual(len(result), 1)
        # watched movie prop = 5, action = 0 (watched=True)
        self.assertEqual(result[0]["firstVal"], [6, 5])
        self.assertEqual(result[0]["action"], 0)

    def test_watched_false_show(self):
        svc = _make_service()
        result = svc._build_rule_conditions(conditions={"watched": False}, data_type="show")
        self.assertEqual(len(result), 1)
        # watched show prop = 17, action = 2 (not watched)
        self.assertEqual(result[0]["firstVal"], [6, 17])
        self.assertEqual(result[0]["action"], 2)

    def test_multiple_conditions_get_operator_set(self):
        svc = _make_service()
        result = svc._build_rule_conditions(
            conditions={"watched": True, "added_days_ago_gte": 30},
            data_type="movie",
        )
        self.assertEqual(len(result), 2)
        # First entry has operator=None, second has operator=0
        self.assertIsNone(result[0]["operator"])
        self.assertEqual(result[1]["operator"], 0)

    def test_favorited_by_any_user_movie(self):
        svc = _make_service()
        result = svc._build_rule_conditions(
            conditions={"favorited_by_any_user": True}, data_type="movie",
        )
        self.assertEqual(len(result), 1)
        # movie favorited prop = 39
        self.assertEqual(result[0]["firstVal"], [6, 39])


class TestLooksLikeYamlRuleSections(unittest.TestCase):
    def test_empty_list_returns_false(self):
        self.assertFalse(MaintainerrRuleTranslationService._looks_like_yaml_rule_sections([]))

    def test_none_returns_false(self):
        self.assertFalse(MaintainerrRuleTranslationService._looks_like_yaml_rule_sections(None))

    def test_valid_yaml_rule_sections(self):
        self.assertTrue(
            MaintainerrRuleTranslationService._looks_like_yaml_rule_sections(
                [{"1": [{"firstVal": [6, 5]}]}]
            )
        )

    def test_non_dict_first_element(self):
        self.assertFalse(
            MaintainerrRuleTranslationService._looks_like_yaml_rule_sections(["string"])
        )


class TestDesiredRulePayloads(unittest.TestCase):
    """Integration-level tests for _desired_rule_payloads covering the translate pipeline."""

    def _libraries(self):
        return [
            {"id": "1", "title": "Movies", "type": "movie"},
            {"id": "2", "title": "TV Shows", "type": "show"},
        ]

    def test_empty_policy_rules_returns_empty(self):
        svc = _make_service()
        result = svc._desired_rule_payloads(
            maintainerr_url="http://maintainerr:6246",
            policy_rules=[],
            libraries=self._libraries(),
        )
        self.assertEqual(result, [])

    def test_condition_based_rule_produces_payloads(self):
        svc = _make_service()
        policy_rules = [
            {
                "name": "Cleanup old watched movies",
                "description": "Remove movies watched 30+ days ago",
                "dataType": "movie",
                "conditions": {"watched": True, "added_days_ago_gte": 60},
                "actions": {"arr_delete_or_unmonitor": "unmonitor"},
            }
        ]
        result = svc._desired_rule_payloads(
            maintainerr_url="http://maintainerr:6246",
            policy_rules=policy_rules,
            libraries=self._libraries(),
        )
        self.assertTrue(len(result) >= 1)
        payload = result[0]
        self.assertEqual(payload["name"], "Cleanup old watched movies")
        self.assertEqual(payload["arrAction"], 3)  # unmonitor
        self.assertEqual(payload["dataType"], "movie")
        self.assertTrue(payload["isActive"])
        self.assertIsInstance(payload["rules"], list)
        self.assertTrue(len(payload["rules"]) >= 2)

    def test_rule_without_name_is_skipped(self):
        svc = _make_service()
        result = svc._desired_rule_payloads(
            maintainerr_url="http://maintainerr:6246",
            policy_rules=[{"conditions": {"watched": True}}],
            libraries=self._libraries(),
        )
        self.assertEqual(result, [])

    def test_rule_with_no_conditions_gets_fallback(self):
        log = MagicMock()
        svc = _make_service(log=log)
        policy_rules = [
            {
                "name": "Fallback rule",
                "dataType": "movie",
                "conditions": {},
                "actions": {},
            }
        ]
        result = svc._desired_rule_payloads(
            maintainerr_url="http://maintainerr:6246",
            policy_rules=policy_rules,
            libraries=self._libraries(),
        )
        self.assertTrue(len(result) >= 1)
        # Should have warned about fallback
        warned = any("fallback" in str(call).lower() for call in log.call_args_list)
        self.assertTrue(warned)

    def test_multi_library_produces_suffixed_names(self):
        svc = _make_service()
        policy_rules = [
            {
                "name": "All libraries rule",
                "conditions": {"watched": True},
                "actions": {},
            }
        ]
        result = svc._desired_rule_payloads(
            maintainerr_url="http://maintainerr:6246",
            policy_rules=policy_rules,
            libraries=self._libraries(),
        )
        # With 2 libraries and no dataType filter, both should be targeted
        self.assertEqual(len(result), 2)
        names = {p["name"] for p in result}
        self.assertIn("All libraries rule (Movies)", names)
        self.assertIn("All libraries rule (TV Shows)", names)


if __name__ == "__main__":
    unittest.main()
