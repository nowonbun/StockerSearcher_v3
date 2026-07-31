from __future__ import annotations

import unittest
import sqlite3
import os
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from batch.cli import build_parser
from batch.config import BatchConfig, command_for, tasks_for
from batch.history import HistoryStore
from batch import runner
from batch.runner import BatchAlreadyRunning, MarketLock


class BatchRunnerTests(unittest.TestCase):
    def test_mode_tasks_are_sequential(self) -> None:
        self.assertEqual(tasks_for("full"), ("collect", "daily", "weekly"))
        self.assertEqual(tasks_for("split"), ("base", "indicators"))
        self.assertEqual(command_for("JP", "collect")[1], "dataset/dataset_jp.py")
        self.assertEqual(command_for("KR", "base")[1], "dataset/base_dataset_kr.py")
        self.assertEqual(command_for("JP", "indicators")[1], "dataset/indicator_dataset_jp.py")
        self.assertEqual(command_for("KR", "weekly")[1], "predict/predict_week_kr_v2.py")


    def test_history_store_records_runs(self) -> None:
        store = HistoryStore(Path(":memory:"))
        store.start_run(
            run_id="run-1", market="JP", mode="collect", trigger_source="test",
            started_at="2026-07-25T00:00:00+00:00", log_path="/tmp/run.log",
        )

    def test_history_store_persists_task_and_run_statuses(self) -> None:
        class PersistentConnection(sqlite3.Connection):
            def close(self) -> None:
                pass

        connection = sqlite3.connect(":memory:", factory=PersistentConnection)
        connection.row_factory = sqlite3.Row
        store = HistoryStore(Path(":memory:"))
        store._create_schema(connection)
        with patch.object(store, "_connection", return_value=connection):
            store.start_run(
                run_id="run-1", market="JP", mode="split", trigger_source="test",
                started_at="2026-07-30T00:00:00+00:00", log_path="/tmp/run.log",
            )
            store.start_task("run-1", "base", "2026-07-30T00:00:01+00:00", "python base.py")
            store.finish_task("run-1", "base", "2026-07-30T00:00:02+00:00", "succeeded", 0)
            store.finish_run("run-1", "2026-07-30T00:00:03+00:00", "succeeded")

            self.assertEqual(store.recent_runs(1), [{
                "run_id": "run-1", "market": "JP", "mode": "split", "trigger_source": "test",
                "started_at": "2026-07-30T00:00:00+00:00", "finished_at": "2026-07-30T00:00:03+00:00",
                "status": "succeeded", "log_path": "/tmp/run.log", "error_message": None,
            }])
        sqlite3.Connection.close(connection)

    def test_batch_config_reads_paths_and_rejects_non_positive_timeout(self) -> None:
        directory = Path.cwd().resolve()
        with patch.dict("os.environ", {
            "BATCH_SOURCE_DIR": str(directory),
            "BATCH_STATE_DIR": str(directory / "state"),
            "BATCH_LOG_DIR": str(directory / "logs"),
            "BATCH_TASK_TIMEOUT_SECONDS": "30",
        }, clear=False):
            config = BatchConfig.from_environment()
            self.assertEqual(config.source_dir, directory)
            self.assertEqual(config.subprocess_timeout_seconds, 30)
            self.assertEqual(config.database_path, directory / "state" / "history.sqlite3")

        with patch.dict("os.environ", {"BATCH_TASK_TIMEOUT_SECONDS": "0"}, clear=False):
            with self.assertRaisesRegex(ValueError, "must be greater than zero"):
                BatchConfig.from_environment()

    def test_cli_parser_requires_supported_market_mode_and_history_limit(self) -> None:
        parser = build_parser()
        run = parser.parse_args(["run", "--market", "jp", "--mode", "split"])
        history = parser.parse_args(["history", "--limit", "3"])

        self.assertEqual((run.command, run.market, run.mode, run.trigger_source), ("run", "jp", "split", "cli"))
        self.assertEqual((history.command, history.limit), ("history", 3))
        with self.assertRaises(SystemExit):
            parser.parse_args(["run", "--market", "US", "--mode", "collect"])


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

    def test_streamed_subprocess_output_can_be_persisted_without_emission(self) -> None:
        log_file = StringIO()
        emitted: list[str] = []

        runner._stream_process_output(
            BytesIO(b"detail row\n"), log_file, emitted.append, emit_output=False
        )

        self.assertEqual(log_file.getvalue(), "detail row\n")
        self.assertEqual(emitted, [])

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

    def test_runner_environment_keeps_existing_pythonpath_and_adds_batch_import_roots(self) -> None:
        source = Path.cwd() / "src"
        config = BatchConfig(source, source / "state", source / "logs", 60)
        with patch.dict(os.environ, {"PYTHONPATH": "existing-path"}, clear=False):
            environment = runner._environment(config)

        self.assertEqual(
            environment["PYTHONPATH"].split(os.pathsep),
            [str(source), str(source / "create_model"), str(source / "predict"), "existing-path"],
        )
        self.assertEqual(environment["PYTHONUNBUFFERED"], "1")

    def test_output_callback_failure_keeps_persisted_output_and_falls_back_to_console(self) -> None:
        log_file = StringIO()
        callback = Mock(side_effect=RuntimeError("logger unavailable"))
        with patch("builtins.print") as console:
            runner._write_output(log_file, "batch output\n", callback)

        self.assertEqual(log_file.getvalue(), "batch output\n")
        console.assert_called_once_with("batch output\n", end="", flush=True)
