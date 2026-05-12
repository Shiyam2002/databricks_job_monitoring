"""
Background scheduler — runs proactive monitoring checks on a fixed cadence.

Jobs:
  check_failures     — every POLL_INTERVAL_MINUTES (default 15)
  check_long_running — every POLL_INTERVAL_MINUTES (default 15)
  daily_digest       — daily at DIGEST_HOUR:00 (default 09:00)
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

    from .alerting import check_failures, check_long_running, daily_digest

    poll_minutes = int(os.environ.get("POLL_INTERVAL_MINUTES", "15"))
    digest_hour = int(os.environ.get("DIGEST_HOUR", "9"))

    _scheduler = AsyncIOScheduler(timezone="UTC")

    _scheduler.add_job(
        check_failures,
        IntervalTrigger(minutes=poll_minutes),
        id="check_failures",
        name="Job failure check",
        max_instances=1,
        misfire_grace_time=60,
    )
    _scheduler.add_job(
        check_long_running,
        IntervalTrigger(minutes=poll_minutes),
        id="check_long_running",
        name="Long-running job check",
        max_instances=1,
        misfire_grace_time=60,
    )
    _scheduler.add_job(
        daily_digest,
        CronTrigger(hour=digest_hour, minute=0),
        id="daily_digest",
        name="Daily job digest",
        max_instances=1,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started — polling every %d min, daily digest at %02d:00 UTC",
        poll_minutes,
        digest_hour,
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
