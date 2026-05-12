"""
Tool implementations that query Databricks system tables.

System tables used:
  system.lakeflow.job_run_timeline  - job run history and status
  system.lakeflow.jobs              - job definitions and names
  system.billing.usage              - DBU/billing usage per workspace
  system.compute.node_timeline      - cluster node usage over time

Adjust catalog/schema names if your workspace uses a different Unity Catalog setup.
"""
import json
from .databricks_client import run_query


def get_failed_jobs(hours_back: int = 24) -> str:
    query = f"""
    SELECT
        r.job_id,
        r.run_id,
        j.name                                              AS job_name,
        r.result_state,
        r.period_start_time                                 AS start_time,
        r.period_end_time                                   AS end_time,
        DATEDIFF(minute, r.period_start_time, r.period_end_time) AS duration_minutes
    FROM system.lakeflow.job_run_timeline r
    LEFT JOIN system.lakeflow.jobs j
        ON r.job_id = j.job_id AND r.workspace_id = j.workspace_id
    WHERE r.result_state IN ('FAILED', 'TIMEDOUT', 'INTERNAL_ERROR')
      AND r.period_start_time >= DATEADD(HOUR, -{hours_back}, CURRENT_TIMESTAMP())
    ORDER BY r.period_start_time DESC
    LIMIT 50
    """
    rows = run_query(query)
    if not rows:
        return f"No failed jobs found in the last {hours_back} hours."
    return json.dumps(rows, default=str)


def get_cluster_usage(days_back: int = 7) -> str:
    query = f"""
    SELECT
        usage_metadata.cluster_id   AS cluster_id,
        sku_name,
        SUM(usage_quantity)         AS total_dbu,
        usage_date
    FROM system.billing.usage
    WHERE usage_date >= DATEADD(DAY, -{days_back}, CURRENT_DATE())
      AND billing_origin_product IN ('JOBS', 'INTERACTIVE')
    GROUP BY
        usage_metadata.cluster_id,
        sku_name,
        usage_date
    ORDER BY total_dbu DESC
    LIMIT 50
    """
    rows = run_query(query)
    if not rows:
        return f"No cluster usage data found in the last {days_back} days."
    return json.dumps(rows, default=str)


def get_long_running_jobs(threshold_minutes: int = 60) -> str:
    query = f"""
    SELECT
        r.job_id,
        r.run_id,
        j.name                                                        AS job_name,
        r.period_start_time                                           AS start_time,
        DATEDIFF(minute, r.period_start_time, CURRENT_TIMESTAMP())   AS running_minutes
    FROM system.lakeflow.job_run_timeline r
    LEFT JOIN system.lakeflow.jobs j
        ON r.job_id = j.job_id AND r.workspace_id = j.workspace_id
    WHERE r.result_state IS NULL
      AND DATEDIFF(minute, r.period_start_time, CURRENT_TIMESTAMP()) > {threshold_minutes}
    ORDER BY running_minutes DESC
    LIMIT 20
    """
    rows = run_query(query)
    if not rows:
        return f"No jobs running longer than {threshold_minutes} minutes."
    return json.dumps(rows, default=str)


def get_job_run_summary(days_back: int = 7) -> str:
    query = f"""
    SELECT
        j.name                          AS job_name,
        r.result_state,
        COUNT(*)                        AS run_count,
        AVG(DATEDIFF(minute, r.period_start_time, r.period_end_time)) AS avg_duration_minutes
    FROM system.lakeflow.job_run_timeline r
    LEFT JOIN system.lakeflow.jobs j
        ON r.job_id = j.job_id AND r.workspace_id = j.workspace_id
    WHERE r.period_start_time >= DATEADD(DAY, -{days_back}, CURRENT_TIMESTAMP())
      AND r.result_state IS NOT NULL
    GROUP BY j.name, r.result_state
    ORDER BY run_count DESC
    LIMIT 50
    """
    rows = run_query(query)
    if not rows:
        return f"No job run data found in the last {days_back} days."
    return json.dumps(rows, default=str)


def get_top_dbu_consumers(days_back: int = 30) -> str:
    query = f"""
    SELECT
        usage_metadata.job_id   AS job_id,
        sku_name,
        SUM(usage_quantity)     AS total_dbu,
        COUNT(DISTINCT usage_date) AS active_days
    FROM system.billing.usage
    WHERE usage_date >= DATEADD(DAY, -{days_back}, CURRENT_DATE())
      AND billing_origin_product = 'JOBS'
      AND usage_metadata.job_id IS NOT NULL
    GROUP BY usage_metadata.job_id, sku_name
    ORDER BY total_dbu DESC
    LIMIT 20
    """
    rows = run_query(query)
    if not rows:
        return f"No DBU consumption data found in the last {days_back} days."
    return json.dumps(rows, default=str)


# ── Scheduler tools (timestamp-based, return list[dict] directly) ─────────────
# These are used by the background alerting checks, not by the interactive agent.
# They filter on period_end_time >= since_iso so that runs which started before
# the window but just finished are never missed.

_pipeline_table_available: bool | None = None  # None = not yet probed
_cluster_table_available: bool | None = None


def get_job_failures_since(since_iso: str) -> list[dict]:
    query = f"""
    SELECT
        r.job_id,
        r.run_id,
        j.name                                                        AS job_name,
        r.result_state,
        r.period_start_time                                           AS start_time,
        r.period_end_time                                             AS end_time,
        DATEDIFF(minute, r.period_start_time, r.period_end_time)     AS duration_minutes
    FROM system.lakeflow.job_run_timeline r
    LEFT JOIN system.lakeflow.jobs j
        ON r.job_id = j.job_id AND r.workspace_id = j.workspace_id
    WHERE r.result_state IN ('FAILED', 'TIMEDOUT', 'INTERNAL_ERROR')
      AND r.period_end_time >= '{since_iso}'
    ORDER BY r.period_end_time DESC
    LIMIT 100
    """
    return run_query(query)


def get_pipeline_failures_since(since_iso: str) -> list[dict]:
    global _pipeline_table_available
    if _pipeline_table_available is False:
        return []
    query = f"""
    SELECT
        p.pipeline_id,
        p.update_id,
        p.pipeline_name,
        p.state                                                       AS result_state,
        p.start_time,
        p.end_time,
        DATEDIFF(minute, p.start_time, p.end_time)                   AS duration_minutes,
        p.cause                                                       AS error_cause
    FROM system.lakeflow.pipeline_run_timeline p
    WHERE p.state IN ('FAILED', 'INTERNAL_ERROR')
      AND p.end_time >= '{since_iso}'
    ORDER BY p.end_time DESC
    LIMIT 100
    """
    try:
        rows = run_query(query)
        _pipeline_table_available = True
        return rows
    except Exception as exc:
        err = str(exc)
        if any(k in err for k in ("TABLE_OR_VIEW_NOT_FOUND", "SCHEMA_NOT_FOUND", "NoSuchTableException")):
            _pipeline_table_available = False
            import logging
            logging.getLogger(__name__).warning(
                "system.lakeflow.pipeline_run_timeline not found — DLT pipeline checks disabled"
            )
            return []
        raise


def get_cluster_failures_since(since_iso: str) -> list[dict]:
    global _cluster_table_available
    if _cluster_table_available is False:
        return []
    query = f"""
    SELECT
        c.cluster_id,
        c.cluster_name,
        c.state,
        c.terminated_time,
        c.termination_reason.type                                     AS termination_type,
        c.termination_reason.code                                     AS termination_code
    FROM system.compute.clusters c
    WHERE c.state = 'TERMINATED'
      AND c.termination_reason.type IN (
          'SPARK_STARTUP_FAILURE', 'UNEXPECTED_LAUNCH_FAILURE',
          'DRIVER_OOM', 'SPARK_CRASH', 'CLUSTER_UNREACHABLE'
      )
      AND c.terminated_time >= '{since_iso}'
    ORDER BY c.terminated_time DESC
    LIMIT 50
    """
    try:
        rows = run_query(query)
        _cluster_table_available = True
        return rows
    except Exception as exc:
        err = str(exc)
        if any(k in err for k in ("TABLE_OR_VIEW_NOT_FOUND", "SCHEMA_NOT_FOUND", "AnalysisException")):
            _cluster_table_available = False
            import logging
            logging.getLogger(__name__).warning(
                "system.compute.clusters struct query failed — cluster checks disabled"
            )
            return []
        raise


def _to_openai_schema(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }


# Map tool names to functions so the agent can invoke them by name
TOOL_REGISTRY = {
    "get_failed_jobs": get_failed_jobs,
    "get_cluster_usage": get_cluster_usage,
    "get_long_running_jobs": get_long_running_jobs,
    "get_job_run_summary": get_job_run_summary,
    "get_top_dbu_consumers": get_top_dbu_consumers,
}

# Tool schemas — Anthropic format
TOOL_SCHEMAS = [
    {
        "name": "get_failed_jobs",
        "description": (
            "Query Databricks system tables for job runs that ended in FAILED, "
            "TIMEDOUT, or INTERNAL_ERROR within a recent time window."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours_back": {
                    "type": "integer",
                    "description": "How many hours back to look for failures. Default 24.",
                    "default": 24,
                }
            },
        },
    },
    {
        "name": "get_cluster_usage",
        "description": (
            "Return DBU consumption per cluster grouped by SKU and date "
            "from the Databricks billing system table."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_back": {
                    "type": "integer",
                    "description": "Number of days back to aggregate usage. Default 7.",
                    "default": 7,
                }
            },
        },
    },
    {
        "name": "get_long_running_jobs",
        "description": (
            "Find job runs that are currently active and have been running "
            "longer than the specified threshold, which may indicate stuck or "
            "runaway jobs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "threshold_minutes": {
                    "type": "integer",
                    "description": "Minimum run duration in minutes to flag. Default 60.",
                    "default": 60,
                }
            },
        },
    },
    {
        "name": "get_job_run_summary",
        "description": (
            "Summarise job runs by name and final state (SUCCEEDED, FAILED, etc.) "
            "with counts and average duration — useful for health dashboards."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_back": {
                    "type": "integer",
                    "description": "Number of days to include in the summary. Default 7.",
                    "default": 7,
                }
            },
        },
    },
    {
        "name": "get_top_dbu_consumers",
        "description": (
            "Identify the jobs consuming the most DBUs over a given period, "
            "ranked by total DBU usage — useful for cost optimisation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_back": {
                    "type": "integer",
                    "description": "Number of days to aggregate. Default 30.",
                    "default": 30,
                }
            },
        },
    },
]

# Tool schemas — OpenAI / Azure OpenAI format (derived from TOOL_SCHEMAS above)
TOOL_SCHEMAS_OPENAI = [_to_openai_schema(t) for t in TOOL_SCHEMAS]
