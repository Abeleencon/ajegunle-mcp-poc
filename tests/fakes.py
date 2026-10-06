"""A scripted stand-in for anthropic.AsyncAnthropic, so the agent loop runs offline."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any


def text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def tool_use(name: str, args: dict[str, Any], call_id: str) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=args)


def response(
    content: list[SimpleNamespace], stop_reason: str, model: str = "claude-opus-5-5"
) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        model=model,
        usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0),
    )


Step = SimpleNamespace | Callable[[dict[str, Any]], SimpleNamespace]


class ScriptedClaude:
    """Returns the scripted responses in order. A step may be a callable that receives
    the request kwargs, so it can read earlier tool results before answering."""

    def __init__(self, steps: list[Step]) -> None:
        self._steps = list(steps)
        self.requests: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> SimpleNamespace:
        # Snapshot messages: the agent keeps appending to the same list.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        if not self._steps:
            raise AssertionError("ScriptedClaude ran out of scripted responses")
        step = self._steps.pop(0)
        return step(kwargs) if callable(step) else step


def last_tool_results(kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for b in kwargs["messages"][-1]["content"] if b.get("type") == "tool_result"]
