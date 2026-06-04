import logging
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum, auto
from typing import Callable, Sequence

from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.pdf.base import KDocument, KLine, KPage, KTable, VObject
from memect.pdf.default.table.filler import TableFiller

from .line import Liner


class YBKMode(StrEnum):
    AUTO = auto()
    """所有的表格都按有边框解析，自动选择pdf的线还是图片的线"""
    PDF = auto()
    """使用PDF线的解析，如果没有pdf的线，返回1*1的表格"""
    IMAGE = auto()
    """总是使用图片的方式解析线"""


class Parser:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    def __init__(self):
        super().__init__()

    def parse(
        self, doc: KDocument, *, max_workers: int = 0, mode: YBKMode = YBKMode.AUTO
    ):
        def parse_page(page: KPage):
            self._parse_page(page, mode)

        self._do(parse_page, doc.working_pages, max_workers=max_workers)

    def _parse_page(self, page: KPage, mode: YBKMode):
        """"""
        i = 0
        for vobj in page.vobjects:
            if vobj.is_table():
                table = self.parse_table(page, i, vobj, mode=mode)
                page.objects.append(table)
                i += 1

    def parse_table(
        self,
        page: KPage,
        index: int,
        vobj: VObject,
        fill: bool = True,
        mode: YBKMode = YBKMode.AUTO,
    ) -> KTable:
        """解析有边框表格
        page:
        index: 表格的index，为了输出图片等方便
        vobj:
        mode：解析方式
        fill：True表示填充对象，False表示先设置到table.cache['objects']中
        """
        debugger = self._debugger.bind(page=page.number)
        bbox = vobj.bbox
        # 获得来自pdf或者图片的线
        source, h_lines, v_lines = self._parse_lines(
            page, bbox.expand(dx=2, dy=3, bound=page.bbox), mode
        )
        raw_lines = h_lines + v_lines
        # 再清理一下，避免误差，更好的解析表格
        table_lines = Liner().parse([line.bbox for line in raw_lines], page=page)
        table_bbox = bbox
        if table_lines:
            # 因为没有包括线的宽度，所以这里大一点
            bbox2 = BBox.join(table_lines)
            if bbox2.expand(dx=5, dy=5).contains(bbox):
                table_bbox = bbox2
            else:
                # 虽然识别了线，但是可能只是局部的，也抛弃
                table_lines = []

        if not table_lines:
            # 理解为单行单列的表格，或者截图？
            table_lines = [
                (table_bbox[0], table_bbox[1], table_bbox[2], table_bbox[1]),
                (table_bbox[2], table_bbox[1], table_bbox[2], table_bbox[3]),
                (table_bbox[0], table_bbox[3], table_bbox[2], table_bbox[3]),
                (table_bbox[0], table_bbox[1], table_bbox[0], table_bbox[3]),
            ]

        table = KTable.from_lines(page, table_lines)
        table.subtype='ybk'
        table.vobject = vobj
        filler = TableFiller()
        result = filler.get_objects(vobj)
        if fill:
            filler.fill(table, result)
        else:
            # 暂时不填充
            table.cache["result"] = result

        if debugger.allow("draw"):
            page.draw(
                ("page", None),
                ("table", [vobj]),
                (f"pdf_chars={len(result.pdf_chars)}", result.pdf_chars),
                (f"ocr_chars={len(result.ocr_chars)}", result.ocr_chars),
                (f"pdf_figures={len(result.pdf_figures)}", result.pdf_figures),
                (f"vobjects={len(result.vobjects)}", result.vobjects, True),
                (f"remain_objects={len(result.remain_objects)}", result.remain_objects),
                (f"{source}_lines={len(raw_lines)}", raw_lines),
                (
                    f"table_lines={len(table_lines)}",
                    [table_bbox.expand(dx=1, dy=1)]
                    + [KLine(page, BBox.from_list(line)) for line in table_lines],
                ),
                dir="debug/default/ybk",
                index=index,
                show_type=False,
                line_width=4
            )

        return table

    def _parse_lines(
        self, page: KPage, bbox: BBox, mode: YBKMode
    ) -> tuple[str, list[KLine], list[KLine]]:
        if mode == YBKMode.PDF:
            h_lines, v_lines = self._parse_pdf_lines(page, bbox)
            return "pdf", h_lines, v_lines
        elif mode == YBKMode.IMAGE:
            h_lines, v_lines = self._parse_image_lines(page, bbox)
            return "image", h_lines, v_lines
        elif mode == YBKMode.AUTO:
            h_lines, v_lines = self._parse_pdf_lines(page, bbox)
            if h_lines or v_lines:
                return "pdf", h_lines, v_lines
            h_lines, v_lines = self._parse_image_lines(page, bbox)
            return "image", h_lines, v_lines
        else:
            raise ValueError()

    def _parse_pdf_lines(
        self, page: KPage, bbox: BBox
    ) -> tuple[list[KLine], list[KLine]]:
        def clean1(h_lines:list[KLine],v_lines:list[KLine]):
            v_lines.sort(key=lambda line:line.bbox.y1,reverse=True)
            if len(h_lines)>=2 and page.bbox.y1-bbox.y1<=100:
                #可能和页眉线相邻
                h_lines.sort(key=lambda line:line.bbox.y1,reverse=True)
                line1=h_lines[0]
                line2=h_lines[1]
                if line1.bbox.y0-line2.bbox.y0<=5:
                    #--------line1---
                    #--------line2---
                    del h_lines[0]
                    self._logger.warning('第%s页，删除和表格粘连的页眉线,line1=%s,line2=%s',page.number,line1.bbox,line2.bbox)

            if len(h_lines)>=2 and bbox.y0<=100:
                h_lines.sort(key=lambda line:line.bbox.y0,reverse=False)
                line1=h_lines[0]
                line2=h_lines[1]
                if line2.bbox.y0-line1.bbox.y0<=5:
                    #--------line2----
                    #--------line1----
                    del h_lines[0]
                    self._logger.warning('第%s页，删除和表格粘连的页脚线,line1=%s,line2=%s',page.number,line1.bbox,line2.bbox)

        def clean2(lines:list[KLine],is_h:bool):
            #清除双层的线，如：
            #------------------line1
            # ---------------line2
            if len(lines)<2:
                return
            
            if is_h:
                x0,y0,x1,y1=0,1,2,3
            else:
                x0,y0,x1,y1=1,0,3,2
            
            lines.sort(key=lambda line:line.bbox[y1],reverse=True)
            i=0
            while i+1<len(lines):
                line1=lines[i].bbox
                line2=lines[i+1].bbox
                #如果线很粗的，可以放大，如：4
                dx=3
                dy=2
                if line1[x1]-line1[x0]>=20 and abs(line1[x0]-line2[x0])<=dx and abs(line1[x1]-line2[x1])<=dx and line1[y1]-line2[y1]<=dy:
                    if line1[x1]-line1[x0]>=line2[x1]-line2[x0]:
                        del lines[i+1]
                    else:
                        del lines[i]
                else:
                    i+=1
            
            pass
        #TODO 还需要去掉下划线，否则可能会把下划线识别为表格线，如：
        # xx____下划线
        #-------表格线
        lines = bbox.get(page.pdf_lines, ratio=0.5)
        h_lines,v_lines=KLine.split(lines)

        clean2(h_lines,True)
        clean2(v_lines,False)
        #TODO 如果有页眉线页脚线，可能会重叠或者相邻，需要先去掉，避免多识别一列
        clean1(h_lines,v_lines)
        return h_lines,v_lines

    def _parse_image_lines(
        self, page: KPage, bbox: BBox
    ) -> tuple[list[KLine], list[KLine]]:
        # TODO 暂时没有实现
        return [], []

    def _fix1(self, page: KPage):
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
