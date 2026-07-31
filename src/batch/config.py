from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


MARKETS = ("JP", "KR")
MODES = ("collect", "predict", "full", "daily", "weekly", "base", "indicators", "split")


@dataclass(frozen=True)
class BatchConfig:
    source_dir: Path
    state_dir: Path
    log_dir: Path
    subprocess_timeout_seconds: int

    @classmethod
    def from_environment(cls) -> "BatchConfig":
        source_dir = Path(os.environ.get("BATCH_SOURCE_DIR", Path.cwd())).resolve()
        state_dir = Path(os.environ.get("BATCH_STATE_DIR", source_dir / "data" / "batch")).resolve()
        log_dir = Path(os.environ.get("BATCH_LOG_DIR", state_dir / "logs")).resolve()
        timeout = int(os.environ.get("BATCH_TASK_TIMEOUT_SECONDS", "7200"))
        if timeout <= 0:
            raise ValueError("BATCH_TASK_TIMEOUT_SECONDS must be greater than zero")
        return cls(source_dir=source_dir, state_dir=state_dir, log_dir=log_dir, subprocess_timeout_seconds=timeout)

    @property
    def database_path(self) -> Path:
        return self.state_dir / "history.sqlite3"

    @property
    def lock_dir(self) -> Path:
        return self.state_dir / "locks"


def command_for(market: str, task: str) -> list[str]:
    market = market.upper()
    if market not in MARKETS:
        raise ValueError(f"Unsupported market: {market}")

    commands = {
        "collect": [sys.executable, f"dataset/dataset_{market.lower()}.py"],
        "base": [sys.executable, f"dataset/base_dataset_{market.lower()}.py"],
        "indicators": [sys.executable, f"dataset/indicator_dataset_{market.lower()}.py"],
        "daily": [sys.executable, f"predict/predict_{market.lower()}_v2.py", "--save-db"],
        "weekly": [sys.executable, f"predict/predict_week_{market.lower()}_v2.py", "--save-db"],
    }
    return commands[task]


def tasks_for(mode: str) -> tuple[str, ...]:
    if mode == "collect":
        return ("collect",)
    if mode == "base":
        return ("base",)
    if mode == "indicators":
        return ("indicators",)
    if mode == "split":
        return ("base", "indicators")
    if mode == "predict":
        return ("daily", "weekly")
    if mode == "full":
        return ("collect", "daily", "weekly")
    if mode in {"daily", "weekly"}:
        return (mode,)
    raise ValueError(f"Unsupported mode: {mode}")
