"""
Azure Bot Framework handler for Microsoft Teams integration.

Receives incoming Teams messages, forwards them to the FastAPI /ask endpoint,
and returns the agent's answer back to the Teams conversation.

Deploy alongside the FastAPI app (same process) or as a separate service.
Register the bot messaging endpoint as: https://your-app.databricksapps.com/api/messages
"""
import os

import httpx
from botbuilder.core import ActivityHandler, TurnContext
from botbuilder.schema import Activity


class MonitoringBot(ActivityHandler):
    def __init__(self):
        self._api_base = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")

    async def on_message_activity(self, turn_context: TurnContext):
        question = turn_context.activity.text or ""
        question = question.strip()

        if not question:
            await turn_context.send_activity("Please ask a question about your Databricks jobs or clusters.")
            return

        await turn_context.send_activity("Analysing your Databricks environment — one moment…")

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self._api_base}/ask",
                    json={"question": question},
                )
                resp.raise_for_status()
                answer = resp.json().get("answer", "No answer returned.")
        except httpx.HTTPStatusError as exc:
            answer = f"The monitoring service returned an error ({exc.response.status_code}). Please try again."
        except Exception as exc:
            answer = f"Failed to reach the monitoring service: {exc}"

        await turn_context.send_activity(Activity(type="message", text=answer))
