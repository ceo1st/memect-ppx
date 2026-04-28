from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Final, Sequence

from memect.pdf.base import KDocument, KPage, TableMode
from memect.pdf.default.table.wbk import WBKMode
from memect.pdf.default.table.ybk import YBKMode
from memect.pdf.model import ModelManager


class TableParser:
    def __init__(self,manager:ModelManager):
        super().__init__()
        self._manager:Final = manager
        self._table_cls: Final = manager.get("table_cls")
        self._table_cls_key: Final = "cache/default/table_cls"
        self._table_det: Final = manager.get("table_det")
        self._table_det_key: Final = "cache/default/table_det"
        self._table_llm = manager.get("table_llm")
        self._table_llm_key: Final = "cache/default/table_llm"

    def _do(
        self, fn: Callable[[KPage], None], pages: Sequence[KPage], max_workers: int = 0
    ):
        if max_workers == 0:
            for page in pages:
                fn(page)
        else:
            # 在free-threaded后才真正使用多核心
            with ThreadPoolExecutor(
                max_workers, thread_name_prefix=fn.__name__
            ) as executor:
                for _ in executor.map(fn, pages):
                    pass

    def parse(self, doc: KDocument, max_workers: int = 0):
        if doc.params.table == TableMode.NO:
            # 不用解析表格，全部作为图片
            self._parse_as_figures(doc, max_workers=max_workers)
        elif doc.params.table == TableMode.LLM:
            self._parse_llm(doc, max_workers=max_workers)
        elif doc.params.table == TableMode.YBK:
            # 全部按有边框
            self._parse_ybk(doc, max_workers=max_workers)
        elif doc.params.table == TableMode.WBK:
            # 全部按无边框
            self._parse_wbk(doc, max_workers=max_workers)
        elif doc.params.table == TableMode.AUTO:
            self._parse_auto(doc, max_workers=max_workers)
        else:
            raise ValueError(f"不支持的表格mode={doc.params.table}")

    def _parse_as_figures(self, doc: KDocument, *, max_workers: int = 0):
        def parse_page(page: KPage):
            for vobj in page.vobjects:
                if vobj.is_table():
                    figure = vobj.make_figure(dx=2,dy=2)
                    page.objects.append(figure)

        self._do(parse_page, doc.working_pages, max_workers=max_workers)

    def _parse_llm(self, doc: KDocument, *, max_workers: int = 0):
        """使用llm来解析表格"""
        from .llm import Parser
        Parser(self._manager).parse(doc,max_workers=max_workers)

    def _parse_ybk(self, doc: KDocument, *, max_workers: int = 0):
        """全部按有边框来解析"""
        from .ybk import Parser        
        Parser().parse(doc,max_workers=max_workers,mode=YBKMode.AUTO)

    def _parse_wbk(self, doc: KDocument, *, max_workers: int = 0):
        """全部按无边框解析，表格的线仅仅用来参考"""
        from .wbk import Parser        
        Parser(self._manager).parse(doc,max_workers=max_workers,mode=WBKMode.ALL)

    def _parse_auto(self, doc: KDocument, *, max_workers: int = 0):
        """自动选择最合适的"""
        from .wbk import Parser
        Parser(self._manager).parse(doc,max_workers=max_workers,mode=WBKMode.AUTO)

