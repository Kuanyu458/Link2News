import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import common


ROOT = Path(__file__).resolve().parents[1]


class ConfigResolutionTests(unittest.TestCase):
    def test_explicit_config_has_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("collector: {base_url: 'https://example.test'}\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"WEEKLY_REPORT_CONFIG": str(path)}):
                self.assertEqual(common.config_path(), path)


class PublicCliTests(unittest.TestCase):
    def test_dry_run_is_machine_readable_and_declares_no_writes(self):
        env = os.environ.copy()
        env["WEEKLY_REPORT_CONFIG"] = str(ROOT / "pipeline" / "config.example.yaml")
        result = subprocess.run(
            [sys.executable, "-m", "pipeline.cli", "run", "--dry-run"],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["external_writes"], [])
        self.assertEqual(payload["mode"], "all")


if __name__ == "__main__":
    unittest.main()
