"""MCP tool server: runbook search and retrieval plus a safe calculator.

The same server object is used two ways:
- in-process over an in-memory transport (local dev, tests, offline evals), and
- as a sidecar container speaking streamable HTTP on 127.0.0.1 inside the pod (EKS).
"""

from __future__ import annotations

from functools import lru_cache

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from agent_service.config import Settings, get_settings
from agent_service.mcp_server import calculator
from agent_service.mcp_server.store import LocalStore, RunbookStore, S3Store, search


class RunbookHit(BaseModel):
    id: str
    title: str
    score: float


class SearchResult(BaseModel):
    hits: list[RunbookHit]


def store_from_settings(settings: Settings) -> RunbookStore:
    if settings.knowledge_bucket:
        import boto3

        s3 = boto3.client("s3", region_name=settings.aws_region)
        return S3Store(settings.knowledge_bucket, settings.knowledge_prefix, s3)
    return LocalStore()


def build_server(store: RunbookStore, *, host: str = "127.0.0.1", port: int = 8001) -> FastMCP:
    mcp = FastMCP(
        "ops-runbooks",
        instructions="Search and read the platform team's operational runbooks.",
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool()
    def search_runbooks(
        query: str = Field(description="Keywords describing the incident or task."),
        limit: int = Field(default=3, ge=1, le=10, description="Maximum results."),
    ) -> SearchResult:
        """Find runbooks relevant to an operational question. Returns ids ranked by relevance;
        call get_runbook with an id to read the steps."""
        hits = search(store, query, limit=limit)
        return SearchResult(
            hits=[RunbookHit(id=rb.id, title=rb.title, score=score) for rb, score in hits]
        )

    @mcp.tool()
    def get_runbook(
        runbook_id: str = Field(description="Runbook id returned by search_runbooks."),
    ) -> str:
        """Return the full Markdown text of one runbook."""
        runbook = store.get(runbook_id)
        if runbook is None:
            raise ValueError(f"No runbook with id {runbook_id!r}. Use search_runbooks first.")
        return runbook.body

    @mcp.tool()
    def calculate(
        expression: str = Field(description="Arithmetic only, e.g. '(3 * 70) / 100'."),
    ) -> float:
        """Evaluate an arithmetic expression exactly. Use for capacity or cost maths."""
        return calculator.evaluate(expression)

    return mcp


@lru_cache
def default_server() -> FastMCP:
    return build_server(store_from_settings(get_settings()))
