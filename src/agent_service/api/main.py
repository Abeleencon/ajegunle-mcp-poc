"""FastAPI front door for the agent."""

from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated

import anthropic
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel, Field

from agent_service import __version__
from agent_service.agent.loop import Agent
from agent_service.agent.mcp_client import mcp_session
from agent_service.config import Settings, get_settings
from agent_service.telemetry import setup_tracing

log = logging.getLogger("agent_service")


class InvokeRequest(BaseModel):
    message: str = Field(min_length=1)


class ToolCallOut(BaseModel):
    name: str
    is_error: bool
    duration_ms: float


class InvokeResponse(BaseModel):
    answer: str
    stop_reason: str
    model: str
    steps: int
    tool_calls: list[ToolCallOut]
    input_tokens: int
    output_tokens: int
    trace_id: str | None


def _default_agent(settings: Settings) -> Agent:
    from agent_service.secrets import build_client

    return Agent(build_client(settings), settings)


def create_app(
    settings: Settings | None = None,
    agent_factory: Callable[[Settings], Agent] = _default_agent,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_tracing(settings)
        app.state.agent = agent_factory(settings)
        yield

    app = FastAPI(title="MCP agent", version=__version__, lifespan=lifespan)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz")

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
        # Log the type only; messages can carry request content. The trace has the rest.
        log.error("unhandled error: %s", type(exc).__name__)
        return JSONResponse({"detail": "internal error"}, status_code=500)

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = settings.service_api_token
        if not expected:
            return
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing token")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request) -> dict[str, object]:
        # Ready only when the MCP tool server answers; the sidecar may start after us.
        try:
            agent: Agent = request.app.state.agent
            async with mcp_session(url=settings.mcp_server_url, server=agent.mcp_server) as session:
                tools = await session.list_tools()
        except Exception as exc:
            log.warning("readiness check failed: %s", type(exc).__name__)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "mcp unavailable") from exc
        return {"status": "ready", "tools": sorted(t.name for t in tools.tools)}

    @app.post(
        "/v1/agent/invoke",
        response_model=InvokeResponse,
        dependencies=[Depends(require_token)],
    )
    async def invoke(body: InvokeRequest, request: Request) -> InvokeResponse:
        if len(body.message) > settings.max_input_chars:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "message too long")
        agent: Agent = request.app.state.agent
        try:
            result = await agent.run(body.message)
        except anthropic.RateLimitError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "model rate limited") from exc
        except anthropic.APIStatusError as exc:
            log.error("model API error status=%s", exc.status_code)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "model API error") from exc
        except anthropic.APIConnectionError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "model API unreachable") from exc
        return InvokeResponse(
            answer=result.answer,
            stop_reason=result.stop_reason,
            model=result.model,
            steps=result.steps,
            tool_calls=[
                ToolCallOut(name=c.name, is_error=c.is_error, duration_ms=c.duration_ms)
                for c in result.tool_calls
            ],
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            trace_id=result.trace_id,
        )

    return app


def app() -> FastAPI:
    """Factory for `uvicorn --factory agent_service.api.main:app`."""
    return create_app()
