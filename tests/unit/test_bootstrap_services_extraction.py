import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.bazarr_service import BazarrService  # noqa: E402
from bootstrap_services.apps.jellyseerr.service import JellyseerrService  # noqa: E402
from bootstrap_services.media_hygiene_service import MediaHygieneService  # noqa: E402


class ServiceExtractionSmokeTests(unittest.TestCase):
    def test_bazarr_service_noop_when_disabled(self):
        svc = BazarrService(
            log=mock.Mock(),
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            normalize_url=lambda url: url,
            wait_for_service=mock.Mock(),
            get_arr_app=mock.Mock(),
            parse_service_url=mock.Mock(),
            coerce_list=lambda value: value if isinstance(value, list) else [],
            resolve_path=mock.Mock(),
            apply_scalar_updates=mock.Mock(),
        )

        changed = svc.ensure_arr_integration(
            cfg={"bazarr": {"enabled": False}},
            config_root="/srv-config",
            arr_apps=[],
            app_keys={},
            wait_timeout=30,
        )
        self.assertFalse(changed)
        svc.wait_for_service.assert_not_called()

    def test_jellyseerr_service_noop_when_disabled(self):
        svc = JellyseerrService(
            log=mock.Mock(),
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            normalize_url=lambda url: url,
            wait_for_service=mock.Mock(),
            resolve_jellyfin_api_key=mock.Mock(),
            parse_service_url=mock.Mock(),
            to_int=lambda value, default=None: value if value is not None else default,
            coerce_list=lambda value: value if isinstance(value, list) else [],
            choose_profile=mock.Mock(),
            choose_root_folder=mock.Mock(),
            normalize_base_path=lambda value: value,
            find_existing_servarr=mock.Mock(),
            read_json_file=mock.Mock(),
            get_arr_app=mock.Mock(),
            detect_arr_api_base=mock.Mock(),
            get_arr_quality_profile=mock.Mock(),
            get_arr_root_folder_path=mock.Mock(),
            get_sonarr_language_profile_id=mock.Mock(),
            read_jellyseerr_api_key=mock.Mock(),
            http_request=mock.Mock(),
        )

        svc.configure(
            cfg={"jellyseerr": {"enabled": False}},
            arr_apps=[],
            app_keys={},
            config_root="/srv-config",
            wait_timeout=30,
        )
        svc.wait_for_service.assert_not_called()

    def test_media_hygiene_service_noop_when_disabled(self):
        svc = MediaHygieneService(
            log=mock.Mock(),
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            normalize_url=lambda value: value,
            detect_arr_api_base=mock.Mock(),
            ensure_arr_failed_queue_cleanup=mock.Mock(),
            run_filesystem_hygiene=mock.Mock(),
            run_qbit_ipfilter_refresh=mock.Mock(),
            run_qbit_queue_guardrails=mock.Mock(),
            run_qbit_duplicate_prune=mock.Mock(),
        )

        svc.run(
            cfg={"media_hygiene": {"enabled": False}},
            arr_apps=[],
            app_keys={},
            qbit_cfg={},
            qb_username="admin",
            qb_password="secret",
        )
        svc.run_filesystem_hygiene.assert_not_called()


if __name__ == "__main__":
    unittest.main()
