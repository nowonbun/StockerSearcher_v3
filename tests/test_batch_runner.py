from __future__ import annotations

import unittest
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import Mock

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

    def test_streamed_subprocess_output_is_persisted_and_emitted(self) -> None:
        log_file = StringIO()
        emitted: list[str] = []

        runner._stream_process_output(
            BytesIO(b"first line\ninvalid: \xff\n"), log_file, emitted.append
        )

        self.assertEqual(log_file.getvalue(), "first line\ninvalid: �\n")
        self.assertEqual(emitted, ["first line\ninvalid: �\n"])

    def test_wrapper_command_and_pgid_read_are_task_specific(self) -> None:
        pgid_path = Path("state/run_collect.pgid")
        command = runner._wrapped_command(["python", "dataset/example.py"], pgid_path)
        self.assertEqual(command[-3:], ["--", "python", "dataset/example.py"])
        self.assertIn("batch.process_wrapper", command)
        missing_path = Mock()
        missing_path.read_text.side_effect = OSError()
        self.assertIsNone(runner._read_pgid(missing_path))
        present_path = Mock()
        present_path.read_text.return_value = "12345\n"
        self.assertEqual(runner._read_pgid(present_path), 12345)
