"""Deterministic graders for agent eval cases. Each returns (passed, reason)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

CITATION = re.compile(r"\[runbook:([a-z0-9-]+)\]")


@dataclass
class Case:
    id: str
    question: str
    expected_runbooks: list[str] = field(default_factory=list)
    expect_tools: list[str] = field(default_factory=list)
    must_include: list[str] = field(default_factory=list)
    must_not_include: list[str] = field(default_factory=list)
    expect_no_citations: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Case:
        return cls(**d)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


def grade(case: Case, answer: str, tools_used: list[str], stop_reason: str) -> list[Check]:
    checks = [Check("completed", stop_reason == "end_turn", f"stop_reason={stop_reason}")]

    missing_tools = [t for t in case.expect_tools if t not in tools_used]
    checks.append(Check("tools", not missing_tools, f"missing={missing_tools}"))

    cited = set(CITATION.findall(answer))
    if case.expect_no_citations:
        checks.append(Check("no_citations", not cited, f"cited={sorted(cited)}"))
    elif case.expected_runbooks:
        missing = [r for r in case.expected_runbooks if r not in cited]
        checks.append(Check("citations", not missing, f"missing={missing}"))

    lower = answer.lower()
    absent = [s for s in case.must_include if s.lower() not in lower]
    checks.append(Check("must_include", not absent, f"absent={absent}"))
    present = [s for s in case.must_not_include if s.lower() in lower]
    checks.append(Check("must_not_include", not present, f"present={present}"))
    return checks
