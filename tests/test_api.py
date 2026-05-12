"""
Basic API tests.  Run with: pytest tests/ -v

These tests mock the agent so they don't require live credentials.
"""
import os
import pytest
from unittest.mock import patch

os.environ.setdefault("DATABRICKS_HOST", "https://test.azuredatabricks.net")
os.environ.setdefault("DATABRICKS_TOKEN", "test-token")
os.environ.setdefault("DATABRICKS_HTTP_PATH", "/sql/1.0/warehouses/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ask_returns_answer(client):
    with patch("app.main.ask_agent", return_value="No failures in the last 24 hours."):
        resp = client.post("/ask", json={"question": "Are there any failed jobs?"})
    assert resp.status_code == 200
    assert resp.json()["answer"] == "No failures in the last 24 hours."


def test_ask_empty_question(client):
    resp = client.post("/ask", json={"question": "   "})
    assert resp.status_code == 400


def test_ask_agent_exception_returns_500(client):
    with patch("app.main.ask_agent", side_effect=RuntimeError("DB connection failed")):
        resp = client.post("/ask", json={"question": "Show me cluster usage."})
    assert resp.status_code == 500
    assert "DB connection failed" in resp.json()["detail"]


# ── Provider dispatcher tests ─────────────────────────────────────────────────

def test_dispatcher_anthropic(monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
    with patch("app.agent_anthropic.ask_agent", return_value="anthropic answer") as mock:
        from app.agent import ask_agent
        result = ask_agent("test question")
    assert result == "anthropic answer"


def test_dispatcher_openai(monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    with patch("app.agent_openai.ask_agent", return_value="openai answer"):
        from app.agent import ask_agent
        result = ask_agent("test question")
    assert result == "openai answer"


def test_dispatcher_azure_openai(monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "azure_openai")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-azure-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    with patch("app.agent_azure_openai.ask_agent", return_value="azure answer"):
        from app.agent import ask_agent
        result = ask_agent("test question")
    assert result == "azure answer"


def test_dispatcher_invalid_provider(monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "invalid")
    from app.agent import ask_agent
    with pytest.raises(ValueError, match="Unknown AGENT_PROVIDER"):
        ask_agent("test")
