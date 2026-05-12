"""
Proactive alert checks run by the background scheduler.

Each check:
  1. Reads the last-checked timestamp from the persistent state file
  2. Queries Databricks only for events SINCE that timestamp (no redundant full-history scans)
  3. Filters out run IDs already alerted on (persisted across restarts)
  4. Sends a Teams card for any new findings
  5. Updates last-checked and alerted IDs in the state file

On query failure the last-checked timestamp is NOT updated, so the next poll
automatically retries the same time window.
"""
import logging

from . import state
from .notifier import send_teams_alert
from .tools import (
    get_cluster_failures_since,
    get_job_failures_since,
    get_job_run_summary,
    get_long_running_jobs,
    get_pipeline_failures_since,
)

logger = logging.getLogger(__name__)


def _env_int(key: str, default: int) -> int:
    import os
    try:
        return int(os.environ.get(key, default))
    except ValueError:
        return default


# ── Job failures ──────────────────────────────────────────────────────────────

def check_failures() -> None:
    """Alert on any new FAILED / TIMEDOUT / INTERNAL_ERROR job runs."""
    key = "job_failures"
    fallback = _env_int("ALERT_FAILURE_HOURS_BACK", 1) * 60
    since = state.get_last_checked(key, fallback_minutes=fallback)

    try:
        rows = get_job_failures_since(since)
        state.set_last_checked(key)  # advance window even if no results

        if not rows:
            logger.info("check_failures: no new failures since %s", since)
            return

        alerted = state.get_alerted_ids(key)
        new_rows = [r for r in rows if str(r.get("run_id")) not in alerted]
        if not new_rows:
            logger.info("check_failures: %d failure(s) already alerted", len(rows))
            return

        lines, new_ids = [], set()
        for r in new_rows:
            new_ids.add(str(r.get("run_id")))
            job_name = r.get("job_name") or f"Job {r.get('job_id')}"
            state_val = r.get("result_state", "FAILED")
            start = r.get("start_time", "unknown")
            dur = r.get("duration_minutes")
            dur_str = f" ({dur} min)" if dur is not None else ""
            lines.append(
                f"**{job_name}** — {state_val}{dur_str} | started {start} | run_id: {r.get('run_id')}"
            )

        send_teams_alert(
            title=f"🚨 {len(new_rows)} Job Failure(s) Detected",
            body="\n\n".join(lines),
            color="FF0000",
        )
        state.add_alerted_ids(key, new_ids)
        logger.warning("Alerted on %d new job failure(s)", len(new_rows))

    except Exception:
        logger.exception("check_failures raised an unexpected error")
        # last_checked not updated on exception — next poll retries same window


# ── DLT Pipeline failures ─────────────────────────────────────────────────────

def check_pipeline_failures() -> None:
    """Alert on any new DLT pipeline runs that ended in FAILED / INTERNAL_ERROR."""
    key = "pipeline_failures"
    fallback = _env_int("ALERT_FAILURE_HOURS_BACK", 1) * 60
    since = state.get_last_checked(key, fallback_minutes=fallback)

    try:
        rows = get_pipeline_failures_since(since)
        state.set_last_checked(key)

        if not rows:
            logger.info("check_pipeline_failures: no new failures since %s", since)
            return

        alerted = state.get_alerted_ids(key)
        new_rows = [r for r in rows if str(r.get("update_id")) not in alerted]
        if not new_rows:
            return

        lines, new_ids = [], set()
        for r in new_rows:
            uid = str(r.get("update_id"))
            new_ids.add(uid)
            name = r.get("pipeline_name") or f"Pipeline {r.get('pipeline_id')}"
            st = r.get("result_state", "FAILED")
            end = r.get("end_time", "unknown")
            dur = r.get("duration_minutes")
            dur_str = f" ({dur} min)" if dur is not None else ""
            cause = r.get("error_cause") or ""
            cause_str = f" | cause: {cause}" if cause else ""
            lines.append(
                f"**{name}** — {st}{dur_str} | ended {end} | update_id: {uid}{cause_str}"
            )

        send_teams_alert(
            title=f"🚨 {len(new_rows)} DLT Pipeline Failure(s) Detected",
            body="\n\n".join(lines),
            color="FF0000",
        )
        state.add_alerted_ids(key, new_ids)
        logger.warning("Alerted on %d new pipeline failure(s)", len(new_rows))

    except Exception:
        logger.exception("check_pipeline_failures raised an unexpected error")


# ── Cluster failures ──────────────────────────────────────────────────────────

def check_cluster_failures() -> None:
    """Alert on clusters that terminated with an error (crash, OOM, startup failure)."""
    key = "cluster_failures"
    fallback = _env_int("ALERT_FAILURE_HOURS_BACK", 1) * 60
    since = state.get_last_checked(key, fallback_minutes=fallback)

    try:
        rows = get_cluster_failures_since(since)
        state.set_last_checked(key)

        if not rows:
            logger.info("check_cluster_failures: no new failures since %s", since)
            return

        alerted = state.get_alerted_ids(key)
        new_rows = [r for r in rows if str(r.get("cluster_id")) not in alerted]
        if not new_rows:
            return

        lines, new_ids = [], set()
        for r in new_rows:
            cid = str(r.get("cluster_id"))
            new_ids.add(cid)
            name = r.get("cluster_name") or cid
            code = r.get("termination_code") or r.get("termination_type") or "UNKNOWN"
            terminated = r.get("terminated_time", "unknown")
            lines.append(
                f"**{name}** [{cid}] — {code} | terminated {terminated}"
            )

        send_teams_alert(
            title=f"🔴 {len(new_rows)} Cluster Failure(s) Detected",
            body="\n\n".join(lines),
            color="8B0000",
        )
        state.add_alerted_ids(key, new_ids)
        logger.warning("Alerted on %d new cluster failure(s)", len(new_rows))

    except Exception:
        logger.exception("check_cluster_failures raised an unexpected error")


# ── Long-running jobs ─────────────────────────────────────────────────────────

def check_long_running() -> None:
    """Alert on jobs running longer than the configured threshold."""
    key = "long_running"
    threshold = _env_int("ALERT_LONG_RUNNING_MINUTES", 120)

    try:
        import json
        result = get_long_running_jobs(threshold_minutes=threshold)
        if result.startswith("No jobs"):
            logger.info("check_long_running: no long-running jobs")
            return

        rows = json.loads(result)
        alerted = state.get_alerted_ids(key)
        new_rows = [r for r in rows if str(r.get("run_id")) not in alerted]
        if not new_rows:
            return

        lines, new_ids = [], set()
        for r in new_rows:
            new_ids.add(str(r.get("run_id")))
            job_name = r.get("job_name") or f"Job {r.get('job_id')}"
            mins = r.get("running_minutes", "?")
            start = r.get("start_time", "unknown")
            lines.append(
                f"**{job_name}** — running for **{mins} minutes** | started {start} | run_id: {r.get('run_id')}"
            )

        send_teams_alert(
            title=f"⏱️ {len(new_rows)} Long-Running Job(s) Detected",
            body="\n\n".join(lines),
            color="FFA500",
        )
        state.add_alerted_ids(key, new_ids)
        logger.warning("Alerted on %d long-running job(s)", len(new_rows))

    except Exception:
        logger.exception("check_long_running raised an unexpected error")


# ── Daily digest ──────────────────────────────────────────────────────────────

def daily_digest() -> None:
    """Send a daily summary of all job run outcomes over the past 24 hours."""
    try:
        import json
        result = get_job_run_summary(days_back=1)
        if result.startswith("No job"):
            send_teams_alert(
                title="📊 Daily Databricks Job Digest",
                body="No job runs recorded in the last 24 hours.",
                color="0078D4",
            )
            return

        rows = json.loads(result)
        lines = []
        for r in rows:
            job = r.get("job_name") or "Unknown Job"
            st = r.get("result_state", "?")
            count = r.get("run_count", 0)
            avg = r.get("avg_duration_minutes")
            avg_str = f" | avg {avg:.1f} min" if avg is not None else ""
            emoji = "✅" if st == "SUCCEEDED" else "❌" if st in ("FAILED", "TIMEDOUT", "INTERNAL_ERROR") else "ℹ️"
            lines.append(f"{emoji} **{job}** [{st}] — {count} run(s){avg_str}")

        send_teams_alert(
            title="📊 Daily Databricks Job Digest",
            body="\n\n".join(lines),
            color="0078D4",
        )
        logger.info("Daily digest sent (%d entries)", len(rows))

    except Exception:
        logger.exception("daily_digest raised an unexpected error")
