import unittest

from media_stack.cli.workflows.controller_job_artifacts_service import ControllerJobArtifactsService


class ControllerJobArtifactsServiceTests(unittest.TestCase):
    def test_create_and_cleanup(self):
        svc = ControllerJobArtifactsService()
        artifacts = svc.create()
        self.assertTrue(artifacts.job_log_file.exists())
        self.assertTrue(artifacts.job_config_file.exists())
        svc.cleanup(artifacts)
        self.assertFalse(artifacts.job_log_file.exists())
        self.assertFalse(artifacts.job_config_file.exists())


if __name__ == "__main__":
    unittest.main()
