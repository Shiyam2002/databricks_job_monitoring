import json
import os
from openai import OpenAI
from .tools import TOOL_REGISTRY, TOOL_SCHEMAS_OPENAI

_client: OpenAI | None = None

SYSTEM_PROMPT = """You are an intelligent Databricks job monitoring assistant for an enterprise data platform.
You have access to tools that query Databricks system tables to provide real-time insights about:
- Job failures and errors
- Cluster resource utilization and DBU consumption
- Long-running or stuck jobs
- Job run health summaries and trends
- Top DBU consumers for cost optimisation

When answering questions:
1. Use the appropriate tool(s) to fetch live data — never guess or fabricate numbers.
2. Summarise findings clearly: highlight critical issues first, then provide supporting detail.
3. For failures, always include job name, run ID, and time of failure.
4. For cost questions, express DBU figures with two decimal places.
5. If no issues are found, explicitly confirm the platform looks healthy.
6. Be concise — enterprise users want actionable insight, not verbose prose.
"""


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def ask_agent(question: str) -> str:
    client = _get_client()
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    while True:
        response = client.chat.completions.create(
            model=model,
            tools=TOOL_SCHEMAS_OPENAI,
            tool_choice="auto",
            messages=messages,
        )

        choice = response.choices[0]
        messages.append(choice.message)

        if choice.finish_reason == "stop":
            return choice.message.content or "I was unable to produce a response. Please try again."

        if choice.finish_reason != "tool_calls":
            return f"Unexpected finish reason: {choice.finish_reason}"

        for call in choice.message.tool_calls:
            tool_fn = TOOL_REGISTRY.get(call.function.name)
            if tool_fn is None:
                result = f"Error: unknown tool '{call.function.name}'"
            else:
                try:
                    args = json.loads(call.function.arguments)
                    result = tool_fn(**args)
                except Exception as exc:
                    result = f"Error executing {call.function.name}: {exc}"

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                }
            )
