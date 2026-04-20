import asyncio
import base64
import logging
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from typing import Any, Final, Mapping, Self, Sequence, TypeGuard, cast, override

# 可以使用pil或者opencv处理图片
import PIL.Image
from memect.base import utils
from openai import OpenAI
from PIL.Image import Image

from memect.parse.core import strs

from ..core.bbox import BBox
from ..core.xdebugger import XDebugger
from ._base import Block, Char, PageType, Table, Text


class HCell:
    def __init__(self, text: str, row_index: int, col_index: int, row_span: int = 1, col_span: int = 1):
        super().__init__()
        self.text = text
        self.text2 = self.normalize_text(text)
        """归一化后的字符串"""
        self.row_index = row_index
        self.col_index = col_index
        self.row_span = row_span
        self.col_span = col_span
        self.bbox: BBox | None = None
        """该单元格占据的bbox"""
        self.spans: list[Text] = []

    @classmethod
    def normalize_text(cls, s: str) -> str:
        return strs.NText.get(s).normalize(mode='q2b', space='remove').text


type _Grid = list[list[HCell]]


class HTable:
    def __init__(self, row_num: int, col_num: int, cells: Sequence[HCell]):
        super().__init__()
        self.row_num: int = row_num
        self.col_num: int = col_num
        self.cells: Sequence[HCell] = tuple(cells)
        self.bbox: BBox | None = None

        grid: list[list[HCell]] = []
        for i in range(self.row_num):
            row: list[HCell] = [None]*self.col_num  # type: ignore
            grid.append(row)

        for c in self.cells:
            for i in range(c.row_index, c.row_index+c.row_span):
                for j in range(c.col_index, c.col_index+c.col_span):
                    grid[i][j] = c

        self.grid = grid

    def fill(self, block: Block) -> Table | None:
        return None

    def get_row(self, row_index: int) -> list[HCell]:
        row: list[HCell] = []
        for c in self.cells:
            if c.row_index <= row_index < c.row_index+c.row_span:
                row.append(c)
        return row

    def get_column(self, col_index: int) -> list[HCell]:
        column: list[HCell] = []
        for c in self.cells:
            if c.col_index <= col_index < c.col_index+c.col_span:
                column.append(c)
        return column

    def __getitem__(self, key: tuple[int, int]) -> HCell:
        i, j = key
        return self.grid[i][j]

    def adjust_cells(self, block: Block):
        """调整单元格的bbox"""
        self._adjust_rows()
        self._adjust_columns()
        if True:
            block.page.show('cells', objects=[
                            c.bbox for c in self.cells if c.bbox is not None])

    def _adjust_rows(self):
        # 按行计算，获得行的高度
        for i in range(self.row_num):
            row = self.grid[i]
            # 顶部对齐
            row2 = [c for c in row if c.row_index == i]
            bbox = BBox.x_union(row2)
            assert bbox is not None
            for c in row2:
                c.bbox = c.bbox.copy(y1=bbox.y1)
            # 底部对齐
            row2 = [c for c in row if c.row_index+c.row_span-1 == i]
            bbox = BBox.x_union(row2)
            assert bbox is not None
            for c in row2:
                c.bbox = c.bbox.copy(y0=bbox.y0)

    def _adjust_columns(self):
        for i in range(self.col_num):
            column = [row[i] for row in self.grid]
            # 左边对齐
            group = [c for c in column if c.col_index == i]
            bbox = BBox.x_union(group)
            assert bbox is not None
            for c in group:
                c.bbox = c.bbox.copy(x0=bbox.x0)
            # 右边对齐
            group = [c for c in column if c.col_index+c.col_span-1 == i]
            bbox = BBox.x_union(group)
            assert bbox is not None
            for c in group:
                c.bbox = c.bbox.copy(x1=bbox.x1)

    @classmethod
    def from_grid(cls, grid: _Grid) -> Self:
        row_num = len(grid)
        col_num = len(grid[0])
        cells: list[HCell] = []
        for row in grid:
            for cell in row:
                if cell not in cells:
                    cells.append(cell)
        return cls(row_num, col_num, cells)


class PDFTable(HTable):
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger = XDebugger(f'{__module__}.{__qualname__}')

    @override
    def fill(self, block: Block) -> Table | None:
        # 暂时先不考虑单元格包含图片的情况
        debugger = self._debugger.bind(page=block.page.number)
        texts = PDFTextParser().parse(block)
        self._step1(block, texts)
        self._step2(block, texts)
        self.adjust_cells(block)

        from ._wtable import WCell, WObject, WTable
        wcells: list[WCell] = []
        for c in self.cells:
            assert c.bbox is not None
            wcell = WCell(c.bbox)
            # TODO 实际上这些spans可以合并为一个Text
            #wcell.objects.extend(c.spans)
            for span in c.spans:
                wcell.objects.append(WObject(span.bbox,object=span,block=block))
            wcells.append(wcell)

        wtable = WTable(wcells, page=block.page, bbox=block.bbox)
        wtable.beautify()
        if debugger.allow('gui'):
            wtable.show('beautify')

        return wtable.to_table()

    def _step1(self, block: Block, texts: list[Text]):
        """先匹配唯一的"""
        debugger = self._debugger.bind(page=block.page.number)
        cell_groups: dict[str, list[HCell]] = {}
        text_groups: dict[str, list[Text]] = {}
        for cell in self.cells:
            group = cell_groups.setdefault(cell.text2, [])
            group.append(cell)
        for text in texts:
            group = text_groups.setdefault(HCell.normalize_text(text.text), [])
            group.append(text)

        for text, group in cell_groups.items():
            if len(group) == 1:
                group2 = text_groups.get(text)
                if group2 and len(group2) == 1:
                    group[0].spans.append(group2[0])
                    group[0].bbox = BBox.x_union(group[0].spans)
                    texts.remove(group2[0])

        # 然后可以显示cell的bbox的，对于不为None的
        if debugger.allow('gui'):
            block.page.show('step1', objects=[
                            cell.bbox for cell in self.cells if cell.bbox is not None])
        pass

    def _step2(self, block: Block, texts: list[Text]):
        if not texts:
            return

        # 对于剩余的texts
        cells: list[HCell] = []
        for c in self.cells:
            if c.bbox is None:
                cells.append(c)
        if not cells:
            return

        debugger = self._debugger.bind(page=block.page.number)

        texts.sort(key=lambda span: span.bbox[1])

        def get_row_bbox(row_index: int) -> BBox | None:
            row = [c for c in self.grid[row_index]
                   if c.row_span == 1 and c.bbox is not None]
            bbox = BBox.x_union(row)
            if bbox is not None:
                # 获得最大？
                return bbox

            # 如果没有，就计算一个，如
            # ----row1----
            # ----row2---- 根据row1和row3来计算row2
            # ----row3----

            # x0,x1不重要，随便写一个
            if True:
                row_height = 15
                x0, y0, x1, y1 = 0, None, 100, None
                if row_index-1 >= 0:
                    # b1 = concat_bbox(rows[row_index-1].cells)
                    b1 = BBox.x_union(
                        [c for c in self.grid[row_index-1] if c.row_span == 1 and c.bbox is not None])
                    if b1 is not None:
                        y1 = b1[1]+2

                if row_index+1 < self.row_num:
                    # b2 = concat_bbox(rows[row_index+1].cells)
                    b2 = BBox.x_union(
                        [c for c in self.grid[row_index+1] if c.row_span == 1 and c.bbox is not None])
                    if b2 is not None:
                        y0 = b2[3]-2
                else:
                    pass

                if y0 is not None and y1 is not None:
                    return BBox(x0, y0, x1, y1)
                elif y0 is not None:
                    # 往下计算一个y1
                    return BBox(x0, y0, x1, y0+row_height)
                elif y1 is not None:
                    # 往上计算一个y0
                    return BBox(x0, min(0, y1-row_height), x1, y1)
                else:
                    return None

        def get_column_bbox(col_index: int) -> BBox | None:
            # column = self._get_column(col_index)
            column = [row[col_index] for row in self.grid if row[col_index].col_span ==
                      1 and row[col_index].bbox is not None]
            bbox = BBox.x_union(column)
            # 可以为None，目的是获得列的bbox[0],bbox[2]即可
            return bbox

        def get_column_bbox2(col_index: int) -> BBox | None:
            bbox = get_column_bbox(col_index)
            if bbox is not None:
                return bbox
            if col_index-1 >= 0 and col_index+1 < self.col_num:
                bbox1 = get_column_bbox(col_index-1)
                bbox2 = get_column_bbox(col_index+1)
                if bbox1 is not None and bbox2 is not None:
                    bbox = concat_bbox([bbox1, bbox2])
                    assert bbox is not None
                    x0, y0, x1, y1 = bbox
                    x0 = bbox1[2]
                    x1 = bbox2[0]
                    if x1-x0 >= 10:
                        return (x0, y0, x1, y1)

            # 如果依然不能够获得，根据当前的table包含的spans，进行分列，然后进行匹配，这样就可以获得丢失的列
            return None

        def handle(cell: HCell, force: bool = False):
            assert cell.bbox is None
            row_bbox = get_row_bbox(cell.row_index)
            column_bbox = get_column_bbox(cell.col_index)

            if debugger.allow('info'):
                debugger.print((cell.row_index, cell.col_index,
                                cell.text, row_bbox, column_bbox))

            if column_bbox is None and force:
                # 如果不能够存在的，就获得自动计算的，也就是夹在两个列中间的，这样支持因为印章的影响，
                # 倒置个别列没有找到匹配的表头，然后该列有数据，可能都是“-”，无法匹配，就倒置这一列都无法匹配了
                column_bbox = get_column_bbox2(cell.col_index)

            if row_bbox is None or column_bbox is None:
                # 如果不能够获得，就先不处理
                return

            i = 0
            while i < len(texts):
                span = texts[i]
                if span.bbox.over('x', column_bbox, d=5) and span.bbox.over('y', row_bbox, d=5):
                    # [span1,span2,span3] =>如果有多个，可能因为间距大些被分开
                    # 但是如果是分成两行的，这里还没有处理
                    if cell.bbox is not None:
                        # 如果已经有一个以上了，判断是否为
                        # [span1][span2]，如果是，可以允许
                        pass
                    # span.owner = cell
                    cell.spans.append(span)
                    cell.bbox = BBox.x_union(cell.spans)
                    del texts[i]
                    # 先取一个，因为下一个可能是下一行的，如：
                    # [span1][span2] =>这个可以，但是暂时还是不允许，如果需要，在前面判断
                    # [span3] =>这个可能是下一行的
                    break
                else:
                    i += 1

        # 第一行是表头，先不匹配
        # 如果一行已经有至少一个cell匹配了，就可以获得该行的bbox（大概），然后再根据列，
        # 计算出该行其它的cell对应的span（因为错别字，或者表格中有多个相同内容）

        def fix1():
            while True:
                changed = False
                i = 0
                while i < len(cells):
                    cell = cells[i]
                    handle(cell)
                    if cell.bbox is not None:
                        del cells[i]
                        changed = True
                    else:
                        i += 1

                if not changed or len(cells) == 0:
                    break

        def fix2():
            i = 0
            while i < len(cells):
                c = cells[i]
                if c.row_span == 1 and c.col_span == 1:
                    row_index = c.row_index
                    col_index = c.col_index
                    row_bbox = BBox.x_union(
                        [c for c in self.grid[row_index] if c.row_span == 1 and c.bbox is not None])
                    col_bbox = BBox.x_union(
                        [row[col_index] for row in self.grid if row[col_index].col_span == 1 and row[col_index].bbox is not None])
                    print('================>>cccc', c.row_index,
                          c.col_index, row_bbox, col_bbox)
                    if row_bbox and col_bbox:
                        c.bbox = BBox(col_bbox.x0, row_bbox.y0,
                                      col_bbox.x1, row_bbox.y1)
                        del cells[i]
                    else:
                        i += 1
                else:
                    i += 1

            i = 0
            while i < len(cells):
                c = cells[i]
                row_index = c.row_index
                col_index = c.col_index
                if c.row_span == 1 and c.col_span == 1:
                    i += 1
                    continue
                row_cells: list[HCell] = []
                col_cells: list[HCell] = []
                for k in range(c.row_index, c.row_index+c.row_span):
                    row_cells.extend(self.get_row(k))
                for k in range(c.col_index, c.col_index+c.col_span):
                    col_cells.extend(self.get_column(k))
                row_bbox = BBox.x_union(
                    [c for c in row_cells if c.bbox is not None])
                col_bbox = BBox.x_union(
                    [c for c in col_cells if c.bbox is not None])
                if row_bbox and col_bbox:
                    c.bbox = BBox(col_bbox.x0, row_bbox.y0,
                                  col_bbox.x1, row_bbox.y1)
                    del cells[i]
                else:
                    i += 1

        fix1()

        fix2()

        if debugger.allow('info'):
            with debugger.group('grid'):
                for row in self.grid:
                    debugger.console.print([(c.row_index, c.col_index, c.text, c.bbox)
                                            for c in row])

            with debugger.group('cells'):
                for c in self.cells:
                    debugger.console.print(
                        (c.row_index, c.col_index, c.text, c.bbox))

        if debugger.allow('gui'):
            block.page.show('step2', objects=[
                            cell.bbox for cell in self.cells if cell.bbox is not None])

    def _step3(self):
        pass


class PDFTextParser:
    _logger=logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger=XDebugger(f'{__module__}.{__qualname__}')

    def parse(self, block: Block) -> list[Text]:
        # 有3种可能
        # 1.没有任何逻辑的书写顺序，概率很小，要么是pdf的制作工具很烂，要么是先生成了pdf模版，然后再添加数据
        # 2.通过常见工具（如：word，wps等）生成的pdf，书写顺序是按行的，也就是单元格是从左到右书写
        # 3.在设计表格的时候，是按行，但是单元格的内容是合并的，如：
        # [xx][2012年][2013年]
        # [xx][12月]  [6月]
        # 作者在设计这个表格的时候，2个单元格应该是：[2012年12月],[2013年6月]
        # 但是使用了2行设计，书写顺序就变成了：[2012年]->[2013年]->[12月]->[6月]
        # 如果只使用一行设计，书写顺序为：[2012年6月]->[2013年12月]

        # 下面这种典型的n*m表格，列粘连在一起，无法根据间距划分，需要支持这种，就需要增加更多的解析时间
        # local/嘉实bugs/pdfs/issue28.pdf 28-30页等


        debugger = self._debugger.bind(page=block.page.number)

        # TODO 暂时先忽略图片
        chars = block.chars()
        if not chars:
            return []
        page = chars[0].page
        # 水平划分为span
        chars.sort(key=lambda char: char.index)
        spans: list[Text] = []

        while chars:
            t = self._parse_span(chars)
            if t is not None:
                # 如果不是空白字符串
                spans.append(t)

        if debugger.allow('gui'):
            # 显示一个窗口
            page.show('spans', objects=spans, draw_text=False)

        # 再垂直合并，为了简化维护，仅仅支持最严格的方式
        # [t1]
        # ------------- t1可以为独立的单元格，也可能为[t1,t2] or [t1,t2,t3]
        # [t2]  [t4]
        # [t3]
        texts: list[Text] = []
        while spans:
            t = self._parse_text(spans)
            if t.text2:
                texts.append(t)
            else:
                # 空白字符串，忽略
                pass

        if debugger.allow('gui'):
            page.show('texts', objects=texts, draw_text=False)

        return texts

    def _parse_span(self, chars: list[Char]) -> Text | None:
        group: list[Char] = []
        group.append(chars[0])
        for i in range(1, len(chars)):
            char1 = group[-1]
            char2 = chars[i]
            if char1.index+1 != char2.index:
                # 如果为严格的，必须是连续，但是考虑到可能把空格等去掉了
                #
                pass
            # TODO 如何处理空格
            # 先小一点的，先合并靠的很近的

            dy = min(char1.bbox.height, char2.bbox.height)/2
            dy = max(4, dy)
            # 如何处理上下标？
            # 先根据间距划分，多数情况可以，有些粘连在一起的列就无法区分了
            # print('=======>>>',dx,dy,char1.bbox,char2.bbox,char1.bbox.over('y',char2.bbox,d=dy))
            # TODO 对于粘连在一起的如何切断？如：
            # [xxxxx][zzzz] =>这两个粘连在一起了，间距为0，可以通过先初步找到列
            # 现在不需要，可以先使用对象识别出单元格，如果是左对齐或者右对齐的，识别出来的单元格还是很准确的

            # 如果前面是全角字符，后面的可能会重叠比较多，如：
            # “），”，逗号可能往前移动很多
            dx1 = 5
            dx0 = max(2, char1.bbox.width)
            # d=char2.bbox.x0-char1.bbox.x1
            if char1.bbox.over('y', char2.bbox, d=dy) and -dx0 <= char2.bbox.x0-char1.bbox.x1 <= dx1:
                group.append(char2)
            else:
                break

        del chars[0:len(group)]
        return Text.create(objects=group, tl=True).strip()

    def _parse_text(self, spans: list[Text]) -> Text:
        """
        按书写顺序垂直合并
        """
        def test1(group: list[Text], span: Text) -> bool:
            # 需要过滤这种
            # [span1][span2][span3]   => 如下会合并[span3,span4]，导致占据了[span1,span2]的空间
            # [span4--------------]
            b = span.bbox.union(group)
            objs = b.adjust(dx=-2, dy=-2).get(spans, mode='overlap',
                                              filter=lambda obj: obj not in group and obj is not span)
            return len(objs) == 0

        def join(index: int, group: list[Text]) -> bool:
            t1 = group[-1]
            t2 = spans[index]
            # [t1]
            # [t2]
            # print('==========>>>',t1.text,'||',t2.text,t1.bbox,t2.bbox,t1.bbox.over('x',t2.bbox,d=5),-2<=t1.bbox.y0-t2.bbox.y1<=10)
            if t1.bbox.over('x', t2.bbox, d=5) and -2 <= t1.bbox.y0-t2.bbox.y1 <= 10 and test1(group, t2):
                group.append(t2)
                return True
            else:
                return False

        group: list[Text] = []
        group.append(spans[0])
        for i in range(1, len(spans)):
            if not join(i, group):
                break
        del spans[0:len(group)]
        return Text.join(group)


class ImageTable(HTable):
    @override
    def fill(self, block: Block):
        return super().fill(block)


class ModelBase:
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger = XDebugger(f'{__module__}.{__qualname__}')

    def batch(self, blocks: Sequence[Block], *, batch_size: int | None = None) -> list[Table | None]:
        return []

    def parse(self, block: Block) -> Table | None:
        return None
    
    def _parse_html(self, block: Block, html: str) -> list[_Grid]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        # TODO 可能有多个？
        debugger = self._debugger.bind(page=block.page.number)

        def parse_grid(table: Any) -> _Grid | None:
            # 可以先获得这个表格是多少行多少列的
            row_index = 0
            row_num: int = 0
            col_num: int = 0

            trs: list[Any] = []
            for tr in table.find_all('tr'):
                tds: list[Any] = []
                for td in tr.find_all(['td', 'th']):
                    tds.append(td)
                if len(tds) > 0:
                    # TODO monkeyocr可能返回空白的<tr></tr>，忽略这些
                    trs.append(tds)

            for tr in trs:
                # <thead><tr/></thread>
                # <tbody><tr/></tbody>
                # <table><tr/></table>
                row_num += 1
                if row_num == 1:
                    col_num = 0
                    for td in tr:
                        col_span = int(td.get('colspan', 1))
                        row_span = int(td.get('rowspan', 1))
                        col_num += col_span

            if debugger.allow('info'):
                debugger.print(f'table,row_num={row_num},col_num={col_num}')

            grid: list[list[HCell | None]] = []
            for i in range(row_num):
                grid.append([None]*col_num)

            for row_index, tr in enumerate(trs):
                for td in tr:
                    # <tr><td/></tr>
                    # <tr><th/></tr>
                    text = td.text.strip()
                    col_span = int(td.get('colspan', 1))
                    row_span = int(td.get('rowspan', 1))

                    filled = False
                    for col_index in range(col_num):
                        # 找到第一个空的位置填上去
                        if grid[row_index][col_index:col_index+col_span] == [None]*col_span:
                            filled = True
                            cell = HCell(
                                text, row_index=row_index, col_index=col_index, row_span=row_span, col_span=col_span)
                            for i in range(row_index, row_index+row_span):
                                grid[i][col_index:col_index +
                                        col_span] = [cell]*col_span

                            if debugger.allow('info'):
                                debugger.print(
                                    (cell.row_index, cell.col_index, cell.row_span, cell.col_span, cell.text))
                            break

                    if not filled:
                        # 模型返回的html并不是一个正确的table，行列丢失/不一致
                        if debugger.allow('info'):
                            debugger.print(f'返回的表格不是完整的,错误的列:{row_index}')
                        break

            ok = all([cell is not None for row in grid for cell in row])
            if not ok:
                # 模型返回的html table不完整，忽略不处理
                if debugger.allow('info'):
                    debugger.print(f'返回的表格不是完整的，忽略该表格')
                return None

            # 把重复的去掉即可，返回结果
            return grid  # type: ignore

        grids: list[_Grid] = []
        for table in soup.find_all('table'):
            grid = parse_grid(table)
            if grid is None:
                # 只要有一个为无效的，就忽略结果？
                return []
            grids.append(grid)
        return grids

    def _parse_table(self, block: Block, html: str) -> Table | None:
        grids = self._parse_html(block, html)
        if len(grids) != 1:
            # TODO 必须只有一个
            return None

        if block.page.type == PageType.PDF:
            table = PDFTable.from_grid(grids[0])
        else:
            table = ImageTable.from_grid(grids[0])

        # 使用block的chars/figures匹配表格，获得bbox等
        return table.fill(block)
    
class DefaultModel(ModelBase):
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger = XDebugger(f'{__module__}.{__qualname__}')

    def __init__(self, settings: Mapping[str, Any]):
        super().__init__()
        # 如果是使用百炼的api，可以使用专用的接口，设置额外的参数
        # 或者统一使用openai的接口，将来可能会有所改变，因为这个太繁琐了
        # pip install dashscope，阿里云的sdk，支持更多的参数
        # import dashscope
        self._api: Final = OpenAI(
            **settings['api']
        )
        self._params: Final[Mapping[str, Any]] = settings['params']

        self._prompt: Final[str] = settings['prompt']

        self._max_image_size: tuple[int, int] = settings.get(
            'max_image_size', (2000, 2000))
        self._batch_size: int = settings.get('batch_size', 2)

    @override
    def batch(self, blocks: Sequence[Block], *, batch_size: int | None = None) -> list[Table | None]:
        if batch_size is None:
            batch_size = self._batch_size

        def execute(block:Block,image: Image):
            return self._infer(block,image)

        tables: list[Table | None] = []
        images: list[Image] = []
        htmls: list[str | None] = []

        #TODO 后续可以在这里线程中执行裁剪？如果是python>=3.13，可以同时使用多个核心
        #但前提是page.crop需要支持多线程
        blocks:list[Block]=[]
        for item in blocks:
            image = item.page.crop(item.bbox)
            images.append(image)
            blocks.append(item)

        # 可以同时执行多个推理获得结果
        with ThreadPoolExecutor(max_workers=batch_size, thread_name_prefix='wbk_ai') as executor:
            htmls.extend(executor.map(execute,blocks,images))

        # 填充使用单线程
        for block, html in zip(blocks, htmls):
            if html is not None:
                tables.append(self._parse_table(block, html))
            else:
                # 如果为空，如何处理？
                tables.append(None)
        return tables

    @override
    def parse(self, block: Block) -> Table | None:
        """解析表格区域为一个表格对象"""
        img = block.page.crop(block.bbox)
        html = self._infer(block, img)
        if not html:
            return None
        return self._parse_table(block, html)



    def _infer(self, block: Block, file: str | Path | Image) -> str | None:
        """调用模型获得结果"""
        # 分开实现的目的是方便测试，可以仅仅测试大模型的解析结果
        debugger = self._debugger.bind(page=block.page.number)
        prompt = self._prompt
        width, height, image_url = self._load_image(file, self._max_image_size)
        messages: list[Any] = [
            # {"role": "system", "content": [{"type": "text", "text": '你是一个有用的助手'}]},
            {"role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": prompt},
                ],
             }
        ]

        # 使用**参数的方式，ide无法获得准确的返回类型（因为overload）
        from openai.types.chat.chat_completion import ChatCompletion
        completion: ChatCompletion = cast(ChatCompletion, self._api.chat.completions.create(
            messages=messages,
            **self._params,
        ))

        # 有时候返回的<table><tr><td/></tr></table>并不完全正确
        html: str | None = completion.choices[0].message.content
        if debugger.allow('info'):
            debugger.print(completion.usage)
            # 原始的输出即可，不需要任何
            # debugger.console.print(html)
            print(html)
        return html

    def _load_image(self, file: str | Path | Image, max_size: tuple[int, int]) -> tuple[int, int, str]:
        if isinstance(file, Image):
            img = file
        else:
            file = Path(file)
            img = PIL.Image.open(file)
        w, h = img.size
        if w > max_size[0] or h > max_size[0]:
            r = min(max_size[0]/w, max_size[1]/h)
            w = max(1, int(w*r))
            h = max(1, int(h*r))
            file = img.resize((w, h))
        else:
            # 如果满足条件的，直接使用原图即可，减少编码解码的时间
            pass

        if isinstance(file, Image):
            img = file
            with BytesIO() as fp:
                img.save(fp, format='png')
                image_url = self._encode_image(fp.getvalue(), img)
        else:
            image_url = self._encode_image(Path(file).read_bytes(), img)
        return (w, h, image_url)

    def _encode_image(self, data: bytes, img: PIL.Image.Image) -> str:
        format_ = img.format or 'png'
        s = base64.b64encode(data).decode('utf-8')
        return f'data:image/{format_};base64,{s}'


class MinerUModel(ModelBase):
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger=XDebugger(f'{__module__}.{__qualname__}')
    def __init__(self, settings: Mapping[str, Any]):
        super().__init__()
        
        from mineru_vl_utils import MinerUClient
        self._client: Final = MinerUClient(
            **settings['api']
        )

        self._batch_size:Final[int] = settings.get('batch_size',4)

    @override
    def batch(self, items:Sequence[Block], *, batch_size:int|None = None)->list[Table|None]:
        if not items:
            return []
        from mineru_vl_utils.structs import BlockType, ContentBlock
        
        page = items[0].page
        client = self._client
        image = items[0].page.pil_image
        content_blocks:list[ContentBlock]=[]
        debugger=self._debugger.bind(page=page.number)
        for item in items:
            #获得的是相对页面的
            x0,y0,x1,y1=item.bbox.change_origin(page.height)
            x0=x0/page.width
            x1=x1/page.width
            y0=y0/page.height
            y1=y1/page.height
            content_block = ContentBlock(type=BlockType.TABLE,bbox=[x0,y0,x1,y1])
            content_blocks.append(content_block)
        
        #TODO 暂时只能够如此使用，因为client的接口不满足要求
        block_images, prompts, params, indices = client.helper.prepare_for_extract(image,content_blocks)
        #不应该出现这个，因为目前仅仅prepare_for_extract中会处理表格，只是过滤了list等3个
        assert len(block_images)==len(content_blocks)
        
        #True表示使用batch_size
        use_batch_size=True
        if use_batch_size:
            if batch_size is None:
                batch_size = self._batch_size
            task = client.client.aio_batch_predict(block_images,prompts,params,semaphore=asyncio.Semaphore(batch_size))
            outputs= asyncio.run(task)
        else:
            #这里使用了client.client.max_concurrency=100
            outputs = client.client.batch_predict(block_images, prompts, params,None)
        assert len(outputs)==len(content_blocks)

        for idx, output in zip(indices, outputs):
            content_blocks[idx].content = output
        content_blocks = client.helper.post_process(content_blocks)
        
        verbose=True
        if debugger.allow('info') and verbose:
            with debugger.group('blocks'):
                for block in content_blocks:
                    #因为可能有大量的文本切需要复制粘贴测试，就直接使用print了
                    #print(block.type,block.bbox,block.content)
                    debugger.console.print((block.type,block.bbox,block.content))
        
        tables:list[Table|None]=[]
        for block,content_block in zip(items,content_blocks):
            table=None
            if content_block.content is not None:
                table = self._parse_table(block,content_block.content)
            tables.append(table)
        return tables
    
    @override
    def parse(self, block:Block)->Table|None:
        tables = self.batch([block])
        return tables[0]


class Parser:
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger = XDebugger(f'{__module__}.{__qualname__}')

    def __init__(self, settings: Mapping[str, Any]|None=None):
        super().__init__()
        self._settings: Final = settings or utils.settings()['parser']['wbk']
        self._models: dict[str,ModelBase] = {}

        def is_dict(a:Any)->TypeGuard[dict[str,Any]]:
            if isinstance(a,dict):
                return True
            else:
                return False


        #正常只有一个"default":"xxx"，表示这些需要创建
        for key,value in self._settings.items():
            if isinstance(value,str):
                self._models[key]=self._models[value]=self._create_model(value,self._settings[value])
            else:
                #其它的稍后处理
                pass
        for key,value in self._settings.items():
            #abc={'enable':True}  =>指定为True的，才创建
            if is_dict(value) and key not in self._models and value.get('enable',False):
                self._models[key]=self._create_model(key,value)


    def _create_model(self, name: str, settings: Mapping[str, Any]) -> ModelBase:
        self._logger.info('create wbk parser,name=%s',name)
        if settings.get('model') == 'mineru':
            return MinerUModel(settings)
        else:
            return DefaultModel(settings)

    def parse(self,table:Block, *, model_name: str = 'default'):
        # 可以有多个模型的配置，然后使用最合适的一个
        return self._models[model_name].parse(table)
    
    def batch(self,tables:Sequence[Block],*,model_name:str='default'):
        return self._models[model_name].batch(tables)


