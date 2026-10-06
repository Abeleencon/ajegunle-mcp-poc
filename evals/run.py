"""Evaluation gate.

  python -m evals.run --suite retrieval   # deterministic, no API key; runs on every PR
  python -m evals.run --suite agent       # end-to-end against Claude; needs credentials

Each suite compares its metrics with evals/thresholds.json and exits non-zero when any
metric misses, which fails the CI job and blocks the deploy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

from agent_service.agent.mcp_client import mcp_session
from agent_service.mcp_server.server import build_server
from agent_service.mcp_server.store import LocalStore
from evals.graders import Case, grade

HERE = Path(__file__).parent


def load_cases() -> list[Case]:
    lines = (HERE / "cases.jsonl").read_text().splitlines()
    return [Case.from_dict(json.loads(line)) for line in lines if line.strip()]


async def run_retrieval(cases: list[Case]) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Recall of the MCP search tool, called through the MCP protocol like the agent does."""
    scored = [c for c in cases if c.expected_runbooks]
    rows = []
    async with mcp_session(server=build_server(LocalStore())) as session:
        for case in scored:
            out = await session.call_tool("search_runbooks", {"query": case.question, "limit": 3})
            ids = [h["id"] for h in out.structuredContent["hits"]]
            rows.append(
                {
                    "id": case.id,
                    "expected": case.expected_runbooks,
                    "got": ids,
                    "hit_at_1": ids[:1] == case.expected_runbooks[:1],
                    "hit_at_3": all(r in ids for r in case.expected_runbooks),
                }
            )
    n = len(rows) or 1
    metrics = {
        "recall_at_1": sum(r["hit_at_1"] for r in rows) / n,
        "recall_at_3": sum(r["hit_at_3"] for r in rows) / n,
    }
    return metrics, rows


async def run_agent(cases: list[Case], concurrency: int) -> tuple[dict[str, float], list[dict]]:
    from agent_service.agent.loop import Agent
    from agent_service.config import get_settings
    from agent_service.secrets import build_client
    from agent_service.telemetry import setup_tracing

    settings = get_settings()
    setup_tracing(settings)
    agent = Agent(build_client(settings), settings, mcp_server=build_server(LocalStore()))
    sem = asyncio.Semaphore(concurrency)

    async def one(case: Case) -> dict[str, Any]:
        async with sem:
            started = time.perf_counter()
            try:
                res = await agent.run(case.question)
            except Exception as exc:
                return {
                    "id": case.id,
                    "passed": False,
                    "error": repr(exc),
                    "latency_s": 0.0,
                    "steps": 0,
                    "tokens": 0,
                    "checks": [],
                }
            latency = time.perf_counter() - started
            tools = [c.name for c in res.tool_calls]
            checks = grade(case, res.answer, tools, res.stop_reason)
            return {
                "id": case.id,
                "passed": all(c.passed for c in checks),
                "checks": [c.__dict__ for c in checks],
                "tools": tools,
                "steps": res.steps,
                "tokens": res.input_tokens + res.output_tokens,
                "latency_s": round(latency, 2),
                "trace_id": res.trace_id,
                "answer": res.answer,
            }

    rows = await asyncio.gather(*(one(c) for c in cases))
    n = len(rows) or 1
    latencies = sorted(r["latency_s"] for r in rows)
    p95 = latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)] if latencies else 0.0
    metrics = {
        "pass_rate": sum(r["passed"] for r in rows) / n,
        "avg_steps": sum(r["steps"] for r in rows) / n,
        "p95_latency_s": p95,
        "avg_tokens": sum(r["tokens"] for r in rows) / n,
    }
    return metrics, list(rows)


def check_thresholds(suite: str, metrics: dict[str, float]) -> list[str]:
    limits = json.loads((HERE / "thresholds.json").read_text())[suite]
    failures = []
    for key, rule in limits.items():
        metric, bound = key.removeprefix("min_").removeprefix("max_"), rule
        value = metrics[metric]
        if key.startswith("min_") and value < bound:
            failures.append(f"{metric}={value:.3f} < {bound}")
        if key.startswith("max_") and value > bound:
            failures.append(f"{metric}={value:.3f} > {bound}")
    return failures


def write_summary(suite: str, metrics: dict[str, float], rows: list[dict], failures: list[str]):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"## Eval gate: {suite} {'FAILED' if failures else 'passed'}", ""]
    lines += [f"- **{k}**: {v:.3f}" for k, v in metrics.items()]
    lines += [f"- :x: {f}" for f in failures]
    failed = [r["id"] for r in rows if not (r.get("passed") or r.get("hit_at_1"))]
    if failed:
        lines += ["", "Failing cases: " + ", ".join(failed)]
    with open(path, "a") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=["retrieval", "agent"], required=True)
    parser.add_argument("--out", type=Path, help="write a JSON report here")
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args(argv)

    cases = load_cases()
    if args.suite == "retrieval":
        metrics, rows = asyncio.run(run_retrieval(cases))
    else:
        metrics, rows = asyncio.run(run_agent(cases, args.concurrency))

    failures = check_thresholds(args.suite, metrics)
    report = {"suite": args.suite, "metrics": metrics, "failures": failures, "cases": rows}
    if args.out:
        args.out.write_text(json.dumps(report, indent=2))
    write_summary(args.suite, metrics, rows, failures)

    print(json.dumps({"suite": args.suite, "metrics": metrics, "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
