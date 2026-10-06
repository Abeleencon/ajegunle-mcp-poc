"""Runbook storage backends for the MCP server.

Locally the runbooks are the Markdown files bundled with the package. In EKS they live in
an S3 bucket the pod can only read (see infra/terraform/irsa.tf).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be by do for from how i if in is it my of on or the to what when "
    "why with".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


@dataclass(frozen=True)
class Runbook:
    id: str
    title: str
    body: str

    @classmethod
    def parse(cls, runbook_id: str, text: str) -> Runbook:
        first = text.strip().splitlines()[0] if text.strip() else runbook_id
        return cls(id=runbook_id, title=first.lstrip("# ").strip(), body=text)


class RunbookStore(Protocol):
    def list_ids(self) -> list[str]: ...
    def get(self, runbook_id: str) -> Runbook | None: ...


_VALID_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def is_valid_id(runbook_id: str) -> bool:
    """Ids are plain slugs, so a tool argument can never become a path or key traversal."""
    return bool(_VALID_ID.match(runbook_id))


class LocalStore:
    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or Path(str(resources.files("agent_service") / "knowledge"))

    def list_ids(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.md"))

    def get(self, runbook_id: str) -> Runbook | None:
        if not is_valid_id(runbook_id):
            return None
        path = self._dir / f"{runbook_id}.md"
        if not path.is_file():
            return None
        return Runbook.parse(runbook_id, path.read_text(encoding="utf-8"))


class S3Store:
    """Reads runbooks from s3://bucket/prefix/<id>.md. Needs only s3:ListBucket on the
    prefix and s3:GetObject on prefix/* (plus kms:Decrypt via S3)."""

    def __init__(self, bucket: str, prefix: str, s3_client: Any) -> None:
        self._bucket = bucket
        self._prefix = prefix if prefix.endswith("/") else prefix + "/"
        self._s3 = s3_client

    def list_ids(self) -> list[str]:
        ids: list[str] = []
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=self._prefix):
            for obj in page.get("Contents", []):
                name = obj["Key"][len(self._prefix) :]
                if name.endswith(".md") and "/" not in name:
                    ids.append(name[:-3])
        return sorted(ids)

    def get(self, runbook_id: str) -> Runbook | None:
        if not is_valid_id(runbook_id):
            return None
        try:
            obj = self._s3.get_object(Bucket=self._bucket, Key=f"{self._prefix}{runbook_id}.md")
        except self._s3.exceptions.NoSuchKey:
            return None
        return Runbook.parse(runbook_id, obj["Body"].read().decode("utf-8"))


def search(store: RunbookStore, query: str, limit: int = 3) -> list[tuple[Runbook, float]]:
    """Small BM25 ranking over title and body. Deterministic, so it can be evaluated offline."""
    docs = [rb for rb in (store.get(i) for i in store.list_ids()) if rb is not None]
    if not docs:
        return []
    q_terms = set(tokenize(query))
    tokenized = [tokenize(rb.title + " " + rb.title + " " + rb.body) for rb in docs]
    avg_len = sum(len(t) for t in tokenized) / len(tokenized)
    df = Counter(term for toks in tokenized for term in set(toks))
    n = len(docs)
    k1, b = 1.5, 0.75

    scored: list[tuple[Runbook, float]] = []
    for rb, toks in zip(docs, tokenized, strict=True):
        tf = Counter(toks)
        score = 0.0
        for term in q_terms:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            denom = tf[term] + k1 * (1 - b + b * len(toks) / avg_len)
            score += idf * tf[term] * (k1 + 1) / denom
        if score > 0:
            scored.append((rb, round(score, 4)))
    scored.sort(key=lambda pair: (-pair[1], pair[0].id))
    return scored[:limit]
