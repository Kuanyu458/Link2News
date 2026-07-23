from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from demo.service import DemoRejectedError, QuotaStore, cleanup_expired_jobs


class DemoServiceGuardTests(unittest.TestCase):
    def test_quota_rejects_third_attempt_per_client(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuotaStore(Path(directory) / "quota.sqlite", salt="test")
            store.consume("203.0.113.10")
            store.consume("203.0.113.10")
            with self.assertRaisesRegex(DemoRejectedError, "2 次"):
                store.consume("203.0.113.10")

    def test_turnstile_token_cannot_be_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuotaStore(Path(directory) / "quota.sqlite", salt="test")
            store.consume_turnstile_token("token")
            with self.assertRaisesRegex(DemoRejectedError, "已使用"):
                store.consume_turnstile_token("token")

    def test_cleanup_removes_only_expired_job_directories(self):
        import demo.service as module

        with tempfile.TemporaryDirectory() as directory:
            original = module.WORK_ROOT
            module.WORK_ROOT = Path(directory)
            try:
                old = module.WORK_ROOT / "jobs" / "old"
                fresh = module.WORK_ROOT / "jobs" / "fresh"
                old.mkdir(parents=True)
                fresh.mkdir()
                old_time = time.time() - 3700
                import os
                os.utime(old, (old_time, old_time))
                self.assertEqual(cleanup_expired_jobs(), 1)
                self.assertFalse(old.exists())
                self.assertTrue(fresh.exists())
            finally:
                module.WORK_ROOT = original
