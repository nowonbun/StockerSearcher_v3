from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import threading
import uuid
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Callable, TextIO

try:  # Linux containers use flock; the fallback keeps Windows imports testable.
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows only
    fcntl = None  # type: ignore[assignment]

from batch.config import BatchConfig, command_for, tasks_for
from batch.history import HistoryStore
from batch.process_wrapper import CANCELLED_EXIT_CODE


class BatchAlreadyRunning(RuntimeError):
    pass


_fallback_locks: dict[str, threading.Lock] = {}
_fallback_locks_guard = threading.Lock()
OutputCallback = Callable[[str], None]


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class MarketLock(AbstractContextManager["MarketLock"]):
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None
        self._fallback_lock: threading.Lock | None = None

    def __enter__(self) -> "MarketLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if fcntl is not None:
            self._file = self.path.open("a+")
            try:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                self._file.close()
                raise BatchAlreadyRunning(f"A batch for {self.path.stem} is already running") from error
            return self

        with _fallback_locks_guard:
            self._fallback_lock = _fallback_locks.setdefault(str(self.path), threading.Lock())
        if not self._fallback_lock.acquire(blocking=False):
            raise BatchAlreadyRunning(f"A batch for {self.path.stem} is already running")
        return self

    def __exit__(self, *_: object) -> None:
        if self._file is not None:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
        if self._fallback_lock is not None:
            self._fallback_lock.release()


def _environment(config: BatchConfig) -> dict[str, str]:
    environment = os.environ.copy()
    paths = [str(config.source_dir), str(config.source_dir / "create_model"), str(config.source_dir / "predict")]
    if environment.get("PYTHONPATH"):
        paths.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _write_output(
    log_file: TextIO,
    text: str,
    output_callback: OutputCallback | None,
) -> None:
    """Persist output first, then expose it to the current execution surface."""
    log_file.write(text)
    log_file.flush()
    if output_callback is not None:
        try:
            output_callback(text)
            return
        except Exception:
            # Logging must never prevent a batch subprocess from completing.
            pass
    print(text, end="", flush=True)


def _stream_process_output(
    stream: BinaryIO,
    log_file: TextIO,
    output_callback: OutputCallback | None,
) -> None:
    """Copy bounded binary chunks to the persistent log and live execution output."""
    while chunk := stream.read1(4096):
        _write_output(log_file, chunk.decode("utf-8", errors="replace"), output_callback)


def _wrapped_command(command: list[str], pgid_path: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "batch.process_wrapper",
        "--pgid-file",
        str(pgid_path),
        "--",
        *command,
    ]


def _read_pgid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _kill_process_group(pgid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pgid, sig)
    except OSError:
        pass


def _stop_wrapper(process: subprocess.Popen[bytes]) -> None:
    try:
        process.terminate()
    except OSError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass


def run_batch(
    market: str,
    mode: str,
    trigger_source: str = "cli",
    config: BatchConfig | None = None,
    output_callback: OutputCallback | None = None,
) -> dict[str, str]:
    market = market.upper()
    config = config or BatchConfig.from_environment()
    tasks = tasks_for(mode)
    run_id = uuid.uuid4().hex
    log_path = config.log_dir / f"{run_id}_{market.lower()}_{mode}.log"
    history = HistoryStore(config.database_path)

    with MarketLock(config.lock_dir / f"{market.lower()}.lock"):
        config.log_dir.mkdir(parents=True, exist_ok=True)
        history.start_run(
            run_id=run_id,
            market=market,
            mode=mode,
            trigger_source=trigger_source,
            started_at=utc_now(),
            log_path=str(log_path),
        )
        try:
            with log_path.open("w", encoding="utf-8") as log_file:
                for task_name in tasks:
                    command = command_for(market, task_name)
                    rendered_command = shlex.join(command)
                    pgid_path = config.lock_dir / f"{run_id}_{task_name}.pgid"
                    pgid_path.unlink(missing_ok=True)
                    history.start_task(run_id, task_name, utc_now(), rendered_command)
                    _write_output(
                        log_file,
                        f"[{utc_now()}] START {task_name}: {rendered_command}\n",
                        output_callback,
                    )
                    process = subprocess.Popen(
                            _wrapped_command(command, pgid_path),
                            cwd=config.source_dir,
                            env=_environment(config),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                    )
                    assert process.stdout is not None
                    output_thread = threading.Thread(
                        target=_stream_process_output,
                        args=(process.stdout, log_file, output_callback),
                        daemon=True,
                    )
                    output_thread.start()
                    try:
                        exit_code = process.wait(timeout=config.subprocess_timeout_seconds)
                        output_thread.join()
                    except subprocess.TimeoutExpired as error:
                        pgid = _read_pgid(pgid_path)
                        if pgid is None:
                            _write_output(
                                log_file,
                                f"[{utc_now()}] WARNING {task_name}: inner process group unavailable\n",
                                output_callback,
                            )
                        else:
                            _kill_process_group(pgid, signal.SIGKILL)
                        _stop_wrapper(process)
                        process.wait()
                        output_thread.join()
                        history.finish_task(run_id, task_name, utc_now(), "failed", -1)
                        _write_output(
                            log_file,
                            f"[{utc_now()}] TIMEOUT {task_name}: "
                            f"limit={config.subprocess_timeout_seconds}s\n",
                            output_callback,
                        )
                        raise RuntimeError(f"{task_name} timed out after {config.subprocess_timeout_seconds}s") from error
                    finally:
                        pgid_path.unlink(missing_ok=True)
                    if exit_code == CANCELLED_EXIT_CODE:
                        history.finish_task(run_id, task_name, utc_now(), "cancelled", exit_code)
                        _write_output(
                            log_file,
                            f"[{utc_now()}] CANCELLED {task_name}\n",
                            output_callback,
                        )
                        raise KeyboardInterrupt("batch subprocess cancelled")
                    status = "succeeded" if exit_code == 0 else "failed"
                    history.finish_task(run_id, task_name, utc_now(), status, exit_code)
                    _write_output(
                        log_file,
                        f"[{utc_now()}] END {task_name}: exit_code={exit_code}\n",
                        output_callback,
                    )
                    if exit_code != 0:
                        raise RuntimeError(f"{task_name} failed with exit code {exit_code}")
        except KeyboardInterrupt:
            history.finish_run(run_id, utc_now(), "cancelled", "flow run cancelled")
            raise
        except Exception as error:
            history.finish_run(run_id, utc_now(), "failed", str(error))
            raise
        history.finish_run(run_id, utc_now(), "succeeded")
    return {"run_id": run_id, "status": "succeeded", "log_path": str(log_path)}
