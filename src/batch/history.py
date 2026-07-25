from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class HistoryStore:
    _schema_lock = threading.Lock()
    _initialized_paths: set[str] = set()

    def __init__(self, path: Path) -> None:
        self.path = path
        self._ensure_schema()

    def _new_connection(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS batch_runs (
                run_id TEXT PRIMARY KEY,
                market TEXT NOT NULL,
                mode TEXT NOT NULL,
                trigger_source TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                log_path TEXT NOT NULL,
                error_message TEXT
            );
            CREATE TABLE IF NOT EXISTS batch_tasks (
                run_id TEXT NOT NULL,
                task_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                exit_code INTEGER,
                command TEXT NOT NULL,
                PRIMARY KEY (run_id, task_name),
                FOREIGN KEY (run_id) REFERENCES batch_runs(run_id)
            );
            """
        )

    def _ensure_schema(self) -> None:
        # Each in-memory connection needs its own schema; production uses a file path.
        if str(self.path) == ":memory:":
            return
        key = str(self.path)
        with self._schema_lock:
            if key in self._initialized_paths:
                return
            connection = self._new_connection()
            try:
                self._create_schema(connection)
            finally:
                connection.close()
            self._initialized_paths.add(key)

    def _connection(self) -> sqlite3.Connection:
        connection = self._new_connection()
        if str(self.path) == ":memory:":
            self._create_schema(connection)
        return connection

    def start_run(self, **values: str) -> None:
        connection = self._connection()
        try:
            connection.execute(
                """INSERT INTO batch_runs
                (run_id, market, mode, trigger_source, started_at, status, log_path)
                VALUES (:run_id, :market, :mode, :trigger_source, :started_at, 'running', :log_path)""",
                values,
            )
            connection.commit()
        finally:
            connection.close()

    def finish_run(self, run_id: str, finished_at: str, status: str, error_message: str | None = None) -> None:
        connection = self._connection()
        try:
            connection.execute(
                """UPDATE batch_runs SET finished_at = ?, status = ?, error_message = ?
                WHERE run_id = ?""",
                (finished_at, status, error_message, run_id),
            )
            connection.commit()
        finally:
            connection.close()

    def start_task(self, run_id: str, task_name: str, started_at: str, command: str) -> None:
        connection = self._connection()
        try:
            connection.execute(
                """INSERT INTO batch_tasks (run_id, task_name, started_at, status, command)
                VALUES (?, ?, ?, 'running', ?)""",
                (run_id, task_name, started_at, command),
            )
            connection.commit()
        finally:
            connection.close()

    def finish_task(self, run_id: str, task_name: str, finished_at: str, status: str, exit_code: int) -> None:
        connection = self._connection()
        try:
            connection.execute(
                """UPDATE batch_tasks SET finished_at = ?, status = ?, exit_code = ?
                WHERE run_id = ? AND task_name = ?""",
                (finished_at, status, exit_code, run_id, task_name),
            )
            connection.commit()
        finally:
            connection.close()

    def recent_runs(self, limit: int) -> list[dict[str, object]]:
        connection = self._connection()
        try:
            rows = connection.execute(
                """SELECT run_id, market, mode, trigger_source, started_at, finished_at, status,
                log_path, error_message FROM batch_runs ORDER BY started_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]
