from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any

from memect.base.utils import console

from .models import ChapterProposal, EvalResult


_SYSTEM_PROMPT = """\
你是 PDF 一级章节模板专家。根据 tree.md 内容总结一份 template.chapters 规则清单，
描述"什么样的标题/段落算一级章节"。不要直接列出本文档的具体章节标题。

规则类型（每条规则是一个 dict，顺序按文档结构排列）：

1) 逻辑章节（无原文标题的固定段落，用尖括号占位）：
   {"title": "<首页>", "type": "plain", "pages": [1]}
   {"title": "<封面>", "type": "plain"}
   {"type": "toc"}                                # 目录
   {"title": "<正文>", "type": "normal"}          # 正文容器，谨慎使用
   {"title": "<结尾>", "type": "plain"}           # 文末免责/声明等

2) 关键字字面标题（出现在原文的固定词）：
   {"title": "声明"}
   {"title": "前言"}
   {"title": "免责声明"}
   {"title": "摘要"}
   {"title": "附录"}

3) 正则规则（覆盖带序号的章节，titles 是 Python re 可用的正则列表）：
   {"titles": ["^第[一二三四五六七八九十0-9]+[章节篇部].+"]}
   {"titles": ["^[0-9]+(?:\\\\.[0-9]+)*[、.．\\\\s]+\\\\S.+"]}
   {"titles": ["^[一二三四五六七八九十]+[、.．].+"]}
   同类型规则可合并到同一条 titles 列表里。

约束：
- 只描述第一级，不要 1.1 / 1.1.1 之类的子级正则
- 规则顺序按文档预期顺序：封面 → 摘要/声明 → 目录 → 正文 → 附录 → 结尾
- 如果文档没有规范标题的正文，使用 {"title": "<正文>", "type": "normal"}
- 总规则数控制在 ≤15 条

如收到 previous_attempt，必须针对 issues 逐条修正：
- missing_keyword → 添加 {"title": "<关键字>"} 规则
- missing_numbered → 添加 titles 正则规则
- unused_rule → 删除或合并该规则
- unmatched_h1 → 补充能匹配该标题的规则
- promote_h2 → tree.md 当前把章节误划为 H2，加 titles 正则把它们提升为一级

输出严格 JSON：{"chapters": [...]}
不含 markdown 代码块，不含解释。\
"""


def _load_agent_config(agent_json: Path) -> dict[str, Any]:
    if not agent_json.is_file():
        raise FileNotFoundError(f"agent.json not found: {agent_json}")
    return json.loads(agent_json.read_text("utf-8"))


def _call_llm(cfg: dict[str, Any], prompt: str) -> str:
    provider = cfg.get("provider", "openai-chat")
    base_url = cfg["base_url"]
    api_key = cfg.get("api_key", "")
    model = cfg["model"]
    max_tokens = cfg.get("max_tokens", 4096)

    if provider in ("openai-chat", "openai"):
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key or "sk-x")
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=cfg.get("temperature", 0),
        )
        return resp.choices[0].message.content or ""
    elif provider == "openai-responses":
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key or "sk-x")
        effort = cfg.get("model_reasoning_effort") or cfg.get("reasoning_effort")
        params: dict[str, Any] = dict(
            model=model,
            instructions=_SYSTEM_PROMPT,
            input=[{"role": "user", "content": prompt}],
            max_output_tokens=max_tokens,
        )
        if effort:
            params["reasoning"] = {"effort": effort}
        resp = client.responses.create(**params)
        return getattr(resp, "output_text", None) or ""
    elif provider == "anthropic":
        from anthropic import Anthropic
        client = Anthropic(base_url=base_url, api_key=api_key)
        resp = client.messages.create(
            model=model,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        return resp.content[0].text
    else:
        raise ValueError(f"unsupported provider: {provider}")


def _parse_response(text: str) -> ChapterProposal:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```\w*\n?", "", cleaned).rstrip("`").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}

    chapters = data.get("chapters", []) if isinstance(data, dict) else []
    if not isinstance(chapters, list):
        chapters = []
    chapters = [c for c in chapters if isinstance(c, dict)]
    return ChapterProposal(chapters=chapters, raw=data)


class ChapterProposer:
    def __init__(self, agent_json: Path):
        self._cfg = _load_agent_config(agent_json)

    def propose(
        self,
        tree_md: str,
        prev_proposal: ChapterProposal | None = None,
        prev_eval: EvalResult | None = None,
    ) -> ChapterProposal:
        payload: dict[str, Any] = {"tree_md": tree_md[:60000]}
        if prev_proposal and prev_eval:
            payload["previous_attempt"] = {
                "template_chapters": list(prev_proposal.chapters),
                "score": prev_eval.score,
                "issues": [
                    {"kind": i.kind, "advice": i.advice, "detail": i.detail}
                    for i in prev_eval.issues
                ],
            }
        prompt = json.dumps(payload, ensure_ascii=False)
        console.print(
            f"  → calling {self._cfg.get('provider', 'openai-chat')} "
            f"model={self._cfg.get('model')}  "
            f"prompt={len(prompt)} chars  "
            f"with_critique={prev_eval is not None}",
            style="dim",
        )
        text = _call_llm(self._cfg, prompt)
        console.print(f"  ← response {len(text)} chars", style="dim")
        return _parse_response(text)
