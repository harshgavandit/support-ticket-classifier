"""
FastAPI entry point for the AI-Powered Support Ticket Classifier.
"""

import os
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from graph import run_pipeline
from production_modules.prompt_versioning import list_versions, get_active_version
from production_modules.cost_calculator import session_tracker
from production_modules.llm_config import get_default_model, get_provider
from production_modules.rate_limit import RedisRateLimiter, RateLimitUnavailable

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)
rate_limiter = RedisRateLimiter.from_env()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class ClassifyRequest(BaseModel):
    ticket_text: str = Field(..., min_length=5, max_length=4000)
    channel: str = Field(default="web_form", pattern="^(web_form|email)$")


class ClassifyResponse(BaseModel):
    issue_category: str
    assigned_team: str
    priority: str
    user_sentiment: str
    confidence_score: float
    reasoning: str
    requires_human_review: bool
    pii_detected: bool
    prompt_version: str | None
    cost_info: dict | None
    injection_blocked: bool


# ---------------------------------------------------------------------------
# Startup banner
# ---------------------------------------------------------------------------
def print_banner():
    model = get_default_model()
    prompt_version = get_active_version()
    pii_enabled = True   # always on in this build
    cost_tracking = os.getenv("LOG_COSTS", "true").lower() == "true"

    banner = f"""
╔══════════════════════════════════════════════════════════╗
║       AI-Powered Support Ticket Classifier               ║
╠══════════════════════════════════════════════════════════╣
║  Model          : {model:<38} ║
║  Prompt Version : {prompt_version:<38} ║
║  PII Redaction  : {'enabled' if pii_enabled else 'disabled':<38} ║
║  Cost Tracking  : {'enabled' if cost_tracking else 'disabled':<38} ║
╚══════════════════════════════════════════════════════════╝
"""
    encoding = sys.stdout.encoding or "utf-8"
    print(banner.encode(encoding, errors="replace").decode(encoding))


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    print_banner()
    try:
        yield
    finally:
        rate_limiter.close()
    summary = session_tracker.summary
    logger.info("Session ended. Total cost: $%.6f over %d calls", summary["total_cost_usd"], summary["calls"])


app = FastAPI(
    title="Support Ticket Classifier",
    description="LangGraph + FastAPI ticket classification pipeline",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "Retry-After"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "provider": get_provider(), "model": get_default_model(),
            "rate_limit": {"enabled": rate_limiter.enabled, "requests": rate_limiter.limit,
                           "window_seconds": rate_limiter.window_seconds, "backend": "upstash_redis"}}


@app.get("/ready")
def readiness():
    try:
        if not rate_limiter.ping():
            raise RateLimitUnavailable()
    except RateLimitUnavailable:
        raise HTTPException(status_code=503, detail="Rate limit service unavailable")
    return {"status": "ready", "redis": "ok" if rate_limiter.enabled else "disabled"}


@app.get("/", include_in_schema=False)
async def demo():
    return FileResponse(Path(__file__).resolve().parent / "demo_ui" / "index.html")


@app.get("/prompts")
async def get_prompts():
    return {"versions": list_versions(), "active": get_active_version()}


def enforce_rate_limit(request: Request, response: Response):
    # Identity comes from the transport, never an arbitrary forwarding header.
    identity = request.client.host if request.client else "unknown"
    try:
        decision = rate_limiter.check(identity)
    except RateLimitUnavailable:
        logger.warning("Redis rate limit service unavailable; classification denied")
        raise HTTPException(status_code=503, detail="Rate limit service unavailable. Please try again shortly.", headers={"Retry-After": "5"})
    if decision is None:
        return
    if not decision.allowed:
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded. Try again in {decision.retry_after} seconds.",
                            headers={**decision.headers, "Retry-After": str(decision.retry_after)})
    response.headers.update(decision.headers)


@app.post("/classify", response_model=ClassifyResponse, dependencies=[Depends(enforce_rate_limit)],
          responses={429: {"description": "Client request limit exceeded"},
                     503: {"description": "Rate limit service or AI provider unavailable"}})
def classify(request: ClassifyRequest):
    try:
        state = run_pipeline(request.ticket_text, request.channel)
    except Exception as exc:
        logger.exception("Pipeline error: %s", exc)
        raise HTTPException(status_code=500, detail="Classification could not be completed")

    classification = state.get("classification")
    if state.get("error") == "Injection detected: guard_error":
        raise HTTPException(status_code=503, detail="AI provider unavailable. Check the configured key, model access, and quota.")
    if classification is None:
        raise HTTPException(status_code=422, detail=state.get("error", "Classification failed"))

    return ClassifyResponse(
        issue_category=classification.issue_category.value,
        assigned_team=classification.assigned_team.value,
        priority=classification.priority.value,
        user_sentiment=classification.user_sentiment.value,
        confidence_score=classification.confidence_score,
        reasoning=classification.reasoning,
        requires_human_review=classification.requires_human_review,
        pii_detected=state.get("pii_detected", False),
        prompt_version=state.get("prompt_version"),
        cost_info=state.get("cost_info"),
        injection_blocked=state.get("injection_blocked", False),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True, proxy_headers=False)
