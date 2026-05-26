import json
import logging
import re
from typing import Any, Final, Mapping, Sequence

from anthropic import Anthropic
from openai import OpenAI

from memect.base.utils import MyBaseModel
from memect.pdf.base import KDocument, KObject, KText
from .xbase import XNode, XObject, XText, XTree


class ParserArgs(MyBaseModel):
    # or anthropic
    provider: str = "openai"
    model: str
    base_url: str
    api_key: str
    prompt: str
    temperature: float = 0
    max_tokens: int = 1024 * 4
    client: Mapping[str, Any] | None = None
    """可以配置特定的参数，参考:OpenAI/Anthropic"""
    extras: Mapping[str, Any] | None = None
    """可以配置特定的参数，在调用的时候"""


class Parser:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self, args: Mapping[str, Any] | ParserArgs|None=None):
        super().__init__()
        args = ParserArgs.create(args)
        self._args: Final = args
        self._openai: OpenAI | None = None
        self._anthropic: Anthropic | None = None
        if args.provider == "openai":
            self._openai = self._create_openai(args)
        elif args.provider == "anthropic":
            self._anthropic = self._create_anthropic(args)
        else:
            raise ValueError(f"不支持的provider:{args.provider}")

    def _create_openai(self, args: ParserArgs):
        kwargs: dict[str, Any] = {}
        if args.client:
            kwargs.update(args.client)
        kwargs["api_key"] = args.api_key
        kwargs["base_url"] = args.base_url
        return OpenAI(**kwargs)

    def _create_anthropic(self, args: ParserArgs):
        kwargs: dict[str, Any] = {}
        if args.client:
            kwargs.update(args.client)
        return Anthropic(api_key=args.api_key, base_url=args.base_url, **kwargs)

    def parse(self, xtree: XTree):
        self._build(
            xtree.root, xtree.xobjects, self._get_result(xtree.doc, xtree.xobjects)
        )

    def _get_result(self, doc: KDocument, objects: Sequence[KObject]) -> Any:
        # 如果太长了，如何分割？
        cache_filename = "cache/xtree/llm.json"
        if doc.is_dev() and doc.has_file(cache_filename):
            result = doc.read_json(cache_filename) or {}
        else:
            buf: list[str] = []
            for idx, obj in enumerate(objects):
                buf.append(f"[{obj.page.number},{idx},{obj.type}]")
                if isinstance(obj, KText):
                    buf.append(obj.text)
                buf.append("\n")
            if len(buf) > 0:
                buf.pop()
            result = self._invoke("".join(buf))
            if doc.is_dev():
                doc.write(cache_filename, result)
        return result

    def _invoke(self, text: str) -> dict[str, Any]:
        prompt = self._args.prompt + text
        print(prompt)
        messages: list[Any] = []
        messages.append({"role": "system", "content": "你是一个章节树专家"})
        # {"content":[{"type":"text","text":""}]}
        messages.append({"role": "user", "content": prompt})
        if self._openai is not None:
            completions = self._openai.chat.completions.create(
                model=self._args.model,
                temperature=self._args.temperature,
                max_tokens=self._args.max_tokens,
                # response_format='json',
                messages=messages,
                **(self._args.extras or {}),
            )
            usage = completions.usage
            input_tokens = usage.prompt_tokens or 1  # 输入 token 数
            output_tokens = usage.completion_tokens or 1  # 输出 token 数
            # total_tokens = usage.total_tokens        # 总 token 数
            result = completions.choices[0].message.content or ""
            print("===============")
            print(result)
            self._logger.info(
                "input_chars/token=%.2f,output_chars/token=%.2f,input_token=%s,output_token=%s,input_text=%s,output_text=%s",
                len(prompt) / input_tokens,
                len(result) / output_tokens,
                input_tokens,
                output_tokens,
                len(prompt),
                len(result),
            )
            return result
        elif self._anthropic is not None:
            response = self._anthropic.messages.create(
                system="只返回纯JSON，不要有任何多余文字、注释或markdown代码块包裹。",
                model=self._args.model,
                max_tokens=self._args.max_tokens,
                temperature=self._args.temperature,
                messages=messages,
                **(self._args.extras or {}),
            )
            usage = response.usage
            input_tokens = usage.input_tokens or 1
            output_tokens = usage.output_tokens or 1
            result = response.content[0].text
            self._logger.info(
                "input_chars/token=%.2f,output_chars/token=%.2f,input_token=%s,output_token=%s,input_text=%s,output_text=%s",
                len(prompt) / input_tokens,
                len(result) / output_tokens,
                input_tokens,
                output_tokens,
                len(prompt),
                len(result),
            )

            # 去除 ```plain ... ``` 或 ``` ... ```
            result = re.sub(r"```\w*\n?(.*?)```", r"\1", text, flags=re.DOTALL).strip()
            print(result)
            # 验证是否为合法 JSON
            try:
                return json.loads(result)
            except json.JSONDecodeError as e:
                self._logger.error("invalid json: %s, result=%s", e, result)
                raise
        else:
            raise RuntimeError()

    def _build(self, root: XNode, objects: Sequence[XObject], result: Sequence[Any]):
        # result=[{},{},1]

        used_idx: list[int] = []

        def create_xobject(ids: Sequence[int]) -> XObject | None:
            group: list[KObject] = []
            for idx in ids:
                if idx in used_idx:
                    self._logger.warning("重复使用了同一个idx=%s", idx)
                elif idx < len(objects):
                    group.append(objects[idx])
                    used_idx.append(idx)
                else:
                    self._logger.warning("返回了不存在的idx=%s", idx)
            if len(group) > 0:
                return XObject.from_objects(group)
            else:
                self._logger.warning("无法创建对象：ids=%s", ids)
                return None

        def parse(parent: XNode, values: Sequence[Any]):
            for value in values:
                xobj: XObject | None = None
                if isinstance(value, int):
                    xobj = create_xobject([value])
                elif isinstance(value, Sequence):
                    xobj = create_xobject(value)
                elif isinstance(value, dict):
                    label = value.get("label")
                    ids = value.get("ids")
                    children = value.get("children")
                    if label:
                        # 逻辑标题
                        xobj = XText.create_title(f"<{label}>")
                    elif isinstance(ids, Sequence):
                        xobj = create_xobject(ids)
                    else:
                        pass

                    if xobj is None:
                        self._logger.warning("无法创建对象:%s", value)
                    if children and xobj is not None:
                        if isinstance(xobj, XText):
                            xobj.as_title()
                        parse(xobj.node, children)
                else:
                    self._logger.warning("返回了不支持的对象:%s", value)

                if xobj is not None:
                    parent.add(xobj)

        parse(root, result)

    def _build2(self, root: XNode, objects: Sequence[KObject], result: str):
        # [-1]xxx => -1 表示为逻辑标题
        # [1,2,3] =>多个表示合并为一个
        pattern = re.compile(
            r"(?P<indent>[\s]*)\[(?P<id>-1|[0-9]+(,[0-9]+)?)\](?P<text>.*)"
        )
        levels: list[tuple[int, list[int], str]] = []
        for line in result.splitlines():
            m = pattern.fullmatch(line)
            if m:
                level = len(m.group("indent")) // 4
                ids = [int(v) for v in m.group("id").split(",")]
                s = m.group("text")
                levels.append((level, ids, s))
            else:
                # 错误的返回？
                self._logger.warning("返回错误的行:%s", line)

        for level, ids, s in levels:
            xobj: XObject | None = None
            if len(ids) == 1 and ids[0] == -1:
                # 表示逻辑标题，没有原文出处，但是还是需要计算一个坐标（页码+bbox）
                # 页码+bbox：使用第一个子的？
                # 逻辑标题必须有子，否则没有存在的必要？
                xobj = XText.create_title(f"<{s}>")
            else:
                group: list[KObject] = []
                for idx in ids:
                    if 0 <= idx < len(objects):
                        obj = objects[idx]
                        group.append(obj)
                    else:
                        # 如果包含了错误的idx，只是警告，然后继续？
                        self._logger.warning("错误的idx:%s", idx)
                if len(group) > 0:
                    xobj = XObject.from_objects(group)
                else:
                    self._logger.warning("错误的ids，没有对应任何对象:%s", ids)

            if xobj is not None:
                parent = root
                n = -1
                while n < level - 1:
                    parent = parent.children[-1]
                    n += 1
                parent.add(xobj)

        # 标记有子的文本为标题
        def setup_titles(node: XNode):
            if node.is_text() and node.size > 0:
                node.text.as_title()
                for child in node.children:
                    setup_titles(child)

        setup_titles(root)
