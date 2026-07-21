import os
import sys
import unittest
import tempfile
import shutil
from pathlib import Path

# Ensure scripts directory is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import sync_daily_check


class TestSyncDailyCheck(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.yard_dir = Path(self.temp_dir) / "SYNC"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cmd_check_nonexistent_dir(self):
        non_existent = Path(self.temp_dir) / "NON_EXISTENT"
        ret = sync_daily_check.main(["check", "--dir", str(non_existent), "--host", "TESTHOST"])
        self.assertEqual(ret, 0)

    def test_cmd_mark_and_check_flow(self):
        # 1. Initially check returns 0 because LOG_NAME does not exist yet (gate inactive)
        ret_initial = sync_daily_check.cmd_check(self.yard_dir, "TESTHOST")
        self.assertEqual(ret_initial, 0)

        # 2. Mark today's sync
        ret_mark = sync_daily_check.cmd_mark(self.yard_dir, "TESTHOST", "Initial test sync")
        self.assertEqual(ret_mark, 0)

        # 3. Check again -> should return 0 (synced today)
        ret_check_after = sync_daily_check.cmd_check(self.yard_dir, "TESTHOST")
        self.assertEqual(ret_check_after, 0)

        # 4. Verify log file exists and has content
        log_file = self.yard_dir / sync_daily_check.LOG_NAME
        self.assertTrue(log_file.exists())
        content = log_file.read_text(encoding="utf-8")
        self.assertIn("TESTHOST", content)
        self.assertIn("Initial test sync", content)

    def test_cmd_check_due_when_log_exists_without_host(self):
        # Create log with a different date / host
        self.yard_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.yard_dir / sync_daily_check.LOG_NAME
        log_file.write_text(sync_daily_check.HEADER + "| 2026-01-01 | OTHERHOST | note |\n", encoding="utf-8")

        # Now check for TESTHOST -> should return 1 (sync due)
        ret = sync_daily_check.cmd_check(self.yard_dir, "TESTHOST")
        self.assertEqual(ret, 1)

    def test_resolve_dir_env(self):
        os.environ["SYNC_MASTER_DIR"] = self.temp_dir
        try:
            resolved = sync_daily_check.resolve_dir(None)
            self.assertEqual(resolved, Path(self.temp_dir))
        finally:
            del os.environ["SYNC_MASTER_DIR"]

    def test_cli_missing_dir(self):
        env_backup = os.environ.pop("SYNC_MASTER_DIR", None)
        try:
            ret = sync_daily_check.main(["check"])
            self.assertEqual(ret, 2)
        finally:
            if env_backup:
                os.environ["SYNC_MASTER_DIR"] = env_backup


if __name__ == "__main__":
    unittest.main()
