import json

from evals import run
from evals.graders import Case, grade


def test_grader_passes_a_good_answer():
    case = Case(
        id="x",
        question="q",
        expected_runbooks=["crashloop"],
        expect_tools=["search_runbooks"],
        must_include=["rollout undo"],
    )
    checks = grade(case, "Run `rollout undo`. [runbook:crashloop]", ["search_runbooks"], "end_turn")
    assert all(c.passed for c in checks), checks


def test_grader_catches_each_failure_mode():
    case = Case(
        id="x",
        question="q",
        expected_runbooks=["crashloop"],
        expect_tools=["get_runbook"],
        must_include=["rollout undo"],
        must_not_include=["delete"],
    )
    checks = {c.name: c.passed for c in grade(case, "delete it", [], "max_steps")}
    assert checks == {
        "completed": False,
        "tools": False,
        "citations": False,
        "must_include": False,
        "must_not_include": False,
    }


def test_out_of_scope_must_not_cite():
    case = Case(id="x", question="q", expect_no_citations=True)
    checks = {c.name: c.passed for c in grade(case, "See [runbook:crashloop]", [], "end_turn")}
    assert checks["no_citations"] is False


def test_cases_file_is_valid():
    cases = run.load_cases()
    assert len({c.id for c in cases}) == len(cases) >= 10


def test_retrieval_gate_passes(tmp_path):
    out = tmp_path / "report.json"
    assert run.main(["--suite", "retrieval", "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["failures"] == []


def test_threshold_check_reports_misses():
    failures = run.check_thresholds("agent", {"pass_rate": 0.5, "avg_steps": 9, "p95_latency_s": 1})
    assert failures == ["pass_rate=0.500 < 0.9", "avg_steps=9.000 > 5"]


class RetrievalEchoClaude:
    """Stateless fake that always searches, reads the top hit and pastes it back. It
    cannot do arithmetic or decline, so the agent suite should score it 8/10 and fail."""

    def __init__(self):
        from types import SimpleNamespace

        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        from tests.fakes import last_tool_results, response, text, tool_use

        msgs = kwargs["messages"]
        if len(msgs) == 1:
            return response(
                [tool_use("search_runbooks", {"query": msgs[0]["content"]}, "s")], "tool_use"
            )
        content = last_tool_results(kwargs)[0]["content"]
        if len(msgs) == 3:
            top = json.loads(content)["hits"][0]["id"]
            return response([tool_use("get_runbook", {"runbook_id": top}, "g")], "tool_use")
        first_call = msgs[3]["content"][0]  # the get_runbook tool_use block
        return response(
            [text(f"{content}\n[runbook:{first_call.input['runbook_id']}]")], "end_turn"
        )


def test_agent_gate_blocks_a_weak_agent(monkeypatch, tmp_path):
    import agent_service.secrets

    monkeypatch.setattr(agent_service.secrets, "build_client", lambda s: RetrievalEchoClaude())
    out = tmp_path / "agent.json"
    assert run.main(["--suite", "agent", "--out", str(out)]) == 1
    report = json.loads(out.read_text())
    assert report["metrics"]["pass_rate"] == 0.8
    failed = sorted(r["id"] for r in report["cases"] if not r["passed"])
    assert failed == ["calc-capacity", "out-of-scope"]
