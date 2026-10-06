"""The agent: a Claude tool-use loop whose tools come from an MCP server.

A manual loop (rather than the SDK's beta tool runner) is used so every model call and
tool call gets its own OpenTelemetry span, and so the step cap, tool allowlist and
timeouts are enforced in one visible place.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Protocol

from mcp import ClientSession
from mcp.server.fastmcp import FastMCP
from opentelemetry.trace import Status, StatusCode

from agent_service.agent.mcp_client import mcp_session
from agent_service.config import Settings
from agent_service.telemetry import current_trace_id, tracer

AGENT_NAME = "ops-agent"
# Server-side refusal fallback: if a safety classifier declines, the API re-runs the
# request on Anthropic's recommended fallback model inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOOL_RESULT_CHARS = 20_000

SYSTEM_PROMPT = """\
You are the on-call assistant for a platform engineering team.

Answer operational questions from the team's runbooks, which you reach through your tools.
Search the runbooks before answering an operational question, read the most relevant one,
and base your answer on it. Cite every runbook you relied on as [runbook:<id>].
If no runbook covers the question, say so plainly instead of guessing.
Use the calculate tool for any arithmetic.
Keep answers short: the concrete steps or the number the engineer needs."""


class ClaudeClient(Protocol):
    """The slice of anthropic.AsyncAnthropic the agent uses; fakes implement it in tests."""

    @property
    def beta(self) -> Any: ...


@dataclass
class ToolCallRecord:
    name: str
    input: dict[str, Any]
    is_error: bool
    duration_ms: float


@dataclass
class AgentResult:
    answer: str
    stop_reason: str
    steps: int
    model: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    trace_id: str | None = None


class Agent:
    def __init__(
        self,
        client: ClaudeClient,
        settings: Settings,
        *,
        mcp_server: FastMCP | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._mcp_server = mcp_server

    @property
    def mcp_server(self) -> FastMCP | None:
        return self._mcp_server

    async def run(self, user_message: str) -> AgentResult:
        s = self._settings
        with tracer().start_as_current_span(f"invoke_agent {AGENT_NAME}") as span:
            span.set_attribute("gen_ai.operation.name", "invoke_agent")
            span.set_attribute("gen_ai.agent.name", AGENT_NAME)
            span.set_attribute("gen_ai.request.model", s.anthropic_model)
            span.set_attribute("agent.input.chars", len(user_message))

            async with mcp_session(url=s.mcp_server_url, server=self._mcp_server) as session:
                result = await self._loop(session, user_message)

            result.trace_id = current_trace_id()
            span.set_attribute("agent.steps", result.steps)
            span.set_attribute("agent.stop_reason", result.stop_reason)
            span.set_attribute("agent.tool_calls", len(result.tool_calls))
            span.set_attribute("gen_ai.usage.input_tokens", result.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", result.output_tokens)
            if result.stop_reason in ("refusal", "max_steps"):
                span.set_status(Status(StatusCode.ERROR, result.stop_reason))
            return result

    async def _loop(self, session: ClientSession, user_message: str) -> AgentResult:
        s = self._settings
        listed = await session.list_tools()
        tools = [
            {"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
            for t in listed.tools
        ]
        allowed = {t["name"] for t in tools}
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        result = AgentResult(answer="", stop_reason="", steps=0, model=s.anthropic_model)

        for step in range(1, s.agent_max_steps + 1):
            result.steps = step
            response = await self._call_model(messages, tools)
            result.model = getattr(response, "model", s.anthropic_model)
            usage = getattr(response, "usage", None)
            if usage is not None:
                result.input_tokens += getattr(usage, "input_tokens", 0) or 0
                result.output_tokens += getattr(usage, "output_tokens", 0) or 0

            if response.stop_reason == "refusal":
                result.stop_reason = "refusal"
                result.answer = "I can't help with that request."
                return result

            # Append the full content (thinking blocks included) so the next turn is valid.
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "tool_use":
                calls = [b for b in response.content if b.type == "tool_use"]
                results = await asyncio.gather(
                    *(self._run_tool(session, allowed, call, result) for call in calls)
                )
                messages.append({"role": "user", "content": list(results)})
                continue
            if response.stop_reason == "pause_turn":
                continue

            result.stop_reason = response.stop_reason
            result.answer = "\n".join(
                b.text for b in response.content if b.type == "text" and b.text
            ).strip()
            return result

        result.stop_reason = "max_steps"
        result.answer = "I couldn't finish within the allowed number of steps."
        return result

    async def _call_model(self, messages: list[dict[str, Any]], tools: list[dict]) -> Any:
        s = self._settings
        with tracer().start_as_current_span(f"chat {s.anthropic_model}") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.provider.name", "anthropic")
            span.set_attribute("gen_ai.request.model", s.anthropic_model)
            span.set_attribute("gen_ai.request.max_tokens", s.anthropic_max_tokens)
            response = await self._client.beta.messages.create(
                model=s.anthropic_model,
                max_tokens=s.anthropic_max_tokens,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": s.anthropic_effort},
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
            span.set_attribute("gen_ai.response.model", getattr(response, "model", ""))
            span.set_attribute("gen_ai.response.finish_reasons", [str(response.stop_reason)])
            usage = getattr(response, "usage", None)
            if usage is not None:
                span.set_attribute("gen_ai.usage.input_tokens", usage.input_tokens or 0)
                span.set_attribute("gen_ai.usage.output_tokens", usage.output_tokens or 0)
                cached = getattr(usage, "cache_read_input_tokens", None) or 0
                span.set_attribute("gen_ai.usage.cache_read.input_tokens", cached)
            return response

    async def _run_tool(
        self, session: ClientSession, allowed: set[str], call: Any, result: AgentResult
    ) -> dict[str, Any]:
        s = self._settings
        name, args = call.name, dict(call.input or {})
        with tracer().start_as_current_span(f"execute_tool {name}") as span:
            span.set_attribute("gen_ai.operation.name", "execute_tool")
            span.set_attribute("gen_ai.tool.name", name)
            span.set_attribute("gen_ai.tool.call.id", call.id)
            started = time.perf_counter()
            is_error = True
            if name not in allowed:
                content = f"Error: unknown tool {name!r}."
            else:
                try:
                    out = await session.call_tool(
                        name,
                        args,
                        read_timeout_seconds=timedelta(seconds=s.agent_tool_timeout_seconds),
                    )
                    content = _render_tool_output(out)
                    is_error = bool(out.isError)
                except Exception as exc:  # surfaced to the model, not the caller
                    content = f"Error: tool call failed ({type(exc).__name__})."
            duration = (time.perf_counter() - started) * 1000
            span.set_attribute("agent.tool.is_error", is_error)
            if is_error:
                span.set_status(Status(StatusCode.ERROR))
            result.tool_calls.append(ToolCallRecord(name, args, is_error, round(duration, 1)))
            return {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": content[:MAX_TOOL_RESULT_CHARS],
                "is_error": is_error,
            }


def _render_tool_output(out: Any) -> str:
    if out.structuredContent is not None and not out.isError:
        return json.dumps(out.structuredContent)
    texts = [c.text for c in out.content if getattr(c, "type", "") == "text"]
    return "\n".join(texts) or "(no output)"
