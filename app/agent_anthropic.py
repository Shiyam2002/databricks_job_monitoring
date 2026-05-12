import os
import anthropic
from .tools import TOOL_REGISTRY, TOOL_SCHEMAS

_client: anthropic.Anthropic | None = None

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


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def ask_agent(question: str) -> str:
    client = _get_client()

    messages: list[dict] = [{"role": "user", "content": question}]

    system = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    while True:
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=system,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return "I was unable to produce a response. Please try again."

        if response.stop_reason != "tool_use":
            return f"Unexpected stop reason: {response.stop_reason}"

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_fn = TOOL_REGISTRY.get(block.name)
            if tool_fn is None:
                result = f"Error: unknown tool '{block.name}'"
            else:
                try:
                    result = tool_fn(**block.input)
                except Exception as exc:
                    result = f"Error executing {block.name}: {exc}"

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                }
            )

        messages.append({"role": "user", "content": tool_results})
