import json

from agent_service.agent.loop import FALLBACK_BETA, Agent
from tests.fakes import ScriptedClaude, last_tool_results, response, text, tool_use


async def test_agent_searches_reads_and_answers(settings, mcp_server, spans):
    def answer(kwargs):
        body = last_tool_results(kwargs)[0]["content"]
        assert "kubectl -n agent rollout undo" in body
        return response(
            [text("Roll back: `kubectl -n agent rollout undo`. [runbook:crashloop]")], "end_turn"
        )

    def read_top_hit(kwargs):
        hits = json.loads(last_tool_results(kwargs)[0]["content"])["hits"]
        return response([tool_use("get_runbook", {"runbook_id": hits[0]["id"]}, "t2")], "tool_use")

    claude = ScriptedClaude(
        [
            response([tool_use("search_runbooks", {"query": "crashloop pod"}, "t1")], "tool_use"),
            read_top_hit,
            answer,
        ]
    )
    result = await Agent(claude, settings, mcp_server=mcp_server).run("Pods are crashlooping")

    assert result.stop_reason == "end_turn"
    assert "[runbook:crashloop]" in result.answer
    assert [c.name for c in result.tool_calls] == ["search_runbooks", "get_runbook"]
    assert result.steps == 3
    assert result.input_tokens == 300 and result.output_tokens == 60
    assert result.trace_id and len(result.trace_id) == 32

    first = claude.requests[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["thinking"] == {"type": "adaptive"}
    assert first["output_config"] == {"effort": "medium"}
    assert first["fallbacks"] == "default" and first["betas"] == [FALLBACK_BETA]
    assert "tool_choice" not in first  # forced tool choice is rejected on Opus 5.5
    assert {t["name"] for t in first["tools"]} == {"search_runbooks", "get_runbook", "calculate"}

    names = [s.name for s in spans.get_finished_spans()]
    assert names.count("chat claude-opus-5-5") == 3
    assert "execute_tool search_runbooks" in names and "execute_tool get_runbook" in names
    root = next(s for s in spans.get_finished_spans() if s.name == "invoke_agent ops-agent")
    assert root.attributes["gen_ai.usage.input_tokens"] == 300
    assert all(s.context.trace_id == root.context.trace_id for s in spans.get_finished_spans())


async def test_unknown_tool_is_reported_to_the_model_not_executed(settings, mcp_server):
    def check(kwargs):
        res = last_tool_results(kwargs)[0]
        assert res["is_error"] and "unknown tool" in res["content"]
        return response([text("I can't do that.")], "end_turn")

    claude = ScriptedClaude([response([tool_use("delete_cluster", {}, "x")], "tool_use"), check])
    result = await Agent(claude, settings, mcp_server=mcp_server).run("delete prod")
    assert result.tool_calls[0].is_error


async def test_parallel_tool_calls_return_in_one_message(settings, mcp_server):
    def check(kwargs):
        results = last_tool_results(kwargs)
        assert [r["tool_use_id"] for r in results] == ["a", "b"]
        assert json.loads(results[1]["content"]) == {"result": 2.1}
        return response([text("done")], "end_turn")

    claude = ScriptedClaude(
        [
            response(
                [
                    tool_use("search_runbooks", {"query": "latency"}, "a"),
                    tool_use("calculate", {"expression": "3 * 70 / 100"}, "b"),
                ],
                "tool_use",
            ),
            check,
        ]
    )
    result = await Agent(claude, settings, mcp_server=mcp_server).run("q")
    assert result.answer == "done"


async def test_refusal_stops_the_loop(settings, mcp_server, spans):
    claude = ScriptedClaude([response([], "refusal")])
    result = await Agent(claude, settings, mcp_server=mcp_server).run("something disallowed")
    assert result.stop_reason == "refusal"
    assert result.tool_calls == []


async def test_step_cap_bounds_the_loop(settings, mcp_server):
    loop_forever = response([tool_use("calculate", {"expression": "1+1"}, "c")], "tool_use")
    claude = ScriptedClaude([loop_forever] * settings.agent_max_steps)
    result = await Agent(claude, settings, mcp_server=mcp_server).run("q")
    assert result.stop_reason == "max_steps"
    assert len(claude.requests) == settings.agent_max_steps
