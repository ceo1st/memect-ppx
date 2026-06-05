from __future__ import annotations
import re
from typing import Any

from .models import ChapterIssue, ChapterProposal, EvalResult


_NUMBERED_RE = re.compile(
    r"^\s*("
    r"第[一二三四五六七八九十百千万0-9]+[章节篇部]|"
    r"[0-9]+(?:\.[0-9]+)*[、.．\s]+\S|"
    r"[一二三四五六七八九十]+[、.．]\S"
    r")"
)
_KEYWORD_CHAPTERS = (
    "声明", "前言", "引言", "重要提示", "免责声明", "摘要",
    "目录", "附录", "附件", "参考文献", "致谢",
)
_LOGICAL_CONTAINER = {"<全文>", "<正文>"}


def _extract_headings(tree_md: str, max_depth: int = 2) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for line in tree_md.splitlines():
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not m:
            continue
        depth = len(m.group(1))
        if depth > max_depth:
            continue
        text = m.group(2).strip()
        text = re.sub(r"\\([\\\-\.\(\)\[\]\*])", r"\1", text)
        out.append((depth, text))
    return out


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).strip()


def _rule_match(rule: dict[str, Any], text: str) -> bool:
    titles_pat = rule.get("titles")
    if isinstance(titles_pat, list):
        for pat in titles_pat:
            if not isinstance(pat, str):
                continue
            try:
                if re.match(pat, text):
                    return True
            except re.error:
                continue
    title = rule.get("title")
    if isinstance(title, str) and title:
        nt, nr = _normalize(text), _normalize(title)
        if title.startswith("<") and title.endswith(">"):
            if nt == nr:
                return True
            if title in ("<附件>", "<附录>") and nt.startswith(title.strip("<>")):
                return True
        else:
            if nt == nr or nt.startswith(nr):
                return True
    if rule.get("type") == "toc" and _normalize(text).startswith("目录"):
        return True
    return False


def _invalid_regex(rule: dict[str, Any]) -> list[str]:
    bad: list[str] = []
    titles_pat = rule.get("titles")
    if not isinstance(titles_pat, list):
        return bad
    for pat in titles_pat:
        if not isinstance(pat, str):
            bad.append(repr(pat))
            continue
        try:
            re.compile(pat)
        except re.error as e:
            bad.append(f"{pat} ({e})")
    return bad


def _rule_is_catchall(rule: dict[str, Any]) -> bool:
    title = rule.get("title")
    return isinstance(title, str) and title in ("<正文>", "<附件>", "<附录>")


def _rule_targets_keyword(rule: dict[str, Any], kw: str) -> bool:
    title = rule.get("title")
    if isinstance(title, str) and kw in title:
        return True
    titles_pat = rule.get("titles")
    if isinstance(titles_pat, list):
        for pat in titles_pat:
            if isinstance(pat, str) and kw in pat:
                return True
    return False


def _rule_has_numbered_regex(rule: dict[str, Any]) -> bool:
    titles_pat = rule.get("titles")
    if not isinstance(titles_pat, list):
        return False
    for pat in titles_pat:
        if not isinstance(pat, str):
            continue
        if any(tok in pat for tok in ("第", "[0-9]", "\\d", "一二三")):
            return True
    return False


def _rule_desc(rule: dict[str, Any]) -> str:
    if rule.get("titles"):
        first = rule["titles"][0] if rule["titles"] else ""
        more = len(rule["titles"]) - 1
        return f"titles=[{first}{f', +{more}' if more else ''}]"
    if rule.get("title"):
        return f"title={rule['title']}"
    if rule.get("type"):
        return f"type={rule['type']}"
    return str(rule)


class ChapterEvaluator:
    def evaluate(self, proposal: ChapterProposal, tree_md: str) -> EvalResult:
        issues: list[ChapterIssue] = []
        score = 100
        rules = proposal.chapters

        if not rules:
            issues.append(ChapterIssue("bad_count", "template.chapters 为空"))
            return EvalResult(0, issues)

        if len(rules) > 30:
            issues.append(ChapterIssue(
                "bad_count",
                f"规则过多（{len(rules)}），考虑把同类正则合并到一条 titles 列表",
            ))
            score -= 20

        for i, rule in enumerate(rules):
            bad = _invalid_regex(rule)
            if bad:
                issues.append(ChapterIssue(
                    "bad_pattern",
                    f"第 {i+1} 条规则有无效正则",
                    detail="; ".join(bad),
                ))
                score -= 20

        headings = _extract_headings(tree_md, max_depth=2)
        rule_hits = [0] * len(rules)
        unmatched_signal_h1: list[str] = []
        unmatched_numbered_h2: list[str] = []
        has_normal_catchall = any(
            r.get("title") == "<正文>" or r.get("type") == "normal"
            for r in rules
        )

        for depth, text in headings:
            matched_idx = None
            for i, rule in enumerate(rules):
                if _rule_match(rule, text):
                    matched_idx = i
                    break
            if matched_idx is None:
                if depth == 1 and text not in _LOGICAL_CONTAINER:
                    looks_like_chapter = bool(_NUMBERED_RE.match(text)) or any(
                        kw in text for kw in _KEYWORD_CHAPTERS
                    )
                    if looks_like_chapter:
                        unmatched_signal_h1.append(text)
                elif depth == 2 and _NUMBERED_RE.match(text):
                    unmatched_numbered_h2.append(text)
            else:
                rule_hits[matched_idx] += 1

        for i, rule in enumerate(rules):
            if _rule_is_catchall(rule) and rule_hits[i] == 0:
                rule_hits[i] = 1

        for i, hits in enumerate(rule_hits):
            if hits == 0:
                issues.append(ChapterIssue(
                    "unused_rule",
                    f"第 {i+1} 条规则未命中任何标题：{_rule_desc(rules[i])}",
                ))
                score -= 5

        for text in unmatched_signal_h1[:10]:
            issues.append(ChapterIssue(
                "unmatched_h1",
                f"H1 标题像章节但未被任何规则覆盖：{text[:80]}",
            ))
            score -= 5

        _ = has_normal_catchall  # 保留以便未来用于宽松判断

        if unmatched_numbered_h2:
            issues.append(ChapterIssue(
                "promote_h2",
                f"H2 中存在编号章节但模板未把它们提升为一级，共 {len(unmatched_numbered_h2)} 个",
                detail="示例：" + " / ".join(t[:40] for t in unmatched_numbered_h2[:3]),
            ))
            score -= 12

        all_text = " ".join(text for _, text in headings)
        for kw in _KEYWORD_CHAPTERS:
            if kw not in all_text:
                continue
            covered = any(_rule_targets_keyword(rule, kw) for rule in rules)
            if not covered:
                issues.append(ChapterIssue(
                    "missing_keyword",
                    f"tree.md 出现 '{kw}' 但模板没有对应规则",
                ))
                score -= 8

        has_numbered_heading = any(_NUMBERED_RE.match(t) for _, t in headings)
        if has_numbered_heading:
            has_numbered_rule = any(_rule_has_numbered_regex(r) for r in rules)
            if not has_numbered_rule:
                issues.append(ChapterIssue(
                    "missing_numbered",
                    "文档含编号章节但模板缺少 titles 正则规则",
                ))
                score -= 15

        has_cover_rule = any(
            (r.get("title") in ("<首页>", "<封面>")) or
            (r.get("type") == "plain" and 1 in (r.get("pages") or []))
            for r in rules
        )
        if not has_cover_rule:
            issues.append(ChapterIssue(
                "missing_cover", "模板缺少首页/封面规则",
            ))
            score -= 8

        return EvalResult(max(0, score), issues)
