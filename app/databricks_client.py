import os
from databricks import sql


def _resolve_token(host: str) -> str:
    """
    Return a valid Bearer token for the SQL connector.

    Priority:
    1. DATABRICKS_TOKEN  — personal access token (local dev / CI)
    2. DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET  — OAuth M2M,
       auto-injected by Databricks Apps runtime
    """
    pat = os.environ.get("DATABRICKS_TOKEN")
    if pat:
        return pat

    client_id = os.environ.get("DATABRICKS_CLIENT_ID")
    client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET")
    if client_id and client_secret:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient(
            host=f"https://{host}",
            client_id=client_id,
            client_secret=client_secret,
        )
        auth_headers = dict(w.config.authenticate())
        bearer = auth_headers.get("Authorization", "")
        return bearer.removeprefix("Bearer ").strip()

    raise RuntimeError(
        "No Databricks credentials found. "
        "Set DATABRICKS_TOKEN for local dev, or ensure DATABRICKS_CLIENT_ID "
        "and DATABRICKS_CLIENT_SECRET are present (Databricks Apps)."
    )


def get_connection():
    host = os.environ["DATABRICKS_HOST"].replace("https://", "").replace("http://", "")
    return sql.connect(
        server_hostname=host,
        http_path=os.environ["DATABRICKS_HTTP_PATH"],
        access_token=_resolve_token(host),
    )


def run_query(query: str) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)
            if cursor.description is None:
                return []
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]
