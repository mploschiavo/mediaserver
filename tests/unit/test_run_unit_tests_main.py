import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.run_unit_tests_main import build_parser, main  # noqa: E402


class RunUnitTestsMainTests(unittest.TestCase):
    def test_build_parser_uses_env_defaults(self):
        with mock.patch.dict(
            os.environ,
            {
                "UNIT_TEST_START_DIR": "tests/custom",
                "UNIT_TEST_PATTERN": "test_custom_*.py",
                "UNIT_TEST_TOP_N": "7",
                "UNIT_TEST_VERBOSITY": "2",
                "UNIT_TEST_FAILFAST": "1",
                "UNIT_TEST_TIMEOUT_SECONDS": "12.5",
            },
            clear=False,
        ):
            parser = build_parser()
            args = parser.parse_args([])

        self.assertEqual(args.start_dir, "tests/custom")
        self.assertEqual(args.pattern, "test_custom_*.py")
        self.assertEqual(args.top_n, 7)
        self.assertEqual(args.verbosity, 2)
        self.assertTrue(args.failfast)
        self.assertEqual(args.timeout_seconds, 12.5)

    def test_main_builds_runner_config_and_returns_runner_exit_code(self):
        with mock.patch("cli.run_unit_tests_main.run_discovered_unit_tests") as run_mock:
            run_mock.return_value = (3, [])
            rc = main(
                [
                    "--start-dir",
                    "tests/unit",
                    "--pattern",
                    "test_*.py",
                    "--top-n",
                    "5",
                    "--verbosity",
                    "2",
                    "--failfast",
                    "--timeout-seconds",
                    "9.5",
                ]
            )

        self.assertEqual(rc, 3)
        run_mock.assert_called_once()
        cfg = run_mock.call_args.kwargs["cfg"]
        self.assertEqual(cfg.start_dir, "tests/unit")
        self.assertEqual(cfg.pattern, "test_*.py")
        self.assertEqual(cfg.top_n, 5)
        self.assertEqual(cfg.verbosity, 2)
        self.assertTrue(cfg.failfast)
        self.assertEqual(cfg.timeout_seconds, 9.5)
        self.assertEqual(cfg.root_dir, ROOT)

    def test_main_ensures_repo_and_scripts_are_on_sys_path(self):
        repo_root = str(ROOT)
        scripts_root = str(ROOT / "scripts")
        trimmed_path = [item for item in sys.path if item not in {repo_root, scripts_root}]

        with (
            mock.patch.object(sys, "path", trimmed_path),
            mock.patch("cli.run_unit_tests_main.run_discovered_unit_tests") as run_mock,
        ):
            run_mock.return_value = (0, [])
            rc = main(["--verbosity", "0"])
            self.assertIn(repo_root, sys.path)
            self.assertIn(scripts_root, sys.path)

        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
