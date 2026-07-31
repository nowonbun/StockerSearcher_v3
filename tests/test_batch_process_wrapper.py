from __future__ import annotations

import signal
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from batch import process_wrapper


class ProcessWrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        process_wrapper._cancel_requested = False

    def tearDown(self) -> None:
        process_wrapper._cancel_requested = False

    def test_non_linux_parent_death_configuration_is_rejected(self) -> None:
        with patch.object(process_wrapper.sys, "platform", "win32"):
            with self.assertRaisesRegex(RuntimeError, "requires Linux"):
                process_wrapper._set_parent_death_signal()

    def test_cancel_before_child_start_returns_cancelled_without_spawning(self) -> None:
        process_wrapper._cancel_requested = True
        with (
            patch.object(process_wrapper, "_set_parent_death_signal"),
            patch.object(process_wrapper.subprocess, "Popen") as popen,
            patch.object(process_wrapper.signal, "signal"),
        ):
            result = process_wrapper.main(["--pgid-file", "state/cancel.pgid", "--", "python", "worker.py"])

        self.assertEqual(result, process_wrapper.CANCELLED_EXIT_CODE)
        popen.assert_not_called()

    def test_main_runs_wrapped_command_writes_pgid_and_cleans_up(self) -> None:
        child = MagicMock()
        child.pid = 123
        child.poll.side_effect = [None, 0]
        child.returncode = 0
        with (
            patch.object(process_wrapper, "_set_parent_death_signal"),
            patch.object(process_wrapper.subprocess, "Popen", return_value=child) as popen,
            patch.object(process_wrapper, "_write_pgid") as write_pgid,
            patch.object(process_wrapper.signal, "signal"),
            patch.object(process_wrapper.time, "sleep"),
        ):
            result = process_wrapper.main(["--pgid-file", "state/run.pgid", "--", "python", "worker.py"])

        self.assertEqual(result, 0)
        popen.assert_called_once_with(["python", "worker.py"], start_new_session=True)
        write_pgid.assert_called_once()
        self.assertEqual(write_pgid.call_args.args[1], 123)

    def test_stop_child_escalates_to_sigkill_without_real_waiting(self) -> None:
        child = MagicMock()
        child.pid = 44
        child.poll.side_effect = [None, None, None]
        sigkill = object()
        with (
            patch.object(process_wrapper, "_kill_group") as kill_group,
            patch.object(process_wrapper.time, "monotonic", side_effect=[0, 0, 6]),
            patch.object(process_wrapper.time, "sleep") as sleep,
            patch.object(process_wrapper.signal, "SIGKILL", sigkill, create=True),
        ):
            process_wrapper._stop_child(child)

        self.assertEqual(kill_group.call_args_list, [
            unittest.mock.call(44, signal.SIGTERM),
            unittest.mock.call(44, sigkill),
        ])
        sleep.assert_called_once_with(0.1)
        child.wait.assert_called_once()


if __name__ == "__main__":
    unittest.main()
