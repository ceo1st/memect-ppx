import json
import logging
import re
import threading
from typing import Any, Final, Mapping, Self, Sequence, override

from anthropic import Anthropic
from openai import OpenAI

from memect.base.debug import XDebugger
from memect.base.pattern import XPattern
from memect.base.utils import MyBaseModel
from memect.pdf.base import KDocument, KObject, KText, TreeBackend
from memect.pdf.x.xchapter import XChapterParser
from .xbase import XNo, XNode, XObject, XText, XTree
from .xgroup import XGroupParser
from .xtable import XTableParser
from .xtext import XTextParser


class LLMParserArgs(MyBaseModel):
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


class DefaultParserArgs(MyBaseModel):
    extra_patterns: list[str] = []
    exclude_texts: list[str] = []
    chapter_indices: list[int] | None = None
    llm: LLMParserArgs | None = None
    """可选LLM，对规则候选做二次语义判断"""


class XTreeParserArgs(MyBaseModel):
    llm: Mapping[str, Any] | None = None
    default: Mapping[str, Any] | None = None


class XTreeParser:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    def __init__(self, args: Mapping[str, Any] | XTreeParserArgs):
        super().__init__()
        self._args: Final = XTreeParserArgs.create(args)
        self._text_parser = XTextParser()
        self._table_parser = XTableParser()
        self._group_parser = XGroupParser()
        self._chapter_parsers: Final[dict[TreeBackend, XChapterParser]] = {}
        self._lock: Final = threading.RLock()

    def _get_chapter_parser(self, backend: TreeBackend) -> XChapterParser:
        with self._lock:
            parser = self._chapter_parsers.get(backend)
            if parser is None:
                if backend == TreeBackend.DEFAULT:
                    from .xchapter_default import Parser

                    parser = Parser(self._args.default)
                elif backend == TreeBackend.LLM:
                    from .xchapter_llm import Parser

                    parser = Parser(self._args.llm)
                else:
                    raise ValueError(f"不支持的backend={backend}")
                self._chapter_parsers[backend] = parser
            return parser

    def parse(self, doc: KDocument):
        debugger: Final = self._debugger.bind()
        xtree = XTree(doc)
        # 跨页/跨栏文本合并
        self._text_parser.parse(xtree)
        # 跨页/跨栏表格合并
        self._table_parser.parse(xtree)
        # 引用等的处理，也就是把某些局部内容先分成一个组，不需要再细分
        self._group_parser.parse(xtree)

        self._get_chapter_parser(doc.params.tree.backend).parse(xtree)

        xtree.root.setup_ids()
        
        if debugger.allow("save"):
            doc.write("debug/xtree.txt", xtree.root.stringify())

        
