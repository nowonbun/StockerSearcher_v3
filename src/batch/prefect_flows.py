import os
from datetime import UTC, datetime
from typing import Literal

from prefect import flow, get_run_logger
from prefect.runtime import flow_run
from prefect.schedules import Cron

from batch.config import MODES
from batch.runner import run_batch


Market = Literal["JP", "KR"]
Mode = Literal["collect", "predict", "full", "daily", "weekly"]
TriggerSource = Literal["prefect-ui", "prefect-schedule"]


@flow(name="stocksearcher-batch", log_prints=True)
def stocksearcher_batch(
    market: Market,
    mode: Mode = "collect",
    trigger_source: TriggerSource = "prefect-ui",
) -> dict[str, str]:
    """Run one market batch from a Prefect UI or deployment schedule."""
    if mode not in MODES:
        raise ValueError(f"Unsupported mode: {mode}")

    market = market.upper()
    if market not in {"JP", "KR"}:
        raise ValueError(f"Unsupported market: {market}")

    if trigger_source == "prefect-schedule":
        scheduled_at = flow_run.scheduled_start_time
        delay_seconds = (datetime.now(UTC) - scheduled_at.astimezone(UTC)).total_seconds()
        max_delay_seconds = int(os.environ.get("BATCH_MAX_SCHEDULE_DELAY_SECONDS", "300"))
        if delay_seconds > max_delay_seconds:
            get_run_logger().warning(
                "Skipping stale scheduled run: market=%s delay_seconds=%.0f max_delay_seconds=%s",
                market,
                delay_seconds,
                max_delay_seconds,
            )
            return {
                "status": "skipped-stale",
                "market": market,
                "mode": mode,
                "delay_seconds": str(int(delay_seconds)),
            }

    logger = get_run_logger()
    return run_batch(
        market,
        mode,
        trigger_source=trigger_source,
        output_callback=lambda text: logger.info(text.rstrip("\n")),
    )


def main() -> None:
    mode = os.environ.get("BATCH_SCHEDULE_MODE", "collect")
    if mode not in MODES:
        raise ValueError(f"Unsupported BATCH_SCHEDULE_MODE: {mode}")

    stocksearcher_batch.serve(
        name="stocksearcher",
        schedules=[
            Cron(os.environ.get("BATCH_SCHEDULE_JP", "0 12,18 * * 1-5"), timezone="Asia/Seoul", slug="jp-batch", parameters={"market": "JP", "mode": mode, "trigger_source": "prefect-schedule"}),
            Cron(os.environ.get("BATCH_SCHEDULE_KR", "0 14,20 * * 1-5"), timezone="Asia/Seoul", slug="kr-batch", parameters={"market": "KR", "mode": mode, "trigger_source": "prefect-schedule"}),
        ],
        # A delayed run is handled by the explicit stale-run guard above.
        pause_on_shutdown=False,
        # One batch at a time avoids simultaneous JP/KR model or collection load.
        limit=1,
    )


if __name__ == "__main__":
    main()
