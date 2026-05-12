import os
from databricks import sql


def get_connection():
    host = os.environ["DATABRICKS_HOST"].replace("https://", "").replace("http://", "")
    # DATABRICKS_TOKEN is auto-injected by Databricks Apps; on local dev it must
    # be set manually in .env.  The SQL connector accepts None and will fall back
    # to the default credential chain (useful on Databricks Apps with OAuth).
    token = os.environ.get("DATABRICKS_TOKEN")
    return sql.connect(
        server_hostname=host,
        http_path=os.environ["DATABRICKS_HTTP_PATH"],
        access_token=token,
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
