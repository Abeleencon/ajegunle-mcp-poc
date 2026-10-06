import httpx
import pytest
from fastapi.testclient import TestClient

from agent_service.agent.loop import Agent
from agent_service.api.main import create_app
from tests.fakes import ScriptedClaude, response, text


def make_client(settings, mcp_server, steps):
    claude = ScriptedClaude(steps)
    app = create_app(settings, agent_factory=lambda s: Agent(claude, s, mcp_server=mcp_server))
    return TestClient(app)


def test_health_and_readiness(settings, mcp_server):
    with make_client(settings, mcp_server, []) as c:
        assert c.get("/healthz").json() == {"status": "ok"}
        ready = c.get("/readyz").json()
        assert ready["tools"] == ["calculate", "get_runbook", "search_runbooks"]


def test_invoke_returns_answer_and_trace(settings, mcp_server):
    with make_client(settings, mcp_server, [response([text("hi")], "end_turn")]) as c:
        r = c.post("/v1/agent/invoke", json={"message": "hello"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "hi" and body["stop_reason"] == "end_turn"
    assert len(body["trace_id"]) == 32


def test_bearer_token_is_enforced_when_configured(settings, mcp_server):
    settings.service_api_token = "s3cret"
    with make_client(settings, mcp_server, [response([text("ok")], "end_turn")]) as c:
        assert c.post("/v1/agent/invoke", json={"message": "x"}).status_code == 401
        bad = {"Authorization": "Bearer nope"}
        assert c.post("/v1/agent/invoke", json={"message": "x"}, headers=bad).status_code == 401
        good = {"Authorization": "Bearer s3cret"}
        assert c.post("/v1/agent/invoke", json={"message": "x"}, headers=good).status_code == 200
        assert c.get("/healthz").status_code == 200  # probes stay open


def test_input_limits(settings, mcp_server):
    settings.max_input_chars = 10
    with make_client(settings, mcp_server, []) as c:
        assert c.post("/v1/agent/invoke", json={"message": ""}).status_code == 422
        assert c.post("/v1/agent/invoke", json={"message": "x" * 11}).status_code == 413


@pytest.mark.parametrize(("status", "expected"), [(429, 503), (500, 502), (400, 502)])
def test_model_errors_are_mapped(settings, mcp_server, status, expected):
    import anthropic

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(status, request=req, json={"error": {"message": "x"}})
    cls = {
        429: anthropic.RateLimitError,
        500: anthropic.InternalServerError,
        400: anthropic.BadRequestError,
    }[status]

    def boom(_kwargs):
        raise cls("x", response=resp, body=None)

    with make_client(settings, mcp_server, [boom]) as c:
        assert c.post("/v1/agent/invoke", json={"message": "x"}).status_code == expected


def test_unexpected_errors_return_json_without_details(settings, mcp_server):
    def boom(_kwargs):
        raise TypeError("Could not resolve authentication method")

    with make_client(settings, mcp_server, [boom]) as c:
        c_noraise = TestClient(c.app, raise_server_exceptions=False)
        r = c_noraise.post("/v1/agent/invoke", json={"message": "x"})
    assert r.status_code == 500
    assert r.json() == {"detail": "internal error"}
