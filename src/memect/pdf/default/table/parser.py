from concurrent.futures import ThreadPoolExecutor
import logging
from typing import Callable, Final, Sequence

from memect.base.bbox import BBox
from memect.pdf.base import KDocument, KLine, KPage, TableMode, VObject
from memect.pdf.default.table.wbk import WBKMode
from memect.pdf.default.table.ybk import YBKMode
from memect.pdf.model import ModelManager


class TableParser:
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    def __init__(self,manager:ModelManager):
        super().__init__()
        self._manager:Final = manager

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
        #elif doc.params.table == TableMode.LLM:
            #self._parse_llm(doc, max_workers=max_workers)
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

    def _fix1(self, page: KPage):

        """
        修正layout识别错误的表格
        """

        #第一种：1个表格的，被识别为2个
        #--t1--
        #--title-- 这个被识别为普通标题，但是应该为表格内容，同时需要从page.objects中删除
        #--t2--

        #第二种: 粘连在一起的表格，被识别为2个，可能是因为表头颜色不同？
        #--t1--
        #--------
        #--t2--

        #第三种：多包含内容
        #----单位-- 可能包含了这些不属于表格的内容
        #--t1-----

        lines = page.pdf_lines
        h_lines, v_lines = KLine.split(lines)

        def has_v_lines(bbox: BBox) -> bool:
            n = 0
            for line in v_lines:
                if (
                    line.bbox.align("y", bbox, d=10)
                    and bbox.x0 <= line.bbox.x0 <= bbox.x1
                ):
                    return True
            return False

        def get_vobjects(bbox: BBox) -> list[VObject]:
            return bbox.get(page.vobjects, ratio=0.7)

        vobjs = list(vobj for vobj in page.vobjects if vobj.is_table())
        vobjs.sort(key=lambda vobj: vobj.bbox.y1, reverse=True)
        tables: list[list[VObject]] = []
        i = 0
        while i < len(vobjs):
            vobj1 = vobjs[i]
            vobj2 = vobjs[i + 1] if i + 1 < len(vobjs) else None
            if (
                len(v_lines) > 0
                and vobj2
                and 0 <= vobj1.bbox.y0 - vobj2.bbox.y1 <= 20
                and vobj1.bbox.width >= 100
                and vobj1.bbox.height > 50
                and vobj2.bbox.height > 50
                and vobj1.bbox.align("x", vobj2.bbox, d=10)
                and has_v_lines(BBox.join([vobj1.bbox, vobj2.bbox]))
            ):
                # 连接在一起的表格，被识别为了2个或者多个
                # [table1]
                # [title]
                # [table2]
                tables.append(get_vobjects(BBox.join([vobj1.bbox, vobj2.bbox])))
                self._logger.warning(
                    "第%s页，合并识别错误的表格，2个为一个", page.number
                )
                i += 2
            else:
                tables.append([vobj1])
                i += 1

        return tables