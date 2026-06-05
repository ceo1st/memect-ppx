from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChapterProposal:
    """LLM 输出的 template.chapters（每条是一条规则的 dict）。"""
    chapters: list[dict[str, Any]]
    raw: Any = None


@dataclass
class ChapterIssue:
    kind: str
    advice: str
    detail: str = ""


@dataclass
class EvalResult:
    score: int
    issues: list[ChapterIssue] = field(default_factory=list)


@dataclass
class IterState:
    iteration: int = 0
    best_proposal: ChapterProposal | None = None
    best_score: int = -1
    last_eval: EvalResult | None = None
    no_improve_count: int = 0
