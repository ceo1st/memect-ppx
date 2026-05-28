import json
import logging
import re
from typing import Any, Final, Mapping, Sequence

from anthropic import Anthropic
from openai import OpenAI

from memect.base.utils import MyBaseModel
from memect.pdf.base import KDocument
from .xbase import XNode, XObject, XText, XTree


class ParserArgs(MyBaseModel):
    # or anthropic
    provider: str = "openai"
    model: str
    base_url: str
    api_key: str
    prompt: str
    temperature: float = 0
    max_tokens: int = 1024 * 8
    client: Mapping[str, Any] | None = None
    """可以配置特定的参数，参考:OpenAI/Anthropic"""
    extras: Mapping[str, Any] | None = None
    """可以配置特定的参数，在调用的时候"""


json_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ChapterStructure",
    "type": "array",
    "items": {"$ref": "#/definitions/ChapterNode"},
    "definitions": {
        "ChapterNode": {
            "oneOf": [
                {
                    "description": "叶子节点：内容节点序号",
                    "type": "integer",
                    "minimum": 0,
                },
                {
                    "description": "原文标题节点：来自原文的章节标题",
                    "type": "object",
                    "properties": {
                        "id": {
                            "description": "原文标题节点的序号",
                            "type": "integer",
                            "minimum": 0,
                        },
                        "children": {
                            "description": "子节点列表，包含内容节点和子章节",
                            "type": "array",
                            "items": {"$ref": "#/definitions/ChapterNode"},
                            "minItems": 1,
                        },
                    },
                    "required": ["id", "children"],
                    "additionalProperties": False,
                },
                {
                    "description": "逻辑标题节点：无原文对应标题，自拟标题",
                    "type": "object",
                    "properties": {
                        "title": {
                            "description": "自拟的章节标题文字",
                            "type": "string",
                            "minLength": 1,
                        },
                        "children": {
                            "description": "子节点列表，包含内容节点和子章节",
                            "type": "array",
                            "items": {"$ref": "#/definitions/ChapterNode"},
                            "minItems": 1,
                        },
                    },
                    "required": ["title", "children"],
                    "additionalProperties": False,
                },
            ]
        }
    },
}


class Parser:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self, args: Mapping[str, Any] | ParserArgs | None = None):
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

    def _get_result(self, doc: KDocument, objects: Sequence[XObject]) -> Any:
        # 如果太长了，如何分割？
        cache_filename = "cache/xtree/llm.json"
        if doc.is_dev() and doc.has_file(cache_filename):
            result = doc.read_json(cache_filename) or {}
        else:
            buf: list[str] = []
            for idx, obj in enumerate(objects):
                # [1,(1,2),xtext]
                buf.append(
                    f"[{idx},({obj.page_numbers[0]},{obj.page_numbers[-1]}),{obj.type}]"
                )
                if isinstance(obj, XText):
                    buf.append(re.sub(r"\\n", "", obj.text))
                buf.append("\n")
            if len(buf) > 0:
                buf.pop()
            result = self._invoke("".join(buf))
            if doc.is_dev():
                doc.write(cache_filename, result)
        return result

    def _invoke(self, text: str) -> dict[str, Any]:
        verbose = True
        from memect.base.utils import console

        prompt = self._args.prompt + text
        if verbose:
            console.print(prompt)
        # print(prompt)
        messages: list[Any] = []
        # messages.append({"role": "system", "content": "你是一个章节专家"})
        # {"content":[{"type":"text","text":""}]}
        messages.append({"role": "user", "content": prompt})
        input_text = json.dumps(messages, ensure_ascii=False)
        if self._openai is not None:
            completions = self._openai.chat.completions.create(
                model=self._args.model,
                temperature=self._args.temperature,
                max_tokens=self._args.max_tokens,
                # response_format='json',
                messages=messages,
                response_format={"type": "json_object"},
                #response_format={"type":"json_schema","json_schema":json_schema},
                **(self._args.extras or {}),
            )
            usage = completions.usage
            input_tokens = usage.prompt_tokens or 1  # 输入 token 数
            output_tokens = usage.completion_tokens or 1  # 输出 token 数
            # total_tokens = usage.total_tokens        # 总 token 数
            output_text = completions.choices[0].message.content or ""
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
            output_text = response.content[0].text
            # 去除 ```plain ... ``` 或 ``` ... ```
            output_text = re.sub(
                r"```\w*\n?(.*?)```", r"\1", output_text, flags=re.DOTALL
            ).strip()
        else:
            raise RuntimeError()

        if verbose:
            console.print(output_text)
        self._logger.info(
            "input_chars/token=%.2f,output_chars/token=%.2f,input_token=%s,output_token=%s,input_text=%s,output_text=%s",
            len(input_text) / input_tokens,
            len(output_text) / output_tokens,
            input_tokens,
            output_tokens,
            len(input_text),
            len(output_text),
        )
        try:
            return json.loads(output_text)
        except json.JSONDecodeError as e:
            self._logger.error("invalid json: %s, result=%s", e, output_text)
            raise

    def _build(self, root: XNode, objects: Sequence[XObject], result: Sequence[Any]):
        # result=[{},{},1]

        used_idx: list[int] = []

        def get_xobject(idx: int) -> XObject | None:
            if idx in used_idx:
                self._logger.warning("重复使用了同一个idx=%s", idx)
                return None
            elif idx >= len(objects):
                self._logger.warning("返回了不存在的idx=%s", idx)
            else:
                used_idx.append(idx)
                return objects[idx]

        def parse(parent: XNode, values: Sequence[Any]):
            for value in values:
                xobj: XObject | None = None
                if isinstance(value, int):
                    xobj = get_xobject(value)
                elif isinstance(value, dict):
                    title = value.get("title")
                    idx = value.get("id", None)
                    children = value.get("children")
                    if title:
                        # 逻辑标题
                        xobj = XText.create_title(f"<{title}>")
                    elif isinstance(idx, int):
                        xobj = get_xobject(idx)
                    else:
                        pass

                    if xobj is None:
                        self._logger.warning("无法创建对象:%s", value)
                    if children and xobj is not None:
                        if isinstance(xobj, XText):
                            xobj.as_title()
                            parse(xobj.node, children)
                        else:
                            self._logger.warning("非title有子:%s", value)
                else:
                    self._logger.warning("返回了不支持的对象:%s", value)

                if xobj is not None:
                    parent.add(xobj)

        parse(root, result)
