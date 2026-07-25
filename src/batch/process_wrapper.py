from __future__ import annotations

import argparse
import ctypes
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


PR_SET_PDEATHSIG = 1
CANCELLED_EXIT_CODE = 130
_cancel_requested = False


def _request_cancel(*_: object) -> None:
    global _cancel_requested
    _cancel_requested = True


def _kill_group(pgid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pgid, sig)
    except OSError:
        # The child may already have exited after a simultaneous timeout cleanup.
        pass


def _stop_child(child: subprocess.Popen[bytes]) -> None:
    _kill_group(child.pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while child.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    if child.poll() is None:
        _kill_group(child.pid, signal.SIGKILL)
    child.wait()


def _write_pgid(path: Path, pgid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(f"{pgid}\n", encoding="utf-8")
    os.replace(temporary, path)


def _set_parent_death_signal() -> None:
    if sys.platform != "linux":
        raise RuntimeError("batch.process_wrapper requires Linux")
    parent_pid = os.getppid()
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    if libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG) failed")
    # Close the race where the parent died before prctl was configured.
    if os.getppid() != parent_pid:
        _request_cancel()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pgid-file", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    if not arguments.command or arguments.command[0] != "--":
        parser.error("command must follow --")

    _set_parent_death_signal()
    signal.signal(signal.SIGTERM, _request_cancel)
    signal.signal(signal.SIGINT, _request_cancel)
    if _cancel_requested:
        return CANCELLED_EXIT_CODE

    child = subprocess.Popen(arguments.command[1:], start_new_session=True)
    _write_pgid(arguments.pgid_file, child.pid)
    try:
        while child.poll() is None:
            if _cancel_requested:
                _stop_child(child)
                return CANCELLED_EXIT_CODE
            time.sleep(0.1)
        return child.returncode
    finally:
        arguments.pgid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
