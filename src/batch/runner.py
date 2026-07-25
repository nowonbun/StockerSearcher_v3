from __future__ import annotations

import os
import shlex
import signal
import subprocess
import threading
import uuid
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path

try:  # Linux containers use flock; the fallback keeps Windows imports testable.
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows only
    fcntl = None  # type: ignore[assignment]

from batch.config import BatchConfig, command_for, tasks_for
from batch.history import HistoryStore


class BatchAlreadyRunning(RuntimeError):
    pass


_fallback_locks: dict[str, threading.Lock] = {}
_fallback_locks_guard = threading.Lock()


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


def run_batch(market: str, mode: str, trigger_source: str = "cli", config: BatchConfig | None = None) -> dict[str, str]:
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
                    history.start_task(run_id, task_name, utc_now(), rendered_command)
                    log_file.write(f"[{utc_now()}] START {task_name}: {rendered_command}\n")
                    log_file.flush()
                    process = subprocess.Popen(
                            command,
                            cwd=config.source_dir,
                            env=_environment(config),
                            stdout=log_file,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                    )
                    try:
                        exit_code = process.wait(timeout=config.subprocess_timeout_seconds)
                    except subprocess.TimeoutExpired as error:
                        if os.name == "posix":
                            os.killpg(process.pid, signal.SIGKILL)
                        else:  # pragma: no cover - Linux containers are the runtime target.
                            process.kill()
                        process.wait()
                        history.finish_task(run_id, task_name, utc_now(), "failed", -1)
                        log_file.write(
                            f"[{utc_now()}] TIMEOUT {task_name}: "
                            f"limit={config.subprocess_timeout_seconds}s\n"
                        )
                        log_file.flush()
                        raise RuntimeError(f"{task_name} timed out after {config.subprocess_timeout_seconds}s") from error
                    status = "succeeded" if exit_code == 0 else "failed"
                    history.finish_task(run_id, task_name, utc_now(), status, exit_code)
                    log_file.write(f"[{utc_now()}] END {task_name}: exit_code={exit_code}\n")
                    log_file.flush()
                    if exit_code != 0:
                        raise RuntimeError(f"{task_name} failed with exit code {exit_code}")
        except Exception as error:
            history.finish_run(run_id, utc_now(), "failed", str(error))
            raise
        history.finish_run(run_id, utc_now(), "succeeded")
    return {"run_id": run_id, "status": "succeeded", "log_path": str(log_path)}
