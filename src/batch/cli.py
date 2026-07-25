from __future__ import annotations

import argparse
import json
import sys

from batch.config import MARKETS, MODES, BatchConfig
from batch.history import HistoryStore
from batch.runner import BatchAlreadyRunning, run_batch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run and inspect StockSearcher batches.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run one market batch.")
    run.add_argument("--market", choices=[market.lower() for market in MARKETS], required=True)
    run.add_argument("--mode", choices=MODES, required=True)
    run.add_argument("--trigger-source", default="cli")
    history = commands.add_parser("history", help="Print recent batch runs as JSON.")
    history.add_argument("--limit", type=int, default=20)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    config = BatchConfig.from_environment()
    if arguments.command == "history":
        print(json.dumps(HistoryStore(config.database_path).recent_runs(arguments.limit), ensure_ascii=False, indent=2))
        return 0
    try:
        result = run_batch(arguments.market, arguments.mode, arguments.trigger_source, config)
    except BatchAlreadyRunning as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
