import io
import json

import pytest

from agent_service.agent.mcp_client import mcp_session
from agent_service.mcp_server import calculator
from agent_service.mcp_server.store import LocalStore, S3Store, is_valid_id, search


@pytest.mark.parametrize(
    ("expr", "expected"),
    [("1 + 2 * 3", 7), ("(3 * 70) / 100", 2.1), ("-4 ** 2", -16), ("7 // 2", 3)],
)
def test_calculator_evaluates_arithmetic(expr, expected):
    assert calculator.evaluate(expr) == pytest.approx(expected)


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('id')",
        "a + 1",
        "[1, 2]",
        "2 ** 1000",
        "1 / 0",
        "True + 1",
        "x" * 300,
    ],
)
def test_calculator_rejects_anything_else(expr):
    with pytest.raises(calculator.CalculatorError):
        calculator.evaluate(expr)


def test_runbook_ids_cannot_traverse():
    assert is_valid_id("high-latency")
    for bad in ["../etc/passwd", "a/b", "", "UPPER", "x" * 70]:
        assert not is_valid_id(bad)
    assert LocalStore().get("../pyproject") is None


def test_search_ranks_the_relevant_runbook_first():
    store = LocalStore()
    assert search(store, "pods stuck in CrashLoopBackOff")[0][0].id == "crashloop"
    assert search(store, "how do I rotate the api key")[0][0].id == "rotate-api-key"
    assert search(store, "zzz qqq") == []


class FakeS3:
    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self, objects: dict[str, str]) -> None:
        self.objects = objects

    def get_paginator(self, _name):
        objects = self.objects

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k} for k in objects if k.startswith(Prefix)]}

        return P()

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey()
        return {"Body": io.BytesIO(self.objects[Key].encode())}


def test_s3_store_reads_only_its_prefix():
    s3 = FakeS3(
        {
            "runbooks/a.md": "# Alpha\nbody",
            "runbooks/nested/b.md": "# nested",
            "other/c.md": "# other",
        }
    )
    store = S3Store("bucket", "runbooks", s3)
    assert store.list_ids() == ["a"]
    assert store.get("a").title == "Alpha"
    assert store.get("missing") is None


async def test_mcp_server_exposes_tools(mcp_server):
    async with mcp_session(server=mcp_server) as session:
        names = {t.name for t in (await session.list_tools()).tools}
        assert names == {"search_runbooks", "get_runbook", "calculate"}

        found = await session.call_tool("search_runbooks", {"query": "slow p99 latency"})
        assert found.structuredContent["hits"][0]["id"] == "high-latency"

        body = await session.call_tool("get_runbook", {"runbook_id": "crashloop"})
        assert "CrashLoopBackOff" in body.content[0].text

        missing = await session.call_tool("get_runbook", {"runbook_id": "nope"})
        assert missing.isError

        calc = await session.call_tool("calculate", {"expression": "6 * 7"})
        assert json.loads(calc.content[0].text) == 42
