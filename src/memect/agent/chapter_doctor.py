import base64
import json
import mimetypes
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


ChapterSource = Literal[
    "pdf_outline", "doc_tree", "doc_json", "markdown", "agent", "rule"
]
AgentProvider = Literal["auto", "openai-chat", "openai-responses", "anthropic"]


_CHAPTER_RE = re.compile(
    r"^\s*("
    r"第[一二三四五六七八九十百千万0-9]+[章节篇部].*|"
    r"[0-9]+[、.．\s]+[^\s].*|"
    r"[一二三四五六七八九十]+[、.．][^\s].*|"
    r"附件\s*[:：0-9一二三四五六七八九十]*.*|"
    r"附录\s*[:：0-9一二三四五六七八九十A-Za-z]*.*|"
    r"目录|摘要|前言|引言|结论|参考文献|致谢"
    r")\s*$"
)
_SUBSECTION_RE = re.compile(r"^\s*[0-9]+\.[0-9]+")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_ANGLE_HINT_LINE_RE = re.compile(
    r"^\s*(?P<title><[^>]+>)\s*"
    r"(?P<range>-?[0-9]+(?:\s*[-~至]\s*-?[0-9]+)?)?\s*$"
)
_HINT_LINE_RE = re.compile(
    r"^\s*(?P<title>[^\s]+?)"
    r"(?:\s+(?P<range>-?[0-9]+(?:\s*[-~至]\s*-?[0-9]+)?))?\s*$"
)


class ChapterEvidence(BaseModel):
    source: ChapterSource
    page: int | None = None
    text: str | None = None
    detail: Mapping[str, Any] = Field(default_factory=dict)


class ChapterCandidate(BaseModel):
    title: str
    page_start: int | None = None
    page_end: int | None = None
    level: int = 1
    source: ChapterSource
    confidence: float = 0
    evidence: list[ChapterEvidence] = Field(default_factory=list)


class ChapterHint(BaseModel):
    title: str
    page_start: int | None = None
    page_end: int | None = None
    raw: str


class PageImage(BaseModel):
    page: int
    file: Path
    selected_for_agent: bool = False


class ChapterAgentConfig(BaseModel):
    provider: AgentProvider = "auto"
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    timeout: float = 60
    temperature: float | None = 0
    max_completion_tokens: int | None = None
    max_output_tokens: int | None = None
    max_tokens: int | None = None
    model_reasoning_effort: str | None = None
    reasoning_effort: str | None = None
    anthropic_version: str = "2023-06-01"
    thinking: Mapping[str, Any] | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    client: Mapping[str, Any] | None = None
    params: Mapping[str, Any] | None = None
    vision: bool = False
    max_page_images: int = 6

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: Any) -> Any:
        aliases = {
            "": "auto",
            "openai": "openai-chat",
            "openai-compatible": "openai-chat",
            "chat": "openai-chat",
            "responses": "openai-responses",
            "openai-response": "openai-responses",
            "response": "openai-responses",
        }
        if isinstance(value, str):
            return aliases.get(value.strip(), value)
        if value is None:
            return "auto"
        return value


class ChapterAgentResult(BaseModel):
    ok: bool = False
    provider: str | None = None
    model: str | None = None
    vision_used: bool = False
    content: str | None = None
    parsed_json: Any | None = None
    error: str | None = None


class ChapterDoctorArgs(BaseModel):
    problem: str
    file: Path
    out_dirs: list[Path] = Field(default_factory=list)
    out_dir: Path | None = None
    pages: str | None = None
    tree: str | None = None
    params_snapshot: Mapping[str, Any] = Field(default_factory=dict)
    command: list[str] = Field(default_factory=list)
    agent_config: Path = Path("./agent.json")


class ChapterDoctorReport(BaseModel):
    problem: str
    out_dir: Path
    agent_dir: Path
    candidates: list[ChapterCandidate] = Field(default_factory=list)
    page_images: list[PageImage] = Field(default_factory=list)
    params: Mapping[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    agent: ChapterAgentResult | None = None
    report_files: dict[str, Path] = Field(default_factory=dict)


@dataclass
class ChapterAgentProposal:
    candidates: list[ChapterCandidate]
    params: Mapping[str, Any] | None
    images: list[PageImage]
    agent: ChapterAgentResult
    note: str | None = None


@dataclass
class ChapterTUIResult:
    candidates: list[ChapterCandidate]
    params: Mapping[str, Any]
    images: list[PageImage]
    note: str | None = None
    agent: ChapterAgentResult | None = None


class ChapterDoctor:
    def __init__(self, args: ChapterDoctorArgs | Mapping[str, Any]):
        self.args = ChapterDoctorArgs.model_validate(args)
        self.out_dir = self._effective_out_dir()
        self.agent_dir = self.out_dir / "agent"
        self._doc_json: Any | None = None
        self._markdown: str = ""
        self._prediagnosis: Mapping[str, Any] = {}
        self._hints: list[ChapterHint] = []

    def run(self) -> ChapterDoctorReport:
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        self._load_inputs()
        images = self._page_images()
        candidates = self._collect_candidates()
        candidates = self._dedupe_candidates(candidates)
        candidates = self._fill_ranges(candidates)
        notes: list[str] = []

        agent_config = self._load_agent_config()
        agent_result: ChapterAgentResult | None = None
        params = self._build_params(candidates)
        tui = ChapterDoctorTUI(
            candidates=candidates,
            params=params,
            out_dir=self.out_dir,
            agent_dir=self.agent_dir,
            command=self.args.command,
            images=images,
            agent_config=agent_config,
            propose_agent=self._propose_with_agent,
        )
        if tui.enabled():
            tui_result = tui.run()
            candidates = tui_result.candidates
            params = tui_result.params
            images = tui_result.images
            agent_result = tui_result.agent
            if tui_result.note:
                notes.append(tui_result.note)
        else:
            proposal = self._propose_with_agent(
                agent_config,
                candidates,
                images,
                mode="ai",
                template_text=None,
            )
            if proposal is not None:
                images = proposal.images
                agent_result = proposal.agent
                if proposal.agent.ok:
                    candidates = proposal.candidates
                    params = proposal.params or self._build_params(candidates)
                if proposal.note:
                    notes.append(proposal.note)
            elif agent_config is not None:
                notes.append("agent.json 未配置 base_url，章节结构仅使用本地信号生成。")
            else:
                notes.append("未找到 agent.json，章节结构仅使用本地信号生成。")

        if images and not (agent_config and agent_config.vision):
            notes.append("页面图片路径已写入报告；agent_config.vision=false 时不会把图片发送给模型。")

        report = ChapterDoctorReport(
            problem=self.args.problem,
            out_dir=self.out_dir,
            agent_dir=self.agent_dir,
            candidates=candidates,
            page_images=images,
            params=params,
            notes=notes,
            agent=agent_result,
        )
        self._write_report(report)
        return report

    def _propose_with_agent(
        self,
        agent_config: ChapterAgentConfig | None,
        candidates: Sequence[ChapterCandidate],
        images: Sequence[PageImage],
        *,
        mode: Literal["ai", "manual_template"] = "ai",
        template_text: str | None = None,
    ) -> ChapterAgentProposal | None:
        if agent_config is None or not agent_config.base_url:
            return None

        selected_images = self._select_agent_images(images, candidates, agent_config)
        selected_pages = {image.page for image in selected_images}
        marked_images = [
            image.model_copy(update={"selected_for_agent": image.page in selected_pages})
            for image in images
        ]
        agent_result = ChapterAgentClient(agent_config).propose(
            self._agent_payload(
                candidates,
                marked_images,
                mode=mode,
                template_text=template_text,
            ),
            image_files=selected_images if agent_config.vision else [],
        )
        note: str | None = None
        next_candidates = list(candidates)
        params = self._params_from_agent(agent_result)
        if agent_result.ok:
            next_candidates = self._merge_agent_candidates(candidates, agent_result)
            next_candidates = self._dedupe_candidates(next_candidates)
            next_candidates = self._fill_ranges(next_candidates)
        elif agent_result.error:
            note = f"agent 章节建议失败: {agent_result.error}"
        return ChapterAgentProposal(
            candidates=next_candidates,
            params=params,
            images=marked_images,
            agent=agent_result,
            note=note,
        )

    def _effective_out_dir(self) -> Path:
        if self.args.out_dirs:
            return self.args.out_dirs[0]
        if self.args.out_dir is not None:
            return self.args.out_dir
        return Path(f"{self.args.file}.out")

    def _load_inputs(self) -> None:
        self._doc_json = _read_json(self.out_dir / "doc.json")
        md_file = self.out_dir / "doc.md"
        if md_file.is_file():
            self._markdown = md_file.read_text("utf-8", errors="replace")
        prediagnosis = _read_json(self.agent_dir / "doctor-tree-prediagnosis.json")
        if isinstance(prediagnosis, Mapping):
            self._prediagnosis = prediagnosis

    def _page_images(self) -> list[PageImage]:
        pages_dir = self.out_dir / "pages"
        if not pages_dir.is_dir():
            return []
        images: list[PageImage] = []
        for file in pages_dir.iterdir():
            if file.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue
            page = _page_number_from_name(file)
            if page is None:
                continue
            images.append(PageImage(page=page, file=file))
        return sorted(images, key=lambda item: item.page)

    def _collect_candidates(self) -> list[ChapterCandidate]:
        candidates: list[ChapterCandidate] = []
        candidates.extend(self._from_pdf_outline())
        candidates.extend(self._from_doc_tree())
        candidates.extend(self._from_markdown())
        candidates.extend(self._from_doc_json())
        if not candidates:
            candidates.extend(self._fallback_rules())
        return candidates

    def _from_pdf_outline(self) -> list[ChapterCandidate]:
        outline = self._prediagnosis.get("pdf_outline")
        items = outline.get("items") if isinstance(outline, Mapping) else None
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            items = self._inspect_pdf_outline()
        candidates: list[ChapterCandidate] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            level = _as_int(item.get("level"), default=1) or 1
            if level != 1:
                continue
            title = _clean_title(item.get("title"))
            if not title:
                continue
            page = _as_int(item.get("page"))
            candidates.append(
                ChapterCandidate(
                    title=title,
                    page_start=page,
                    source="pdf_outline",
                    confidence=0.95,
                    evidence=[
                        ChapterEvidence(
                            source="pdf_outline", page=page, text=title, detail=dict(item)
                        )
                    ],
                )
            )
        return candidates

    def _inspect_pdf_outline(self) -> list[Mapping[str, Any]]:
        if not self.args.file.is_file() or self.args.file.suffix.lower() != ".pdf":
            return []
        try:
            try:
                import pymupdf
            except ModuleNotFoundError:
                import fitz as pymupdf  # type: ignore

            with pymupdf.open(self.args.file) as doc:
                toc = doc.get_toc(simple=False)
        except Exception:
            return []
        return [
            {"level": row[0], "title": row[1], "page": row[2]}
            for row in toc
            if len(row) >= 3
        ]

    def _from_doc_tree(self) -> list[ChapterCandidate]:
        data = self._doc_json
        if not isinstance(data, Mapping):
            return []
        tree = data.get("tree")
        root = tree.get("root") if isinstance(tree, Mapping) else None
        children = root.get("children") if isinstance(root, Mapping) else None
        if not isinstance(children, Sequence) or isinstance(children, (str, bytes)):
            return []

        candidates: list[ChapterCandidate] = []
        for child in children:
            if not isinstance(child, Mapping):
                continue
            title = _clean_title(_object_text(child.get("data")))
            if not title or title == "<doc>":
                continue
            page = _first_page(child.get("data"))
            candidates.append(
                ChapterCandidate(
                    title=title,
                    page_start=page,
                    source="doc_tree",
                    confidence=0.9,
                    evidence=[ChapterEvidence(source="doc_tree", page=page, text=title)],
                )
            )
        return candidates

    def _from_markdown(self) -> list[ChapterCandidate]:
        if not self._markdown:
            return []
        headings: list[tuple[int, int, str]] = []
        for lineno, line in enumerate(self._markdown.splitlines(), start=1):
            match = _MD_HEADING_RE.match(line)
            if match:
                headings.append((len(match.group(1)), lineno, match.group(2).strip()))
        if not headings:
            return []

        top_level = min(level for level, _, _ in headings)
        candidates: list[ChapterCandidate] = []
        for level, lineno, title in headings:
            if level != top_level:
                continue
            title = _clean_title(title)
            if not title:
                continue
            candidates.append(
                ChapterCandidate(
                    title=title,
                    source="markdown",
                    confidence=0.82,
                    evidence=[
                        ChapterEvidence(
                            source="markdown",
                            text=title,
                            detail={"line": lineno, "heading_level": level},
                        )
                    ],
                )
            )
        return candidates

    def _from_doc_json(self) -> list[ChapterCandidate]:
        data = self._doc_json
        if not isinstance(data, Mapping):
            return []
        pages = data.get("pages")
        if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)):
            return []

        candidates: list[ChapterCandidate] = []
        for page in pages:
            if not isinstance(page, Mapping):
                continue
            page_number = _as_int(page.get("number"))
            objects = page.get("objects")
            if not isinstance(objects, Sequence) or isinstance(objects, (str, bytes)):
                continue
            for obj in objects:
                self._collect_object_candidates(
                    obj,
                    page_number=page_number,
                    candidates=candidates,
                    limit=300,
                )
        return candidates

    def _collect_object_candidates(
        self,
        obj: Any,
        *,
        page_number: int | None,
        candidates: list[ChapterCandidate],
        limit: int,
    ) -> None:
        if len(candidates) >= limit or not isinstance(obj, Mapping):
            return
        typ = obj.get("type")
        text = _clean_title(_object_text(obj))
        if text and _is_chapter_title(text, typ):
            confidence = 0.78 if typ in ("title", "toc") else 0.55
            candidates.append(
                ChapterCandidate(
                    title=text,
                    page_start=page_number,
                    source="doc_json",
                    confidence=confidence,
                    evidence=[
                        ChapterEvidence(
                            source="doc_json",
                            page=page_number,
                            text=text,
                            detail={"type": typ, "bbox": obj.get("bbox")},
                        )
                    ],
                )
            )

        for key in ("objects", "cells", "children", "lines"):
            children = obj.get(key)
            if isinstance(children, Sequence) and not isinstance(children, (str, bytes)):
                for child in children:
                    self._collect_object_candidates(
                        child,
                        page_number=page_number,
                        candidates=candidates,
                        limit=limit,
                    )

    def _fallback_rules(self) -> list[ChapterCandidate]:
        page_count = self._page_count()
        candidates = [
            ChapterCandidate(
                title="<正文>",
                page_start=1 if page_count else None,
                page_end=page_count,
                source="rule",
                confidence=0.3,
                evidence=[
                    ChapterEvidence(
                        source="rule", text="没有找到明显章节标题，退化为正文单章节。"
                    )
                ],
            )
        ]
        return candidates

    def _dedupe_candidates(
        self, candidates: Sequence[ChapterCandidate]
    ) -> list[ChapterCandidate]:
        merged: dict[str, ChapterCandidate] = {}
        for candidate in candidates:
            key = _normalize_title(candidate.title)
            old = merged.get(key)
            if old is None:
                merged[key] = candidate
                continue
            old_score = old.confidence + (0.08 if old.page_start is not None else 0)
            new_score = candidate.confidence + (
                0.08 if candidate.page_start is not None else 0
            )
            keep = old if old_score >= new_score else candidate
            other = candidate if keep is old else old
            evidence = [*old.evidence, *candidate.evidence]
            merged[key] = keep.model_copy(
                update={
                    "confidence": max(old.confidence, candidate.confidence),
                    "page_start": keep.page_start or other.page_start,
                    "page_end": keep.page_end or other.page_end,
                    "evidence": evidence[:10],
                }
            )

        result = list(merged.values())
        result.sort(
            key=lambda item: (
                item.page_start if item.page_start is not None else 10**9,
                -item.confidence,
                item.title,
            )
        )
        return result[:120]

    def _fill_ranges(
        self, candidates: Sequence[ChapterCandidate]
    ) -> list[ChapterCandidate]:
        page_count = self._page_count()
        result: list[ChapterCandidate] = []
        for index, candidate in enumerate(candidates):
            if candidate.page_start is None:
                result.append(candidate)
                continue
            next_page = None
            for next_candidate in candidates[index + 1 :]:
                if next_candidate.page_start and next_candidate.page_start > candidate.page_start:
                    next_page = next_candidate.page_start
                    break
            page_end = candidate.page_end
            if page_end is None:
                if next_page is not None:
                    page_end = max(candidate.page_start, next_page - 1)
                elif page_count:
                    page_end = page_count
            result.append(candidate.model_copy(update={"page_end": page_end}))
        return result

    def _page_count(self) -> int | None:
        data = self._doc_json
        if isinstance(data, Mapping):
            pages = data.get("pages")
            if isinstance(pages, Sequence) and not isinstance(pages, (str, bytes)):
                return len(pages)
        input_info = self._prediagnosis.get("input")
        if isinstance(input_info, Mapping):
            page_count = _as_int(input_info.get("page_count"))
            if page_count:
                return page_count
        images = self._page_images()
        if images:
            return max(image.page for image in images)
        return None

    def _select_agent_images(
        self,
        images: Sequence[PageImage],
        candidates: Sequence[ChapterCandidate],
        config: ChapterAgentConfig,
    ) -> list[PageImage]:
        if not config.vision or not images:
            return []
        pages: list[int] = []
        pages.extend(image.page for image in images[:3])
        for candidate in candidates:
            if candidate.page_start is not None:
                pages.append(candidate.page_start)
            if len(set(pages)) >= config.max_page_images:
                break
        if images:
            pages.append(images[-1].page)

        wanted = set(pages[: config.max_page_images])
        selected = [image for image in images if image.page in wanted]
        return selected[: config.max_page_images]

    def _agent_payload(
        self,
        candidates: Sequence[ChapterCandidate],
        images: Sequence[PageImage],
        *,
        mode: Literal["ai", "manual_template"] = "ai",
        template_text: str | None = None,
    ) -> Mapping[str, Any]:
        return {
            "generation_mode": mode,
            "problem": self.args.problem,
            "file": str(self.args.file),
            "out_dir": str(self.out_dir),
            "page_count": self._page_count(),
            "manual_template": template_text,
            "local_candidates": [
                candidate.model_dump(mode="json", exclude={"evidence"})
                for candidate in candidates[:80]
            ],
            "page_text_samples": self._page_text_samples(),
            "page_images": [
                image.model_dump(mode="json") for image in images[:80]
            ],
            "existing_params": self.args.params_snapshot,
            "requested_output": {
                "chapters": [
                    {
                        "title": "一级章节标题",
                        "page_start": 1,
                        "page_end": 3,
                        "reason": "为什么这样划分",
                    }
                ],
                "params": {
                    "mode": "tree",
                    "tree": {"backend": "default", "template": {"chapters": []}},
                },
                "manual_template_rule": (
                    "当 generation_mode=manual_template 时，按 manual_template 的顺序生成 "
                    "tree.template.chapters。对 <正文> 这类没有固定页码的模板项，"
                    "优先结合 doc.json 推断 titles 正则表达式；不要只输出无法匹配原文标题的逻辑标题。"
                ),
            },
        }

    def _page_text_samples(self) -> list[Mapping[str, Any]]:
        data = self._doc_json
        if not isinstance(data, Mapping):
            return []
        pages = data.get("pages")
        if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)):
            return []

        result: list[Mapping[str, Any]] = []
        for page in _sample_sequence(pages, limit=60, head=20, tail=10):
            if not isinstance(page, Mapping):
                continue
            number = _as_int(page.get("number"))
            text = _object_text(page)
            if not text:
                continue
            result.append({"page": number, "text": text[:800]})
        return result

    def _merge_agent_candidates(
        self,
        candidates: Sequence[ChapterCandidate],
        agent: ChapterAgentResult,
    ) -> list[ChapterCandidate]:
        data = agent.parsed_json
        if not isinstance(data, Mapping):
            return list(candidates)
        chapters = _agent_chapter_items(data)
        if not isinstance(chapters, Sequence) or isinstance(chapters, (str, bytes)):
            return list(candidates)

        agent_candidates: list[ChapterCandidate] = []
        for item in chapters:
            if not isinstance(item, Mapping):
                continue
            title = _agent_chapter_title(item)
            if not title:
                continue
            page_start = _as_int(item.get("page_start"))
            page_end = _as_int(item.get("page_end"))
            if page_start is None:
                page_start, page_end = _pages_to_range(item.get("pages"))
            reason = item.get("reason") if isinstance(item.get("reason"), str) else None
            agent_candidates.append(
                ChapterCandidate(
                    title=title,
                    page_start=page_start,
                    page_end=page_end,
                    source="agent",
                    confidence=0.88,
                    evidence=[
                        ChapterEvidence(
                            source="agent",
                            page=page_start,
                            text=title,
                            detail={"reason": reason} if reason else {},
                        )
                    ],
                )
            )
        return [*agent_candidates, *candidates]

    def _params_from_agent(self, agent: ChapterAgentResult | None) -> Mapping[str, Any] | None:
        if agent is None or not isinstance(agent.parsed_json, Mapping):
            return None
        return _params_from_agent_json(
            agent.parsed_json,
            default_backend=self.args.tree or "default",
        )

    def _build_params(self, candidates: Sequence[ChapterCandidate]) -> Mapping[str, Any]:
        chapters: list[Mapping[str, Any]] = []
        page_count = self._page_count()
        first_page = next(
            (candidate.page_start for candidate in candidates if candidate.page_start),
            None,
        )
        if page_count and (first_page is None or first_page > 1):
            chapters.append({"title": "<首页>", "type": "plain", "pages": [1]})

        has_toc = any(_is_toc_title(candidate.title) for candidate in candidates)
        if has_toc:
            chapters.append({"type": "toc"})

        titles = [
            _exact_title_pattern(candidate.title)
            for candidate in candidates
            if candidate.title
            and not candidate.title.startswith("<")
            and not _is_toc_title(candidate.title)
        ]
        titles = _dedupe_texts(titles)
        if titles:
            chapters.append({"titles": titles})

        return {
            "mode": "tree",
            "tree": {
                "backend": self.args.tree or "default",
                "template": {"chapters": chapters},
            },
        }

    def _load_agent_config(self) -> ChapterAgentConfig | None:
        if not self.args.agent_config.is_file():
            return None
        raw = _read_json(self.args.agent_config)
        if not isinstance(raw, Mapping):
            return None
        data = dict(raw)
        if "url" in data and "base_url" not in data:
            data["base_url"] = data["url"]
        if "key" in data and "api_key" not in data:
            data["api_key"] = data["key"]
        return ChapterAgentConfig.model_validate(data)

    def _write_report(self, report: ChapterDoctorReport) -> None:
        candidates_file = self.agent_dir / "chapter-candidates.json"
        preview_file = self.agent_dir / "chapter-preview.md"
        params_file = self.agent_dir / "chapter-params.json"
        report.report_files = {
            "candidates": candidates_file,
            "preview": preview_file,
            "params": params_file,
        }

        candidates_file.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        preview_file.write_text(format_chapter_report_markdown(report), encoding="utf-8")
        params_file.write_text(
            json.dumps(report.params, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class ChapterDoctorTUI:
    def __init__(
        self,
        *,
        candidates: Sequence[ChapterCandidate],
        params: Mapping[str, Any],
        out_dir: Path,
        agent_dir: Path,
        command: Sequence[str] | None = None,
        images: Sequence[PageImage] | None = None,
        agent_config: ChapterAgentConfig | None = None,
        propose_agent: Callable[..., ChapterAgentProposal | None] | None = None,
    ):
        self.candidates = list(candidates)
        self.params = dict(params)
        self.out_dir = out_dir
        self.agent_dir = agent_dir
        self.command = list(command or [])
        self.images = list(images or [])
        self.agent_config = agent_config
        self.propose_agent = propose_agent
        self.agent: ChapterAgentResult | None = None
        self.params_dirty = False
        self.quit_requested = False

    def run(self) -> ChapterTUIResult:
        if not self.enabled():
            return ChapterTUIResult(self.candidates, self.params, self.images)

        try:
            from rich.console import Console
            from rich.prompt import Prompt
            from rich.table import Table
        except Exception:
            return ChapterTUIResult(
                self.candidates,
                self.params,
                self.images,
                note="rich 不可用，已跳过章节 TUI。",
            )

        console = Console()
        prompt = Prompt()
        notes: list[str] = []
        initial_note = self._choose_generation_mode(console, prompt)
        if initial_note:
            notes.append(initial_note)
        if self.quit_requested:
            return ChapterTUIResult(
                self.candidates,
                self.params,
                self.images,
                note="；".join(_dedupe_texts([note for note in notes if note])),
                agent=self.agent,
            )
        while True:
            console.rule("chapter doctor")
            table = Table(show_header=True, header_style="bold")
            for column in ("#", "title", "pages", "source", "confidence"):
                table.add_column(column)
            for index, candidate in enumerate(self.candidates, start=1):
                table.add_row(
                    str(index),
                    candidate.title,
                    _candidate_pages(candidate),
                    candidate.source,
                    f"{candidate.confidence:.2f}",
                )
            console.print(table)
            console.print(
                "[a] accept  [ai] agent  [m] manual template  "
                "[e] edit  [d] delete  [r] reorder  [s] save  [q] quit"
            )
            action = prompt.ask("action", default="a").strip().lower()
            if action in ("a", "accept", ""):
                self._refresh_params_if_dirty()
                confirmed, note = self._run_parse_and_confirm(console, prompt)
                notes.append(note)
                if confirmed:
                    break
                continue
            if action in ("s", "save"):
                self._refresh_params_if_dirty()
                self._write_live_params()
                confirmed, note = self._run_parse_and_confirm(console, prompt)
                notes.append(note)
                if confirmed:
                    break
                continue
            if action in ("q", "quit"):
                notes.append("TUI 已退出，保留当前章节候选。")
                break
            if action in ("ai", "agent"):
                note = self._run_ai_mode(console)
                if note:
                    notes.append(note)
                continue
            if action in ("m", "manual", "template", "h", "hint"):
                note = self._run_manual_template_mode(console)
                if note:
                    notes.append(note)
                continue
            if action in ("e", "edit"):
                self._edit(console, prompt)
                self.params_dirty = True
                continue
            if action in ("d", "delete"):
                self._delete(prompt)
                self.params_dirty = True
                continue
            if action in ("r", "reorder"):
                self._reorder(prompt)
                self.params_dirty = True
                continue
            console.print("未知操作。")
        return ChapterTUIResult(
            self.candidates,
            self.params,
            self.images,
            note="；".join(_dedupe_texts([note for note in notes if note])),
            agent=self.agent,
        )

    def enabled(self) -> bool:
        if os.environ.get("PPX_CHAPTER_DOCTOR_SKIP_TUI"):
            return False
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return False
        if not self.candidates:
            return False
        return True

    def _choose_generation_mode(self, console: Any, prompt: Any) -> str | None:
        console.rule("chapter doctor")
        console.print("选择章节生成模式：")
        console.print("[ai] agent 根据 doc.json 推理一级章节")
        console.print("[m] 手动输入模板，agent 补全 chapters/titles 正则")
        console.print("[l] 使用当前本地候选")
        default = "ai" if self._agent_ready() else "l"
        choice = prompt.ask("mode", choices=["ai", "m", "l", "q"], default=default)
        choice = choice.strip().lower()
        if choice == "q":
            self.quit_requested = True
            return "TUI 已退出，保留当前章节候选。"
        if choice == "ai":
            return self._run_ai_mode(console)
        if choice == "m":
            return self._run_manual_template_mode(console)
        return "TUI 使用当前本地章节候选。"

    def _run_ai_mode(self, console: Any) -> str | None:
        if not self._agent_ready():
            console.print("agent 未配置 base_url，无法使用 AI 模式。")
            return "agent 未配置 base_url，AI 模式未执行。"
        console.print("agent 正在根据 doc.json/doc.md 和本地候选推理一级章节...")
        proposal = self.propose_agent(
            self.agent_config,
            self.candidates,
            self.images,
            mode="ai",
            template_text=None,
        )
        if proposal is None:
            console.print("agent 不可用，保留当前本地候选。")
            return "agent 不可用，保留当前本地候选。"
        self._apply_agent_proposal(proposal)
        if proposal.agent.ok:
            console.print("AI 章节建议已生成。")
            return "TUI 使用 AI 模式生成章节建议。"
        console.print(proposal.note or "agent 章节建议失败。")
        return proposal.note

    def _run_manual_template_mode(self, console: Any) -> str | None:
        console.print("输入章节模板，空行结束。例如：")
        console.print("<首页>1-1")
        console.print("<正文>")
        console.print("<结尾>")
        template_text = self._read_multiline()
        if not template_text.strip():
            console.print("没有输入模板。")
            return None

        self.agent_dir.mkdir(parents=True, exist_ok=True)
        (self.agent_dir / "chapter-template.txt").write_text(
            template_text,
            encoding="utf-8",
        )
        local_chapters = _template_chapters_from_text(template_text)
        if local_chapters:
            self._apply_template_chapters(local_chapters)
            console.print("已根据手动模板生成 chapters。")
            return "TUI 已根据手动模板生成章节参数。"

        if self._agent_ready():
            console.print("agent 正在根据模板和 doc.json 生成 chapters/titles 正则...")
            proposal = self.propose_agent(
                self.agent_config,
                self.candidates,
                self.images,
                mode="manual_template",
                template_text=template_text,
            )
            if proposal is not None:
                self._apply_agent_proposal(proposal)
                if proposal.agent.ok:
                    console.print("手动模板已由 agent 补全。")
                    return "TUI 使用手动模板模式生成章节参数。"
                console.print(proposal.note or "agent 模板补全失败，尝试按模板直接生成。")

        hints = _parse_hints(template_text)
        if not hints:
            console.print("模板无法直接解析；请修改模板或配置 agent。")
            return "手动模板未生成有效章节参数。"
        self.candidates = _candidates_from_hints(hints)
        self.params = dict(self._params_from_candidates())
        self.params_dirty = False
        console.print("agent 不可用或失败，已按模板直接生成基础 chapters。")
        return "agent 不可用或失败，已按手动模板直接生成基础 chapters。"

    def _read_multiline(self) -> str:
        lines: list[str] = []
        while True:
            try:
                line = input("> ")
            except EOFError:
                break
            if not line.strip():
                break
            lines.append(line)
        return "\n".join(lines)

    def _agent_ready(self) -> bool:
        return (
            self.propose_agent is not None
            and self.agent_config is not None
            and bool(self.agent_config.base_url)
        )

    def _apply_agent_proposal(self, proposal: ChapterAgentProposal) -> None:
        self.images = proposal.images
        self.agent = proposal.agent
        if proposal.agent.ok:
            self.candidates = _candidates_from_params(proposal.params) or proposal.candidates
            if proposal.params is not None:
                self.params = dict(proposal.params)
            else:
                self.params = dict(self._params_from_candidates())
        self.params_dirty = False

    def _apply_template_chapters(self, chapters: Sequence[Mapping[str, Any]]) -> None:
        self.params = dict(self._params_from_template_chapters(chapters))
        self.candidates = _candidates_from_params(self.params) or self.candidates
        self.params_dirty = False

    def _refresh_params_if_dirty(self) -> None:
        if not self.params_dirty:
            return
        self.params = dict(self._params_from_candidates())
        self.params_dirty = False

    def _edit(self, console: Any, prompt: Any) -> None:
        index = self._ask_index(prompt)
        if index is None:
            return
        candidate = self.candidates[index]
        title = prompt.ask("title", default=candidate.title)
        page_start_text = prompt.ask(
            "page_start", default="" if candidate.page_start is None else str(candidate.page_start)
        )
        page_end_text = prompt.ask(
            "page_end", default="" if candidate.page_end is None else str(candidate.page_end)
        )
        page_start = _as_int(page_start_text) if page_start_text.strip() else None
        page_end = _as_int(page_end_text) if page_end_text.strip() else None
        if page_start is not None and page_end is not None and page_end < page_start:
            console.print("page_end 小于 page_start，已忽略这次修改。")
            return
        self.candidates[index] = candidate.model_copy(
            update={
                "title": _clean_title(title) or candidate.title,
                "page_start": page_start,
                "page_end": page_end,
                "source": candidate.source,
                "confidence": candidate.confidence,
            }
        )

    def _delete(self, prompt: Any) -> None:
        index = self._ask_index(prompt)
        if index is None:
            return
        del self.candidates[index]

    def _reorder(self, prompt: Any) -> None:
        old_index = self._ask_index(prompt, label="from")
        if old_index is None:
            return
        new_text = prompt.ask("to", default=str(old_index + 1))
        new_index = _as_int(new_text)
        if new_index is None:
            return
        new_index = max(1, min(len(self.candidates), new_index)) - 1
        item = self.candidates.pop(old_index)
        self.candidates.insert(new_index, item)

    def _ask_index(self, prompt: Any, *, label: str = "index") -> int | None:
        if not self.candidates:
            return None
        value = _as_int(prompt.ask(label, default="1"))
        if value is None or value < 1 or value > len(self.candidates):
            return None
        return value - 1

    def _params_from_candidates(self) -> Mapping[str, Any]:
        hint_chapters = _hint_chapters_from_candidates(self.candidates)
        if hint_chapters:
            return self._params_from_template_chapters(hint_chapters)

        titles = [
            _exact_title_pattern(candidate.title)
            for candidate in self.candidates
            if candidate.title
            and not candidate.title.startswith("<")
            and not _is_toc_title(candidate.title)
        ]
        chapters: list[Mapping[str, Any]] = []
        if any(candidate.title == "<首页>" for candidate in self.candidates):
            chapters.append({"title": "<首页>", "type": "plain", "pages": [1]})
        if any(_is_toc_title(candidate.title) for candidate in self.candidates):
            chapters.append({"type": "toc"})
        if titles:
            chapters.append({"titles": _dedupe_texts(titles)})
        if not chapters and self.candidates:
            first = self.candidates[0]
            if first.page_start is not None:
                page_value: list[int] = [first.page_start]
                if first.page_end is not None and first.page_end != first.page_start:
                    page_value = [first.page_start, first.page_end]
                chapters.append({"title": first.title, "type": "plain", "pages": page_value})
        return self._params_from_template_chapters(chapters)

    def _params_from_template_chapters(
        self, chapters: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Any]:
        return {
            "mode": "tree",
            "tree": {
                "backend": self.params.get("tree", {}).get("backend", "default")
                if isinstance(self.params.get("tree"), Mapping)
                else "default",
                "template": {"chapters": list(chapters)},
            },
        }

    def _run_parse_and_confirm(self, console: Any, prompt: Any) -> tuple[bool, str]:
        params_file = self._write_live_params()
        command = self._command_with_params(params_file)
        if not command:
            return True, f"TUI 已保存章节参数到 {params_file}。"

        console.print("执行章节验证命令：")
        console.print(" ".join(shlex.quote(part) for part in command))
        env = dict(os.environ)
        env["PPX_CHAPTER_DOCTOR_SKIP_TUI"] = "1"
        completed = subprocess.run(command, env=env, check=False)
        if completed.returncode != 0:
            return (
                False,
                f"章节验证命令失败，exit_code={completed.returncode}，请继续修正。",
            )

        answer = prompt.ask("章节结果是否确认", choices=["y", "n"], default="n")
        if answer == "y":
            return True, f"已执行章节验证命令并确认，params={params_file}。"
        return False, "章节验证命令已执行，用户选择继续修正。"

    def _command_with_params(self, params_file: Path) -> list[str]:
        command = self._normalize_command(list(self.command))
        if not command:
            return []

        result: list[str] = []
        index = 0
        while index < len(command):
            item = command[index]
            if item == "--doctor":
                index += 1
                if index < len(command) and not command[index].startswith("-"):
                    index += 1
                continue
            if item.startswith("--doctor="):
                index += 1
                continue
            if item == "--params-file":
                index += 1
                if index < len(command) and not command[index].startswith("-"):
                    index += 1
                continue
            result.append(item)
            index += 1
        result.extend(["--params-file", str(params_file)])
        return result

    def _normalize_command(self, command: list[str]) -> list[str]:
        if len(command) >= 2 and command[0] == "-c" and command[1] == "parse":
            launcher = Path("./ppx")
            executable = "./ppx" if launcher.is_file() else "ppx"
            return [executable, *command[1:]]
        return command

    def _write_live_params(self) -> Path:
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        file = self.agent_dir / "chapter-params.json"
        file.write_text(
            json.dumps(self.params, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return file


class ChapterAgentClient:
    def __init__(self, config: ChapterAgentConfig):
        self.config = config

    def propose(
        self,
        payload: Mapping[str, Any],
        *,
        image_files: Sequence[PageImage],
    ) -> ChapterAgentResult:
        try:
            provider = self._provider()
            if provider == "anthropic":
                return self._anthropic(payload, image_files)
            if provider == "openai-responses":
                return self._openai_responses(payload, image_files)
            return self._openai_chat(payload, image_files)
        except Exception as e:
            return ChapterAgentResult(
                ok=False,
                provider=self.config.provider,
                model=self.config.model,
                vision_used=bool(image_files),
                error=f"{type(e).__name__}: {e}",
            )

    def _openai_chat(
        self, payload: Mapping[str, Any], image_files: Sequence[PageImage]
    ) -> ChapterAgentResult:
        client = self._openai_client()
        content: list[Mapping[str, Any]] = [
            {"type": "text", "text": self._prompt(payload)}
        ]
        content.extend(
            {"type": "image_url", "image_url": {"url": _image_data_url(image.file)}}
            for image in image_files
        )
        params: dict[str, Any] = {
            "model": self._model(),
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": content if image_files else self._prompt(payload)},
            ],
        }
        if self.config.temperature is not None:
            params["temperature"] = self.config.temperature
        if self.config.max_completion_tokens is not None:
            params["max_completion_tokens"] = self.config.max_completion_tokens
        elif self.config.max_tokens is not None:
            params["max_tokens"] = self.config.max_tokens
        effort = self._reasoning_effort()
        if effort:
            params["reasoning_effort"] = effort
        params.update(self.config.params or {})
        response = client.chat.completions.create(**params)
        text = response.choices[0].message.content or ""
        return ChapterAgentResult(
            ok=True,
            provider=self._provider(),
            model=self._model(),
            vision_used=bool(image_files),
            content=text,
            parsed_json=_maybe_json(text),
        )

    def _openai_responses(
        self, payload: Mapping[str, Any], image_files: Sequence[PageImage]
    ) -> ChapterAgentResult:
        client = self._openai_client()
        content: list[Mapping[str, Any]] = [
            {"type": "input_text", "text": self._prompt(payload)}
        ]
        content.extend(
            {"type": "input_image", "image_url": _image_data_url(image.file)}
            for image in image_files
        )
        params: dict[str, Any] = {
            "model": self._model(),
            "instructions": self._system_prompt(),
            "input": [{"role": "user", "content": content}],
        }
        if self.config.temperature is not None:
            params["temperature"] = self.config.temperature
        if self.config.max_output_tokens is not None:
            params["max_output_tokens"] = self.config.max_output_tokens
        elif self.config.max_tokens is not None:
            params["max_output_tokens"] = self.config.max_tokens
        effort = self._reasoning_effort()
        if effort:
            params["reasoning"] = {"effort": effort}
        params.update(self.config.params or {})
        response = client.responses.create(**params)
        text = _extract_openai_response_text(response)
        return ChapterAgentResult(
            ok=True,
            provider=self._provider(),
            model=self._model(),
            vision_used=bool(image_files),
            content=text,
            parsed_json=_maybe_json(text),
        )

    def _anthropic(
        self, payload: Mapping[str, Any], image_files: Sequence[PageImage]
    ) -> ChapterAgentResult:
        client = self._anthropic_client()
        content: list[Mapping[str, Any]] = [{"type": "text", "text": self._prompt(payload)}]
        for image in image_files:
            media_type, data = _image_base64(image.file)
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data,
                    },
                }
            )
        params: dict[str, Any] = {
            "model": self._model(),
            "max_tokens": self.config.max_tokens or 4096,
            "system": self._system_prompt(),
            "messages": [{"role": "user", "content": content}],
        }
        if self.config.temperature is not None:
            params["temperature"] = self.config.temperature
        if self.config.thinking:
            params["thinking"] = dict(self.config.thinking)
        params.update(self.config.params or {})
        response = client.messages.create(**params)
        text = _extract_anthropic_content(response)
        return ChapterAgentResult(
            ok=True,
            provider=self._provider(),
            model=self._model(),
            vision_used=bool(image_files),
            content=text,
            parsed_json=_maybe_json(text),
        )

    def _provider(self) -> str:
        if self.config.provider != "auto":
            return self.config.provider
        base_url = (self.config.base_url or "").lower()
        if "anthropic" in base_url or "claude" in base_url:
            return "anthropic"
        return "openai-chat"

    def _openai_client(self) -> Any:
        try:
            from openai import OpenAI
        except ModuleNotFoundError as e:
            raise RuntimeError("openai SDK is required for openai agent providers") from e

        kwargs: dict[str, Any] = dict(self.config.client or {})
        kwargs["base_url"] = self.config.base_url
        if self.config.api_key is not None:
            kwargs["api_key"] = self.config.api_key
        if self.config.timeout is not None:
            kwargs.setdefault("timeout", self.config.timeout)
        if self.config.headers:
            kwargs.setdefault("default_headers", self.config.headers)
        return OpenAI(**kwargs)

    def _anthropic_client(self) -> Any:
        try:
            from anthropic import Anthropic
        except ModuleNotFoundError as e:
            raise RuntimeError("anthropic SDK is required for anthropic agent provider") from e

        kwargs: dict[str, Any] = dict(self.config.client or {})
        kwargs["base_url"] = self.config.base_url
        if self.config.api_key is not None:
            kwargs["api_key"] = self.config.api_key
        if self.config.timeout is not None:
            kwargs.setdefault("timeout", self.config.timeout)
        headers = dict(self.config.headers)
        if self.config.anthropic_version:
            headers.setdefault("anthropic-version", self.config.anthropic_version)
        if headers:
            kwargs.setdefault("default_headers", headers)
        return Anthropic(**kwargs)

    def _model(self) -> str:
        if not self.config.model:
            raise ValueError("agent model is required")
        return self.config.model

    def _reasoning_effort(self) -> str | None:
        return self.config.model_reasoning_effort or self.config.reasoning_effort

    def _system_prompt(self) -> str:
        return (
            "你是 ppx PDF 章节划分 agent。根据 doc.json/doc.md 信号和可选页面截图，"
            "只梳理一级章节结构，并生成可执行的 tree.template.chapters。"
            "输出严格 JSON，不要 Markdown，不要解释性前后缀。"
        )

    def _prompt(self, payload: Mapping[str, Any]) -> str:
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) > 50000:
            text = text[:50000] + "...<truncated>"
        mode = payload.get("generation_mode")
        if mode == "manual_template":
            return (
                "用户正在通过 TUI 手动模板修正章节。请根据 manual_template 的顺序，"
                "结合 doc.json/doc.md 的页面文本和本地候选，生成只包含一级章节的 params。"
                "模板中类似 <首页>、<目录>、<正文>、<结尾> 是逻辑段落："
                "<首页>/<结尾> 如有页码范围可生成 type=plain 和 pages；"
                "<目录> 生成 type=toc；<正文> 或其它无固定页码的正文段，"
                "应优先从文档真实标题推断 titles 正则表达式，例如第X章、"
                "一、xxx、附件等，而不是只给 title。"
                "titles 必须是 Python re 可用的正则字符串，尽量泛化但避免误匹配。"
                "返回 JSON，必须包含 params，格式为 "
                '{"mode":"tree","tree":{"backend":"default","template":{"chapters":[]}}}；'
                "可以同时包含 chapters 作为人工预览。"
                "\n\n"
                + text
            )
        return (
            "请基于以下 ppx 解析输出，给出一级章节划分。"
            "返回 JSON：chapters 为数组，每项包含 title、page_start、page_end、reason；"
            "params 为可直接传给 --params-file 的对象，格式为 "
            '{"mode":"tree","tree":{"backend":"default","template":{"chapters":[]}}}。'
            "\n\n"
            + text
        )


def should_run_chapter_doctor(problem: str, mode: str | None = None) -> bool:
    _ = mode
    text = (problem or "").lower()
    keywords = ("章节", "目录", "chapter", "tree", "大纲", "结构")
    return any(keyword in text for keyword in keywords)


def format_chapter_report_console(report: ChapterDoctorReport | Mapping[str, Any]) -> str:
    if isinstance(report, Mapping):
        data = report
        candidates = data.get("candidates", [])
        files = data.get("report_files", {})
    else:
        data = report.model_dump(mode="json")
        candidates = data.get("candidates", [])
        files = data.get("report_files", {})

    lines = ["chapter doctor:"]
    if files:
        lines.append(
            "chapter files: "
            + ", ".join(f"{name}={path}" for name, path in files.items())
        )
    if candidates:
        lines.append("chapter candidates:")
        for index, candidate in enumerate(candidates[:20], start=1):
            if not isinstance(candidate, Mapping):
                continue
            page_start = candidate.get("page_start")
            page_end = candidate.get("page_end")
            pages = "-"
            if page_start is not None and page_end is not None:
                pages = f"{page_start}-{page_end}"
            elif page_start is not None:
                pages = str(page_start)
            lines.append(
                f"{index:>2}. {candidate.get('title')} "
                f"pages={pages} source={candidate.get('source')} "
                f"confidence={candidate.get('confidence')}"
            )
    else:
        lines.append("chapter candidates: none")
    return "\n".join(lines)


def format_chapter_report_markdown(report: ChapterDoctorReport) -> str:
    lines = [
        "# ppx chapter doctor",
        "",
        f"- problem: {report.problem}",
        f"- out_dir: {report.out_dir}",
        f"- agent_dir: {report.agent_dir}",
        "",
        "## candidates",
        "",
        "| # | title | pages | source | confidence |",
        "|---:|---|---|---|---:|",
    ]
    for index, candidate in enumerate(report.candidates, start=1):
        pages = ""
        if candidate.page_start is not None and candidate.page_end is not None:
            pages = f"{candidate.page_start}-{candidate.page_end}"
        elif candidate.page_start is not None:
            pages = str(candidate.page_start)
        lines.append(
            f"| {index} | {_md_escape(candidate.title)} | {pages} | "
            f"{candidate.source} | {candidate.confidence:.2f} |"
        )

    lines.extend(["", "## params", "", "```json"])
    lines.append(json.dumps(report.params, ensure_ascii=False, indent=2))
    lines.extend(["```", ""])

    if report.notes:
        lines.extend(["## notes", ""])
        for note in report.notes:
            lines.append(f"- {note}")
        lines.append("")

    if report.agent:
        lines.extend(["## agent", ""])
        lines.append(f"- ok: {report.agent.ok}")
        lines.append(f"- provider: {report.agent.provider}")
        lines.append(f"- model: {report.agent.model}")
        lines.append(f"- vision_used: {report.agent.vision_used}")
        if report.agent.error:
            lines.append(f"- error: {report.agent.error}")
        if report.agent.content:
            lines.extend(["", report.agent.content[:4000], ""])
    return "\n".join(lines)


def _read_json(file: Path) -> Any | None:
    if not file.is_file():
        return None
    try:
        import orjson

        return orjson.loads(file.read_bytes())
    except ModuleNotFoundError:
        return json.loads(file.read_text("utf-8"))
    except Exception:
        return None


def _params_from_agent_json(
    data: Mapping[str, Any],
    *,
    default_backend: str,
) -> Mapping[str, Any] | None:
    params = data.get("params")
    if _template_chapters_from_params(params) is not None and isinstance(params, Mapping):
        return params

    chapters = data.get("chapters")
    if not isinstance(chapters, Sequence) or isinstance(chapters, (str, bytes)):
        return None
    template_chapters = [
        dict(item)
        for item in chapters
        if isinstance(item, Mapping) and _looks_like_template_chapter(item)
    ]
    if not template_chapters:
        return None
    return {
        "mode": "tree",
        "tree": {
            "backend": default_backend,
            "template": {"chapters": template_chapters},
        },
    }


def _template_chapters_from_params(params: Any) -> Sequence[Any] | None:
    if not isinstance(params, Mapping):
        return None
    tree = params.get("tree")
    template = tree.get("template") if isinstance(tree, Mapping) else None
    chapters = template.get("chapters") if isinstance(template, Mapping) else None
    if isinstance(chapters, Sequence) and not isinstance(chapters, (str, bytes)):
        return chapters
    return None


def _agent_chapter_items(data: Mapping[str, Any]) -> Sequence[Any] | None:
    chapters = data.get("chapters")
    if isinstance(chapters, Sequence) and not isinstance(chapters, (str, bytes)):
        return chapters
    return _template_chapters_from_params(data.get("params"))


def _looks_like_template_chapter(item: Mapping[str, Any]) -> bool:
    if any(
        key in item
        for key in ("titles", "pages", "type", "keywords", "style", "deep", "min_keyword_size")
    ):
        return True
    title = item.get("title")
    return isinstance(title, str) and title.startswith("<") and title.endswith(">")


def _agent_chapter_title(item: Mapping[str, Any]) -> str:
    title = _clean_title(item.get("title"))
    if title:
        return title
    typ = item.get("type")
    if typ == "toc":
        return "<目录>"
    titles = item.get("titles")
    if isinstance(titles, Sequence) and not isinstance(titles, (str, bytes)):
        values = [str(value) for value in titles[:3] if value]
        if values:
            suffix = " | ".join(values)
            if len(titles) > len(values):
                suffix += " | ..."
            return f"titles: {suffix}"
    return ""


def _pages_to_range(value: Any) -> tuple[int | None, int | None]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None, None
    numbers = [_as_int(item) for item in value]
    numbers = [number for number in numbers if number is not None]
    if not numbers:
        return None, None
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return numbers[0], numbers[1]


def _candidates_from_params(params: Mapping[str, Any] | None) -> list[ChapterCandidate]:
    chapters = _template_chapters_from_params(params)
    if not chapters:
        return []
    candidates: list[ChapterCandidate] = []
    for item in chapters:
        if not isinstance(item, Mapping):
            continue
        title = _agent_chapter_title(item)
        if not title:
            continue
        page_start, page_end = _pages_to_range(item.get("pages"))
        candidates.append(
            ChapterCandidate(
                title=title,
                page_start=page_start,
                page_end=page_end,
                source="agent",
                confidence=0.9,
                evidence=[
                    ChapterEvidence(
                        source="agent",
                        page=page_start,
                        text=title,
                        detail={"template": dict(item)},
                    )
                ],
            )
        )
    return candidates


def _template_chapters_from_text(text: str) -> list[Mapping[str, Any]]:
    chapters: list[Mapping[str, Any]] = []
    pending_titles: list[str] = []

    def flush_titles() -> None:
        if not pending_titles:
            return
        chapters.append({"titles": _dedupe_texts(pending_titles)})
        pending_titles.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        chapter = _parse_template_chapter_line(line)
        if chapter is not None:
            flush_titles()
            chapters.append(chapter)
            continue
        pending_titles.append(_infer_template_title_pattern(line))
    flush_titles()
    return chapters


def _parse_template_chapter_line(line: str) -> Mapping[str, Any] | None:
    match = re.match(
        r"^(?P<title><[^>]+>)\s*(?P<pages>\([^)]*\)|-?[0-9]+(?:\s*[-~至]\s*-?[0-9]+)?)?\s*$",
        line,
    )
    if not match:
        return None
    chapter: dict[str, Any] = {"title": match.group("title")}
    pages = _parse_template_pages(match.group("pages") or "")
    if pages:
        chapter["pages"] = pages
    return chapter


def _parse_template_pages(text: str) -> list[int]:
    text = text.strip()
    if not text:
        return []
    if text.startswith("(") and text.endswith(")"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        values = [
            _as_int(part.strip())
            for part in re.split(r"\s*[,，]\s*", inner)
            if part.strip()
        ]
        return [value for value in values if value is not None]

    start, end = _parse_page_range(text)
    if start is None:
        return []
    if end is None:
        return [start]
    return [start, end]


def _infer_template_title_pattern(text: str) -> str:
    stripped = text.strip()
    compact = re.sub(r"\s+", "", stripped)
    cn = "零〇一二三四五六七八九十百千万"
    cn_common = "一二三四五六七八九十"

    match = re.match(rf"^第[{cn}]+(?P<unit>[章节篇部])", compact)
    if match:
        return rf"第[{cn_common}]+{match.group('unit')}.+"

    match = re.match(r"^第[0-9]+(?P<unit>[章节篇部])", compact)
    if match:
        return rf"第[0-9]+{match.group('unit')}.+"

    if re.match(rf"^[{cn}]+[、.．]", compact):
        return rf"[{cn_common}]+[、.．].+"

    if re.match(r"^[0-9]+[、.．]", compact):
        return r"[0-9]+[、.．].+"

    return stripped


def _parse_hints(text: str) -> list[ChapterHint]:
    hints: list[ChapterHint] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        line_hints = _parse_inline_hints(line)
        if line_hints:
            hints.extend(line_hints)
            continue
        hint = _parse_hint_line(line)
        if hint is not None:
            hints.append(hint)
    return hints


def _parse_hint_line(line: str) -> ChapterHint | None:
    if len(line) > 80:
        return None
    match = _ANGLE_HINT_LINE_RE.match(line) or _HINT_LINE_RE.match(line)
    if not match:
        return None
    title = _clean_title(match.group("title"))
    if not _looks_like_hint_title(title):
        return None
    page_start: int | None = None
    page_end: int | None = None
    range_text = match.group("range")
    if range_text:
        page_start, page_end = _parse_page_range(range_text)
    return ChapterHint(
        title=title,
        page_start=page_start,
        page_end=page_end,
        raw=line,
    )


def _parse_inline_hints(text: str) -> list[ChapterHint]:
    matches = list(
        re.finditer(r"<[^>]+>\s*(?:-?[0-9]+(?:\s*[-~至]\s*-?[0-9]+)?)?", text)
    )
    hints: list[ChapterHint] = []
    for match in matches:
        hint = _parse_hint_line(match.group(0))
        if hint is not None:
            hints.append(hint)
    return hints


def _candidates_from_hints(hints: Sequence[ChapterHint]) -> list[ChapterCandidate]:
    return [
        ChapterCandidate(
            title=hint.title,
            page_start=hint.page_start,
            page_end=hint.page_end,
            source="rule",
            confidence=0.95,
            evidence=[
                ChapterEvidence(
                    source="rule",
                    page=hint.page_start,
                    text=hint.title,
                    detail={"hint": hint.raw},
                )
            ],
        )
        for hint in hints
    ]


def _parse_page_range(text: str) -> tuple[int | None, int | None]:
    parts = re.split(r"\s*[-~至]\s*", text.strip(), maxsplit=1)
    page_start = _as_int(parts[0])
    page_end = _as_int(parts[1]) if len(parts) > 1 else page_start
    return page_start, page_end


def _looks_like_hint_title(title: str) -> bool:
    if not title:
        return False
    if title.startswith("<") and title.endswith(">"):
        return True
    return _is_toc_title(title) or bool(_CHAPTER_RE.match(title))


def _hint_chapters_from_candidates(
    candidates: Sequence[ChapterCandidate],
) -> list[Mapping[str, Any]]:
    hint_candidates = [
        candidate
        for candidate in candidates
        if candidate.source == "rule"
        and any(e.detail.get("hint") for e in candidate.evidence)
    ]
    if not hint_candidates:
        return []

    chapters: list[Mapping[str, Any]] = []
    for candidate in hint_candidates:
        chapter: dict[str, Any] = {"title": candidate.title}
        if _is_toc_title(candidate.title):
            chapter = {"type": "toc"}
        elif candidate.title.startswith("<") and candidate.title.endswith(">"):
            if candidate.title in ("<首页>", "<封面>"):
                chapter["type"] = "plain"
            elif candidate.title == "<目录>":
                chapter = {"type": "toc"}
            elif candidate.title == "<正文>":
                chapter["type"] = "normal"
            elif candidate.title in ("<结尾>", "<尾页>"):
                chapter["type"] = "plain"
        if candidate.page_start is not None:
            if (
                candidate.page_end is not None
                and candidate.page_end != candidate.page_start
            ):
                chapter["pages"] = [candidate.page_start, candidate.page_end]
            else:
                chapter["pages"] = [candidate.page_start]
        chapters.append(chapter)
    return chapters


def _object_text(obj: Any) -> str:
    if not isinstance(obj, Mapping):
        return ""
    text = obj.get("text")
    if isinstance(text, str):
        return re.sub(r"\s+", " ", text).strip()

    parts: list[str] = []
    for key in ("objects", "cells", "children", "lines"):
        children = obj.get(key)
        if isinstance(children, Sequence) and not isinstance(children, (str, bytes)):
            for child in children:
                child_text = _object_text(child)
                if child_text:
                    parts.append(child_text)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _first_page(obj: Any) -> int | None:
    if not isinstance(obj, Mapping):
        return None
    page = _as_int(obj.get("page_number"))
    if page is not None:
        return page
    for key in ("objects", "cells", "children", "lines"):
        children = obj.get(key)
        if isinstance(children, Sequence) and not isinstance(children, (str, bytes)):
            for child in children:
                page = _first_page(child)
                if page is not None:
                    return page
    return None


def _is_chapter_title(text: str, typ: Any = None) -> bool:
    if not text or len(text) > 160:
        return False
    if re.fullmatch(r"[0-9]+", text.strip()):
        return False
    if _SUBSECTION_RE.match(text):
        return False
    if typ in ("title", "toc"):
        return True
    return _CHAPTER_RE.match(text) is not None


def _clean_title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"\s+", " ", value).strip()
    value = value.strip("# \t\r\n")
    return value[:200]


def _candidate_pages(candidate: ChapterCandidate) -> str:
    if candidate.page_start is not None and candidate.page_end is not None:
        return f"{candidate.page_start}-{candidate.page_end}"
    if candidate.page_start is not None:
        return str(candidate.page_start)
    return ""


def _normalize_title(text: str) -> str:
    return re.sub(r"\s+", "", text).strip().lower()


def _is_toc_title(text: str) -> bool:
    normalized = _normalize_title(text)
    return normalized in {"目录", "正文目录", "图表目录", "图目录", "表目录"} or "目录" in normalized[:10]


def _exact_title_pattern(title: str) -> str:
    escaped = re.escape(title.strip())
    escaped = escaped.replace(r"\ ", r"\s*")
    return f"^{escaped}$"


def _dedupe_texts(items: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _sample_sequence(items: Sequence[Any], *, limit: int, head: int, tail: int) -> list[Any]:
    size = len(items)
    if size <= limit:
        return list(items)
    indexes: set[int] = set(range(min(head, size)))
    indexes.update(range(max(size - tail, 0), size))
    remaining = max(limit - len(indexes), 0)
    if remaining:
        start = min(head, size)
        end = max(size - tail, start)
        span = max(end - start, 1)
        for i in range(remaining):
            indexes.add(start + min(span - 1, round(i * (span - 1) / max(remaining - 1, 1))))
    return [items[index] for index in sorted(indexes)[:limit]]


def _as_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _page_number_from_name(file: Path) -> int | None:
    match = re.match(r"([0-9]+)", file.stem)
    if not match:
        return None
    return _as_int(match.group(1))


def _image_base64(file: Path) -> tuple[str, str]:
    media_type = mimetypes.guess_type(file.name)[0] or "image/png"
    data = base64.b64encode(file.read_bytes()).decode("ascii")
    return media_type, data


def _image_data_url(file: Path) -> str:
    media_type, data = _image_base64(file)
    return f"data:{media_type};base64,{data}"


def _maybe_json(text: str | None) -> Any | None:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    if not cleaned.startswith(("{", "[")):
        match = re.search(r"(\{.*\}|\[.*\])", cleaned, flags=re.DOTALL)
        if match:
            cleaned = match.group(1)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def _extract_openai_response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text
    data = _model_dump(response)
    output = data.get("output") if isinstance(data, Mapping) else None
    if isinstance(output, Sequence) and not isinstance(output, (str, bytes)):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, Mapping):
                continue
            content = item.get("content")
            if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
                continue
            for block in content:
                if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
        if parts:
            return "\n".join(parts)
    return json.dumps(data, ensure_ascii=False)


def _extract_anthropic_content(response: Any) -> str:
    content = getattr(response, "content", None)
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        parts: list[str] = []
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                parts.append(text)
                continue
            data = _model_dump(item)
            if isinstance(data.get("text"), str):
                parts.append(data["text"])
        if parts:
            return "\n".join(parts)
    return json.dumps(_model_dump(response), ensure_ascii=False)


def _model_dump(value: Any) -> Mapping[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return value
    return {"value": str(value)}


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")
