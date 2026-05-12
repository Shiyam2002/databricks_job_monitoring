"""
Provider dispatcher — selects the agent backend based on the AGENT_PROVIDER env var.

Supported values (case-insensitive):
  anthropic        → app/agent_anthropic.py  (default)
  openai           → app/agent_openai.py
  azure_openai     → app/agent_azure_openai.py
"""
import os


def ask_agent(question: str) -> str:
    provider = os.environ.get("AGENT_PROVIDER", "anthropic").lower()

    if provider == "anthropic":
        from .agent_anthropic import ask_agent as _ask
    elif provider == "openai":
        from .agent_openai import ask_agent as _ask
    elif provider == "azure_openai":
        from .agent_azure_openai import ask_agent as _ask
    else:
        raise ValueError(
            f"Unknown AGENT_PROVIDER '{provider}'. "
            "Valid values: anthropic, openai, azure_openai"
        )

    return _ask(question)
