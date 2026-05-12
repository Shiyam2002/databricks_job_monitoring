"""
Proactive alert checks run by the scheduler.

Each check:
  1. Queries the relevant Databricks system table via the tool function
  2. Filters out run_ids already alerted on (in-memory dedup — resets on restart)
  3. Sends a Teams card for any new findings
"""
import json
import logging
import os

from .notifier import send_teams_alert
from .tools import get_failed_jobs, get_job_run_summary, get_long_running_jobs

logger = logging.getLogger(__name__)

# In-memory sets — prevents re-alerting the same run within an app session
_alerted_failure_ids: set[str] = set()
_alerted_long_running_ids: set[str] = set()


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except ValueError:
        return default


def check_failures() -> None:
    """Alert on any new FAILED / TIMEDOUT / INTERNAL_ERROR runs."""
    hours_back = _env_int("ALERT_FAILURE_HOURS_BACK", 1)
    try:
        result = get_failed_jobs(hours_back=hours_back)
        if result.startswith("No failed"):
            logger.info("check_failures: no failures found")
            return

        rows = json.loads(result)
        new_rows = [r for r in rows if str(r.get("run_id")) not in _alerted_failure_ids]
        if not new_rows:
            logger.info("check_failures: %d failure(s) already alerted", len(rows))
            return

        lines = []
        for r in new_rows:
            _alerted_failure_ids.add(str(r.get("run_id")))
            job_name = r.get("job_name") or f"Job {r.get('job_id')}"
            state = r.get("result_state", "FAILED")
            start = r.get("start_time", "unknown time")
            duration = r.get("duration_minutes")
            dur_str = f" ({duration} min)" if duration is not None else ""
            lines.append(f"**{job_name}** — {state}{dur_str} | started {start} | run_id: {r.get('run_id')}")

        body = "\n\n".join(lines)
        send_teams_alert(
            title=f"🚨 {len(new_rows)} Job Failure(s) Detected",
            body=body,
            color="FF0000",
        )
        logger.warning("Alerted on %d new job failure(s)", len(new_rows))

    except Exception:
        logger.exception("check_failures raised an unexpected error")


def check_long_running() -> None:
    """Alert on jobs running longer than the configured threshold."""
    threshold = _env_int("ALERT_LONG_RUNNING_MINUTES", 120)
    try:
        result = get_long_running_jobs(threshold_minutes=threshold)
        if result.startswith("No jobs"):
            logger.info("check_long_running: no long-running jobs")
            return

        rows = json.loads(result)
        new_rows = [r for r in rows if str(r.get("run_id")) not in _alerted_long_running_ids]
        if not new_rows:
            logger.info("check_long_running: %d long-running job(s) already alerted", len(rows))
            return

        lines = []
        for r in new_rows:
            _alerted_long_running_ids.add(str(r.get("run_id")))
            job_name = r.get("job_name") or f"Job {r.get('job_id')}"
            mins = r.get("running_minutes", "?")
            start = r.get("start_time", "unknown time")
            lines.append(f"**{job_name}** — running for **{mins} minutes** | started {start} | run_id: {r.get('run_id')}")

        body = "\n\n".join(lines)
        send_teams_alert(
            title=f"⏱️ {len(new_rows)} Long-Running Job(s) Detected",
            body=body,
            color="FFA500",
        )
        logger.warning("Alerted on %d long-running job(s)", len(new_rows))

    except Exception:
        logger.exception("check_long_running raised an unexpected error")


def daily_digest() -> None:
    """Send a daily summary of all job run outcomes over the past 24 hours."""
    try:
        result = get_job_run_summary(days_back=1)
        if result.startswith("No job"):
            send_teams_alert(
                title="📊 Daily Databricks Job Digest",
                body="No job runs recorded in the last 24 hours.",
                color="0078D4",
            )
            return

        rows = json.loads(result)

        # Group by job name for a cleaner card
        lines = []
        for r in rows:
            job = r.get("job_name") or "Unknown Job"
            state = r.get("result_state", "?")
            count = r.get("run_count", 0)
            avg = r.get("avg_duration_minutes")
            avg_str = f" | avg {avg:.1f} min" if avg is not None else ""
            emoji = "✅" if state == "SUCCEEDED" else "❌" if state in ("FAILED", "TIMEDOUT", "INTERNAL_ERROR") else "ℹ️"
            lines.append(f"{emoji} **{job}** [{state}] — {count} run(s){avg_str}")

        body = "\n\n".join(lines)
        send_teams_alert(
            title="📊 Daily Databricks Job Digest",
            body=body,
            color="0078D4",
        )
        logger.info("Daily digest sent (%d entries)", len(rows))

    except Exception:
        logger.exception("daily_digest raised an unexpected error")
