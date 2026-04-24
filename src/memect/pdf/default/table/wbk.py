from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
import logging
from typing import Any, Callable, Final, Sequence, TypeGuard

from memect.base import images
from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.base.matrix import Matrix
from memect.pdf.base import (
    KChar,
    KDocument,
    KFigure,
    KLine,
    KObject,
    KPDFFigure,
    KPage,
    KTable,
    KTextbox,
    VObject,
)

from .ybk import YBKMode
from memect.pdf.model import ModelManager
from memect.pdf.sort import Sorter
from .builder import TableBuilder
from .filler import TableFiller


class _Item:
    """目的是为了方便调整bbox，而且知道原对象"""

    def __init__(self, source: Any):
        super().__init__()

        self.bbox: BBox = source.bbox.large if hasattr(source, "bbox") else source
        self.source: Final = source


class WBKMode(StrEnum):
    ALL = "all"
    """所有的表格都使用无边框解析"""
    AUTO = "auto"
    """如果有pdf的线，且结构接近，就使用有边框的结构"""


class Parser:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    def __init__(self, manager: ModelManager):
        super().__init__()
        from .ybk import Parser

        self._table_det: Final = manager.get("table_det")
        self._table_det_key: Final = "cache/default/wbk/table_det"
        self._ybk_parser: Final = Parser()

    def parse(
        self, doc: KDocument, *, max_workers: int = 0, mode: WBKMode = WBKMode.ALL
    ):
        def get_tables(page: KPage):
            tables: list[Any] = []
            for vobj in page.vobjects:
                if vobj.is_table():
                    # TODO 如果是有边框表格，可以考虑稍微大一点，包含表格线？
                    img = page.crop(vobj.bbox)
                    if img:
                        tables.append((img, vobj.cache))
                    else:
                        # bbox是无效的？
                        pass

            return tables

        def parse_page(page: KPage):
            return self._parse_page(page, mode=mode)

        self._table_det.parse(doc, self._table_det_key, handler=get_tables)
        self._do(parse_page, doc.working_pages, max_workers=max_workers)

    def _parse_page(self, page: KPage, mode: WBKMode):
        i = 0
        for vobj in page.vobjects:
            if vobj.is_table():
                table = self._parse_table(page, i, vobj, mode=mode)
                page.objects.append(table)
                i += 1

    def _parse_table(self, page: KPage, index: int, vobj: VObject, mode: WBKMode):
        debugger = self._debugger.bind(page=page.number)

        def use_ybk(ybk: KTable, wbk: KTable) -> bool:
            if ybk.row_num >= wbk.row_num and ybk.col_num >= wbk.col_num:
                return True
            # 如果结构不一致，使用哪一个呢？
            # ybk有两种可能：完整有边框，局部有边框
            # 如果是完整有边框，wbk解析出来的行列应该是基本一致的（当然个别表格存在解析错误）
            # 如果是局部有边框，wbk解析出来的行列会多一些
            return False

        steps: list[Any]|None = None
        if debugger.allow('draw'):
            steps=[]
        wbk_table = self._parse_wbk(page, index, vobj,steps=steps)
        table = wbk_table
        beautify = True
        if mode == WBKMode.AUTO:
            ybk_table = self._parse_ybk(page, index, vobj)
            if ybk_table is not None:
                if steps is not None:
                    steps.append(
                        (
                            f"ybk=({ybk_table.row_num},{ybk_table.col_num})",
                            ybk_table.get_lines(),
                        )
                    )
                if use_ybk(ybk_table, wbk_table):
                    table = ybk_table
                    beautify = False

        # 如果之前已经获得对象了（因为各种需要）
        # 之所以在这里再填充对象，只是避免表格内的图片/公式等多次解析，不影响什么
        result = table.cache.pop("result", None)
        result = TableFiller().fill(table, result)

        if beautify:
            self._beautify(table)

        # TODO 如何显示
        if steps is not None:
            steps.append((f"remain_objects={len(result.remain_objects)}",result.remain_objects))
            steps.append((f"{table.subtype}", table.get_lines()))
            page.draw(
                *steps,
                index=index,
                dir="debug/default/wbk/table",
                show_type=False,
                line_width=4,
            )
        return table

    def _parse_ybk(self, page: KPage, index: int, vobj: VObject) -> KTable|None:
        # 仅仅当有PDF的线的时候，按有边框解析才有很高的准确度，如果是使用图片的线，就不如直接无边框解析
        #还可以更加快速的判断，如果一条垂直线都没有的？
        lines = vobj.bbox.get(page.pdf_lines,ratio=0.7)
        h_lines,v_lines = KLine.split(lines)
        if not v_lines:
            return None
        return self._ybk_parser.parse_table(
            page, index, vobj, fill=False, mode=YBKMode.PDF
        )

    def _parse_wbk(
        self, page: KPage, index: int, vobj: VObject, steps: list[Any]|None=None
    ) -> KTable:
        #debugger = self._debugger.bind(page=page.number)
        bbox = vobj.bbox
        cells = self._get_cells_by_model(page, index, vobj)
        raw_cells: Final = list(cells)
        # 避免重叠
        cells = self._adjust_cells(cells)
        result = TableFiller().get_objects(vobj)
        self._expand_cells(cells, result.chars, [0.7, 0.5])
        self._expand_cells(cells, result.pdf_figures, [0.7, 0.5])
        self._expand_cells(cells, result.vobjects, [0.7, 0.5])
        bbox = bbox.union(BBox.join(cells))
        table = TableBuilder().build(page, bbox, cells)
        table.vobject = vobj
        table.subtype = "wbk"
        table.cache["result"] = result

        if steps is not None:
            steps.extend(
                [
                    ("page", None),
                    ("table", [vobj]),
                    (f"pdf_chars={len(result.pdf_chars)}", result.chars),
                    (f"ocr_chars={len(result.ocr_chars)}", result.ocr_chars),
                    (f"removed_chars={len(result.removed_chars)}",result.removed_chars),
                    (f"pdf_figures={len(result.pdf_figures)}", result.pdf_figures),
                    (f"vobjects={len(result.vobjects)}", result.vobjects, True),
                    (f"raw_cells={len(raw_cells)}", raw_cells),
                    (f"cells={len(cells)}", cells),
                    (f"wbk=({table.row_num},{table.col_num})",table.get_lines())
                ]
            )
        return table

    def _expand_cells(
        self, cells: list[BBox], objs: Sequence[Any], ratios: Sequence[float]
    ):
        """确保cell能够塞入对象"""
        objs = list(objs)
        for ratio in ratios:
            if not objs:
                break
            for i, cell in enumerate(cells):
                cell_objs = cell.get(objs, ratio=ratio, remove=True)
                if cell_objs:
                    cells[i] = cell.union(BBox.join2(cell_objs))
                if not objs:
                    break

    def _fill_cells(self, table: KTable, objs: list[KObject | VObject]):
        def remove_spaces(objs: Sequence[KObject]) -> list[KObject]:
            new_objs: list[KObject] = []
            for obj in objs:
                if isinstance(obj, KChar) and obj.text.isspace():
                    pass
                else:
                    new_objs.append(obj)
            return new_objs

        # >=3.13才能够使用TypeIs
        def is_chars(objs: Sequence[Any]) -> TypeGuard[Sequence[KChar]]:
            return all(isinstance(obj, KChar) for obj in objs)

        def is_figures(objs: Sequence[Any]) -> TypeGuard[Sequence[KFigure]]:
            return all(isinstance(obj, KFigure) for obj in objs)

        def is_vobjects(objs: Sequence[Any]) -> TypeGuard[Sequence[VObject]]:
            return all(isinstance(obj, VObject) for obj in objs)

        for cell in table.cells:
            if not objs:
                break
            assert cell.bbox is not None
            # TODO 单元格也可以存在复杂的布局，如果是这样，又需要进行一个单元格的版面分析
            # 目前仅仅支持简单的格式，要么为纯文本，要么为图片
            # 如果是文本和图片混合，如
            # --text1----
            # --figure1--
            # --figure2--
            # --text2----
            cell_objs = cell.bbox.get(objs, ratio=0.7, remove=True)
            if not cell_objs:
                continue

            new_cell_objs: list[KObject] = []
            for obj in cell_objs:
                if isinstance(obj, KPDFFigure | VObject):
                    # 都使用图片即可，如果是vobject的，可以再考虑公式等？
                    obj = obj.make_figure()
                new_cell_objs.append(obj)

            # 空格先暂时去掉，混合在图片中，没有意义
            valid_objs = remove_spaces(new_cell_objs)
            if is_chars(new_cell_objs):
                # 全部都是字符
                tb = KTextbox.from_objects(new_cell_objs)
                cell.objects.append(tb)
                cell.text = tb.text
            elif is_figures(valid_objs):
                # 都是图片
                for obj in valid_objs:
                    cell.objects.append(obj)
            else:
                # 图文并茂，简单的从上到下分行即可
                groups: list[tuple[str, list[KObject]]] = []
                for line in Sorter.get_lines(new_cell_objs):
                    # 先去掉前后的空格字符
                    valid_line = remove_spaces(line)
                    if is_figures(valid_line):
                        groups.append(("figure", list(valid_line)))
                    else:
                        if not groups or groups[-1][0] != "char":
                            groups.append(("char", list(line)))
                        else:
                            groups[-1][1].extend(line)

                for group in groups:
                    if group[0] == "figure":
                        cell.objects.extend(group[1])
                    else:
                        tb = KTextbox.from_objects(group[1])
                        cell.objects.append(tb)
                        cell.text += tb.text

    def _get_cells(self, page: KPage, index: int, vobj: VObject) -> list[BBox]:
        if not vobj.ocr_chars:
            pass
        pass

    def _get_cells_by_model(self, page: KPage, index: int, vobj: VObject) -> list[BBox]:
        # debugger = self._debugger.bind(page=page.number)
        bbox = vobj.bbox
        result = vobj.cache.pop(self._table_det_key, None)
        if not result:
            # 表示无法截图？
            return [vobj.bbox]

        # 获得的结果是相对截图的，还需要进行转化
        width = result["width"]
        height = result["height"]
        # 转化为相对页面的坐标，
        sw = bbox.width / width
        sh = bbox.height / height
        tx = bbox.x0
        ty = bbox.y0
        m = Matrix().lt_to_lb((width, height)).scale(sw, sh).translate(tx, ty)
        cells: list[BBox] = []
        for cell_bbox in result["cells"]:
            cells.append(BBox.from_list(cell_bbox, matrix=m))
        return cells

    def _adjust_cells(self, cells: Sequence[BBox]) -> list[BBox]:
        # 先删除完全包含的？

        def clean(cells: Sequence[BBox]) -> list[BBox]:
            cells = list(cells)
            cells.sort(key=lambda cell: cell.y1, reverse=True)
            i = 0
            while i < len(cells):
                c1 = cells[i]
                j = i + 1
                c1_removed = False
                while j < len(cells):
                    c2 = cells[j]
                    if c2.y1 <= c1.y0:
                        break
                    xb = c1.intersect(c2)
                    # 已经按面积排序，所以不需要再比较哪一个面积更大
                    if xb and xb.area / min(c1.area, c2.area) >= 0.7:
                        if c1.area > c2.area:
                            del cells[j]
                        else:
                            del cells[i]
                            c1_removed = True
                            break
                    else:
                        j += 1
                if not c1_removed:
                    i += 1
            return cells

        cells = clean(cells)
        items: list[_Item] = [_Item(cell) for cell in cells]
        self._adjust_items(items)
        return [item.bbox for item in items]

    def _adjust_items(self, cells: Sequence[_Item]):
        # debugger=self._debugger.bind(page=self.table.pages[0].number)
        strict = False

        def adjust(cells: Sequence[_Item]):
            cells = sorted(cells, key=lambda cell: cell.bbox.y1, reverse=True)
            for i in range(len(cells)):
                c1 = cells[i]
                for j in range(i + 1, len(cells)):
                    c2 = cells[j]
                    # 不允许刚好重叠的，就设置为
                    dy = c2.bbox.y1 - c1.bbox.y0
                    # 如果dy==0，也就是c1,c2粘连在一起，在这种情况，也需要调整上下，否则无法画线？
                    # [c1]
                    # [c2]
                    if dy <= 0:
                        # 不需要再继续了，也可以使用"<"
                        break

                    # 如果完全包含
                    if c1.bbox.expand(dx=3, dy=3).contains(c2.bbox) or c2.bbox.expand(
                        dx=3, dy=3
                    ).contains(c1.bbox):
                        continue
                    area = c1.bbox.intersect(c2.bbox)
                    if area is None:
                        continue

                    if area.width >= area.height:
                        # [--c1--]
                        #  [--c2--]
                        # 水平重叠的多，调整y
                        if c1.bbox.height > c2.bbox.height:
                            c1.bbox = c1.bbox.adjust(y0=c2.bbox.y1 + 1)
                        else:
                            c2.bbox = c2.bbox.adjust(y1=c1.bbox.y0 - 1)
                    else:
                        #      [--c3--]
                        # [--c4--]
                        # 垂直重叠的多，调整x即可
                        if c1.bbox.x1 < c2.bbox.x1:
                            c3, c4 = c2, c1
                        else:
                            c3, c4 = c1, c2
                        if c3.bbox.width > c4.bbox.width:
                            c3.bbox = c3.bbox.adjust(x0=c4.bbox.x1 + 1)
                        else:
                            c4.bbox = c4.bbox.adjust(x1=c3.bbox.x0 - 1)

                    # 不能够break，可能还和其他的重叠
                    if strict and (c1.bbox.area == 0 or c2.bbox.area == 0):
                        raise RuntimeError("程序写错了")

        adjust(cells)

    def _get_lines(self, table: KTable):
        pdf_lines = table.bbox.get(table.page.pdf_lines, ratio=0.7)
        if len(pdf_lines) > 0:
            return pdf_lines
        # 表示为图片，识别页面的线？
        from .lineparser import TableLineParser

        # TableLineParser().parse()
        page = table.page
        p = table.bbox.transform(page.to_lt())
        tx = -p[0]
        ty = -p[1]
        sw = page.image.width / page.width
        sh = page.image.height / page.height
        m = page.to_lt().translate(tx, ty).scale(sw, sh)
        img = page.crop(table.bbox)
        assert img is not None
        content_bboxes: list[tuple[float, float, float, float]] = []
        assert table.vobject is not None
        for cell in table.cells:
            if cell.objects:
                content_bboxes.append(BBox.join2(cell.objects).transform(m).to_tuple())
        TableLineParser().parse(
            images.pil_to_cv2(img), content_bboxes=content_bboxes, debug=True
        )

    def _beautify(self, table: KTable):
        pass

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
