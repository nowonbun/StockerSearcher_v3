from __future__ import annotations

import unittest
from pathlib import Path

from batch.config import command_for, tasks_for
from batch.history import HistoryStore
from batch import runner
from batch.runner import BatchAlreadyRunning, MarketLock


class BatchRunnerTests(unittest.TestCase):
    def test_mode_tasks_are_sequential(self) -> None:
        self.assertEqual(tasks_for("full"), ("collect", "daily", "weekly"))
        self.assertEqual(command_for("JP", "collect")[1], "dataset/dataset_jp.py")
        self.assertEqual(command_for("KR", "weekly")[1], "predict/predict_week_kr_v2.py")


    def test_history_store_records_runs(self) -> None:
        store = HistoryStore(Path(":memory:"))
        store.start_run(
            run_id="run-1", market="JP", mode="collect", trigger_source="test",
            started_at="2026-07-25T00:00:00+00:00", log_path="/tmp/run.log",
        )


    def test_market_lock_rejects_overlap(self) -> None:
        original_fcntl = runner.fcntl
        runner.fcntl = None
        try:
            with MarketLock(Path("jp.lock")):
                with self.assertRaises(BatchAlreadyRunning):
                    with MarketLock(Path("jp.lock")):
                        pass
        finally:
            runner.fcntl = original_fcntl
