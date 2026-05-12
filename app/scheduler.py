"""
Background scheduler — runs proactive monitoring checks on a fixed cadence.

Jobs and default intervals:
  check_failures          — every POLL_INTERVAL_MINUTES       (default 1 min)
  check_pipeline_failures — every POLL_INTERVAL_MINUTES       (default 1 min)
  check_cluster_failures  — every POLL_INTERVAL_MINUTES       (default 1 min)
  check_long_running      — every LONG_RUNNING_INTERVAL_MINUTES (default 10 min)
  daily_digest            — daily at DIGEST_HOUR:00 UTC        (default 09:00)
"""
import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def start_scheduler() -> None:
    global _scheduler

    from .alerting import (
        check_cluster_failures,
        check_failures,
        check_long_running,
        check_pipeline_failures,
        daily_digest,
    )

    poll_minutes = int(os.environ.get("POLL_INTERVAL_MINUTES", "1"))
    long_running_minutes = int(os.environ.get("LONG_RUNNING_INTERVAL_MINUTES", "10"))
    digest_hour = int(os.environ.get("DIGEST_HOUR", "9"))

    _scheduler = AsyncIOScheduler(timezone="UTC")

    # Fast failure checks — run every POLL_INTERVAL_MINUTES
    for func, job_id, label in [
        (check_failures,          "check_failures",          "Job failure check"),
        (check_pipeline_failures, "check_pipeline_failures", "DLT pipeline failure check"),
        (check_cluster_failures,  "check_cluster_failures",  "Cluster failure check"),
    ]:
        _scheduler.add_job(
            func,
            IntervalTrigger(minutes=poll_minutes),
            id=job_id,
            name=label,
            max_instances=1,
            misfire_grace_time=30,
        )

    # Long-running job check — less frequent
    _scheduler.add_job(
        check_long_running,
        IntervalTrigger(minutes=long_running_minutes),
        id="check_long_running",
        name="Long-running job check",
        max_instances=1,
        misfire_grace_time=60,
    )

    # Daily digest
    _scheduler.add_job(
        daily_digest,
        CronTrigger(hour=digest_hour, minute=0),
        id="daily_digest",
        name="Daily job digest",
        max_instances=1,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started — failure polling every %d min, "
        "long-running every %d min, daily digest at %02d:00 UTC",
        poll_minutes,
        long_running_minutes,
        digest_hour,
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
