import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

load_dotenv()

from .agent import ask_agent  # noqa: E402 — must load env before importing agent


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str


def _build_bot_adapter():
    """Lazily build the Teams bot adapter so missing env vars don't break /ask-only deployments."""
    from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings
    from botbuilder.schema import Activity
    from teams_bot.bot import MonitoringBot

    settings = BotFrameworkAdapterSettings(
        app_id=os.environ.get("MicrosoftAppId", ""),
        app_password=os.environ.get("MicrosoftAppPassword", ""),
    )
    adapter = BotFrameworkAdapter(settings)
    bot = MonitoringBot()
    return adapter, bot, Activity


_teams_adapter = None
_teams_bot = None
_Activity = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _teams_adapter, _teams_bot, _Activity

    required = [
        "DATABRICKS_HOST",
        "DATABRICKS_HTTP_PATH",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {missing}")

    if os.environ.get("MicrosoftAppId"):
        try:
            _teams_adapter, _teams_bot, _Activity = _build_bot_adapter()
        except ModuleNotFoundError:
            import warnings
            warnings.warn(
                "botbuilder packages not installed — Teams integration disabled. "
                "Run: pip install botbuilder-core botbuilder-integration-aiohttp",
                stacklevel=2,
            )

    # Start background scheduler if a Teams webhook is configured
    if os.environ.get("TEAMS_WEBHOOK_URL"):
        from .scheduler import start_scheduler
        start_scheduler()

    yield

    from .scheduler import stop_scheduler
    stop_scheduler()


app = FastAPI(
    title="Databricks Job Monitoring Agent",
    description="AI-powered natural language interface for Databricks job and cluster monitoring.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest):
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    try:
        answer = ask_agent(request.question)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return AskResponse(answer=answer)


@app.post("/api/messages")
async def messages(req: Request):
    """Webhook endpoint consumed by Azure Bot Service for Teams messages."""
    if _teams_adapter is None:
        raise HTTPException(status_code=503, detail="Teams integration not configured.")

    body = await req.json()
    activity = _Activity.deserialize(body)
    auth_header = req.headers.get("Authorization", "")

    response = Response()

    async def call_bot(turn_context):
        await _teams_bot.on_turn(turn_context)

    await _teams_adapter.process_activity(activity, auth_header, call_bot)
    return response
