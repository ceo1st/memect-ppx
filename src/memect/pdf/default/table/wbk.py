import logging
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from typing import Any, Callable, Final, Literal, Sequence

from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.base.matrix import Matrix
from memect.pdf.base import (
    KCell,
    KDocument,
    KLine,
    KPage,
    KTable,
    VObject,
)
from memect.pdf.model import ModelManager

from .filler import TableFiller
from .ybk import YBKMode


class _Cell:
    """目的是为了方便调整bbox，而且知道原对象"""

    def __init__(self, source: Any):
        super().__init__()

        self.bbox: BBox = source.bbox.large if hasattr(source, "bbox") else source
        self.content_bbox:BBox|None=None
        """如果设置了，表示为单元格的内容bbox"""
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
        raw_cells: Final = [c.bbox for c in cells]
        # 避免重叠
        cells = self._adjust_cells(cells)
        adjusted_cells:Final=[c.bbox for c in cells]
        result = TableFiller().get_objects(vobj)
        chars=list(result.chars[:])
        self._expand_cells(cells, chars, [0.7, 0.5])
        self._expand_cells(cells, list(result.pdf_figures), [0.7, 0.5])
        self._expand_cells(cells, list(result.vobjects), [0.7, 0.5])
        #有时候识别的区域过小，丢失一部分字符，这里再次纠正
        #self._expand_cells(cells,chars,[0.7],dx=10,dy=0)
        expanded_cells:Final=[c.bbox for c in cells]
        self._adjust_items(cells)
        cells = Builder().build(cells)
        if cells:
            bbox = bbox.union(BBox.join2(cells))
        table = Builder().make_table(page,bbox,cells)
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
                    (f"removed_pdf_figures={len(result.removed_pdf_figures)}", result.removed_pdf_figures),
                    (f"vobjects={len(result.vobjects)}", result.vobjects, True),
                    (f"raw_cells={len(raw_cells)}", raw_cells),
                    (f"adjusted_cells={len(adjusted_cells)}", adjusted_cells),
                    (f'expanded_cells={len(expanded_cells)}',expanded_cells),
                    (f"cells={len(cells)}", cells),
                    (f"wbk=({table.row_num},{table.col_num})",table.get_lines())
                ]
            )
        return table

    def _expand_cells(
        self, cells: list[_Cell], objs: list[Any], ratios: Sequence[float],dx:float=0,dy:float=0
    ):
        """确保cell能够塞入对象"""
        for ratio in ratios:
            if not objs:
                break
            for cell in cells:
                cell_bbox = cell.bbox.expand(dx=dx,dy=dy)
                cell_objs = cell_bbox.get(objs, ratio=ratio, remove=True)
                if cell_objs:
                    cb = BBox.join2(cell_objs)
                    cell.bbox = cell.bbox.union(cb)
                    if cell.content_bbox is None:
                        cell.content_bbox=cb
                    else:
                        cell.content_bbox = cell.content_bbox.union(cb)

                if not objs:
                    break


    def _get_cells_by_model(self, page: KPage, index: int, vobj: VObject) -> list[_Cell]:
        # debugger = self._debugger.bind(page=page.number)
        bbox = vobj.bbox
        result = vobj.cache.pop(self._table_det_key, None)
        if not result:
            # 表示无法截图？
            return [_Cell(vobj.bbox)]

        # 获得的结果是相对截图的，还需要进行转化
        width = result["width"]
        height = result["height"]
        # 转化为相对页面的坐标，
        sw = bbox.width / width
        sh = bbox.height / height
        tx = bbox.x0
        ty = bbox.y0
        m = Matrix().lt_to_lb((width, height)).scale(sw, sh).translate(tx, ty)
        cells: list[_Cell] = []
        for cell_bbox in result["cells"]:
            cells.append(_Cell(BBox.from_list(cell_bbox, matrix=m)))
        return cells

    def _adjust_cells(self, cells: Sequence[_Cell]) -> list[_Cell]:
        # 先删除完全包含的？

        def clean_cells(cells: Sequence[_Cell]) -> list[_Cell]:
            cells = list(cells)
            cells.sort(key=lambda cell: cell.bbox.y1, reverse=True)
            i = 0
            while i < len(cells):
                c1 = cells[i].bbox
                j = i + 1
                c1_removed = False
                while j < len(cells):
                    c2 = cells[j].bbox
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

        cells = clean_cells(cells)
        self._adjust_items(cells)
        return cells




    def _adjust_items(self, cells: Sequence[_Cell]):
        # debugger=self._debugger.bind(page=self.table.pages[0].number)
        strict = False

        def adjust(cells: Sequence[_Cell]):
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


class Builder:
    def __init__(self):
        super().__init__()

    def build(self, cells: list[_Cell]) -> list[_Cell]:
        """把模型检测出的cell规整到一个矩形表格网格中。

        处理目标：
        1. 轻微漂移的边界吸附到全局网格线。
        2. 孤立的错误边界如果落在两条稳定网格线之间，吸附到前/后稳定线。
        3. 保留跨行跨列cell，因为它们自然覆盖多个网格区间。
        4. 对网格中没有被任何cell覆盖的位置补空cell。
        """
        cells = [
            cell for cell in cells if cell.bbox.width > 0 and cell.bbox.height > 0
        ]
        if len(cells) < 2:
            return cells

        self._snap_outlier_edges(cells, axis="x")
        self._snap_outlier_edges(cells, axis="y")
        self._snap_to_grid(cells, axis="x")
        self._snap_to_grid(cells, axis="y")
        self._fill_missing(cells)
        return cells

    def make_table(
        self,
        page: KPage,
        table_bbox: BBox,
        cells: Sequence[_Cell],
    ) -> KTable:
        cells = [cell for cell in cells if cell.bbox.width > 0 and cell.bbox.height > 0]
        table = KTable(page, table_bbox, row_num=1, col_num=1)
        if not cells:
            table.cells.append(KCell(page, table_bbox, row_index=0, col_index=0))
            return table

        x_lines = self._axis_lines(cells, axis="x")
        y_lines = self._axis_lines(cells, axis="y")
        if len(x_lines) < 2 or len(y_lines) < 2:
            bbox = BBox.join2(cells)
            table = KTable(page, bbox, row_num=1, col_num=1)
            table.cells.append(KCell(page, bbox, row_index=0, col_index=0))
            return table

        col_num = len(x_lines) - 1
        row_num = len(y_lines) - 1
        table = KTable(page, table_bbox, row_num=row_num, col_num=col_num)

        kcells: list[KCell] = []
        for cell in cells:
            col0 = self._nearest_index(cell.bbox.x0, x_lines)
            col1 = self._nearest_index(cell.bbox.x1, x_lines)
            y0 = self._nearest_index(cell.bbox.y0, y_lines)
            y1 = self._nearest_index(cell.bbox.y1, y_lines)
            if col1 <= col0:
                col1 = col0 + 1
            if y1 <= y0:
                y1 = y0 + 1
            col0 = max(0, min(col0, col_num - 1))
            col1 = max(col0 + 1, min(col1, col_num))
            y0 = max(0, min(y0, row_num - 1))
            y1 = max(y0 + 1, min(y1, row_num))

            row_index = row_num - y1
            row_span = y1 - y0
            col_span = col1 - col0
            bbox = BBox(x_lines[col0], y_lines[y0], x_lines[col1], y_lines[y1])
            kcells.append(
                KCell(
                    page,
                    bbox,
                    row_index=row_index,
                    col_index=col0,
                    row_span=row_span,
                    col_span=col_span,
                )
            )

        kcells.sort(key=lambda cell: (cell.row_index, cell.col_index))
        table.cells.extend(kcells)
        return table

    def _snap_outlier_edges(self, cells: list[_Cell], *, axis: Literal["x", "y"]):
        """把孤立边界吸附到相邻稳定边界，避免形成很窄的伪行/列。"""
        lo_attr, hi_attr = self._axis_attrs(axis)
        tol = self._edge_tolerance(cells, axis)
        clusters = self._cluster_edges(
            [(getattr(cell.bbox, lo_attr), cell, lo_attr) for cell in cells]
            + [(getattr(cell.bbox, hi_attr), cell, hi_attr) for cell in cells],
            tol,
        )
        if len(clusters) < 3:
            return

        stable = [
            i
            for i, cluster in enumerate(clusters)
            if i == 0 or i == len(clusters) - 1 or len(cluster[1]) >= 2
        ]
        # 如果票数不足以判断稳定线，就不要猜。
        if len(stable) < 2:
            return

        stable_set = set(stable)
        for i, (value, members) in enumerate(clusters):
            if i in stable_set:
                continue
            prev_indexes = [j for j in stable if j < i]
            next_indexes = [j for j in stable if j > i]
            if not prev_indexes or not next_indexes:
                continue

            prev_value = clusters[prev_indexes[-1]][0]
            next_value = clusters[next_indexes[0]][0]
            if next_value <= prev_value:
                continue

            ratio = (value - prev_value) / (next_value - prev_value)
            target = next_value if ratio > 0.5 else prev_value
            for _, cell, attr in members:
                self._adjust_edge(cell, attr, target)

    def _snap_to_grid(self, cells: list[_Cell], *, axis: Literal["x", "y"]):
        """把所有边界吸附到聚类后的网格线。"""
        lo_attr, hi_attr = self._axis_attrs(axis)
        tol = self._edge_tolerance(cells, axis)
        clusters = self._cluster_edges(
            [(getattr(cell.bbox, lo_attr), cell, lo_attr) for cell in cells]
            + [(getattr(cell.bbox, hi_attr), cell, hi_attr) for cell in cells],
            tol,
        )
        if len(clusters) < 2:
            return

        lines = [value for value, _ in clusters]
        for cell in cells:
            lo = getattr(cell.bbox, lo_attr)
            hi = getattr(cell.bbox, hi_attr)
            new_lo = min(lines, key=lambda line: abs(line - lo))
            new_hi = min(lines, key=lambda line: abs(line - hi))
            self._adjust_edge(cell, lo_attr, new_lo)
            self._adjust_edge(cell, hi_attr, new_hi)

    def _fill_missing(self, cells: list[_Cell]):
        x_lines = self._axis_lines(cells, axis="x")
        y_lines = self._axis_lines(cells, axis="y")
        if len(x_lines) < 2 or len(y_lines) < 2:
            return

        rows = len(y_lines) - 1
        cols = len(x_lines) - 1
        covered = [[False] * cols for _ in range(rows)]

        for cell in cells:
            c0 = self._nearest_index(cell.bbox.x0, x_lines)
            c1 = self._nearest_index(cell.bbox.x1, x_lines)
            r0 = self._nearest_index(cell.bbox.y0, y_lines)
            r1 = self._nearest_index(cell.bbox.y1, y_lines)
            if c1 <= c0:
                c1 = c0 + 1
            if r1 <= r0:
                r1 = r0 + 1
            c0 = max(0, min(c0, cols - 1))
            c1 = max(c0 + 1, min(c1, cols))
            r0 = max(0, min(r0, rows - 1))
            r1 = max(r0 + 1, min(r1, rows))
            cell.bbox = BBox(x_lines[c0], y_lines[r0], x_lines[c1], y_lines[r1])
            for r in range(r0, r1):
                for c in range(c0, c1):
                    covered[r][c] = True

        visited = [[False] * cols for _ in range(rows)]
        for r in range(rows):
            for c in range(cols):
                if covered[r][c] or visited[r][c]:
                    continue
                c_end = c
                while (
                    c_end < cols
                    and not covered[r][c_end]
                    and not visited[r][c_end]
                ):
                    c_end += 1
                r_end = r + 1
                while r_end < rows:
                    if any(
                        covered[r_end][cc] or visited[r_end][cc]
                        for cc in range(c, c_end)
                    ):
                        break
                    r_end += 1

                for rr in range(r, r_end):
                    for cc in range(c, c_end):
                        visited[rr][cc] = True
                cells.append(
                    _Cell(
                        BBox(x_lines[c], y_lines[r], x_lines[c_end], y_lines[r_end])
                    )
                )

    def _axis_attrs(self, axis: Literal["x", "y"]) -> tuple[str, str]:
        if axis == "x":
            return "x0", "x1"
        return "y0", "y1"

    def _axis_lines(
        self, cells: list[_Cell], *, axis: Literal["x", "y"]
    ) -> list[float]:
        lo_attr, hi_attr = self._axis_attrs(axis)
        clusters = self._cluster_edges(
            [(getattr(cell.bbox, lo_attr), cell, lo_attr) for cell in cells]
            + [(getattr(cell.bbox, hi_attr), cell, hi_attr) for cell in cells],
            self._edge_tolerance(cells, axis),
        )
        return [value for value, _ in clusters]

    def _adjust_edge(self, cell: _Cell, attr: str, target: float):
        if cell.content_bbox is not None:
            if attr in ("x0", "y0"):
                target = min(target, getattr(cell.content_bbox, attr))
            else:
                target = max(target, getattr(cell.content_bbox, attr))
        bbox = cell.bbox.adjust(**{attr: target})
        if bbox.width > 0 and bbox.height > 0:
            cell.bbox = bbox

    def _edge_tolerance(self, cells: list[_Cell], axis: Literal["x", "y"]) -> float:
        lo_attr, hi_attr = self._axis_attrs(axis)
        sizes = sorted(
            getattr(cell.bbox, hi_attr) - getattr(cell.bbox, lo_attr)
            for cell in cells
            if getattr(cell.bbox, hi_attr) > getattr(cell.bbox, lo_attr)
        )
        if not sizes:
            return 2.0
        base = sizes[len(sizes) // 4]
        median = sizes[len(sizes) // 2]
        return max(2.0, min(base * 0.25, median * 0.12))

    def _cluster_edges(
        self,
        values: list[tuple[float, _Cell, str]],
        tolerance: float,
    ) -> list[tuple[float, list[tuple[float, _Cell, str]]]]:
        if not values:
            return []
        values = sorted(values, key=lambda item: item[0])
        clusters: list[list[tuple[float, _Cell, str]]] = [[values[0]]]
        for item in values[1:]:
            if item[0] - clusters[-1][-1][0] <= tolerance:
                clusters[-1].append(item)
            else:
                clusters.append([item])

        result: list[tuple[float, list[tuple[float, _Cell, str]]]] = []
        for cluster in clusters:
            cluster_values = [item[0] for item in cluster]
            value = sum(cluster_values) / len(cluster_values)
            result.append((value, cluster))
        return result

    def _nearest_index(self, value: float, lines: list[float]) -> int:
        return min(range(len(lines)), key=lambda i: abs(lines[i] - value))
