"""APScheduler worker — runs intel cycles on an interval."""
from __future__ import annotations

import logging
import os
import time

from apscheduler.schedulers.blocking import BlockingScheduler

from packages.agent import run_intel_cycle
from packages.db.session import get_session_factory, init_db

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("signalwatch.worker")


def job() -> None:
    Session = get_session_factory()
    with Session() as db:
        run = run_intel_cycle(db)
        logger.info("Run %s status=%s summary=%s", run.id, run.status, run.summary)


def main() -> None:
    init_db()
    minutes = int(os.getenv("SCHEDULE_MINUTES", "60"))
    sched = BlockingScheduler()
    sched.add_job(job, "interval", minutes=minutes, id="intel_cycle", max_instances=1)
    logger.info("Worker started — every %s minutes", minutes)
    # Optional immediate run
    if os.getenv("RUN_ON_START", "1") == "1":
        job()
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker stopped")


if __name__ == "__main__":
    main()
