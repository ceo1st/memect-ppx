from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Final, Sequence, cast

from memect.base.bbox import BBox
from memect.pdf.base import KCell, KChar, KDocument, KPage
from memect.pdf.model import ModelExecutor


class Parser:
    def __init__(self):
        super().__init__()
        self._table_llm = ModelExecutor.get("table_llm")
        self._table_llm_key: Final = "cache/default/table_llm"
    
    def parse(self,doc:KDocument,*,max_workers:int=0):
        def get_tables(page: KPage):
            items: list[Any] = []
            for vobj in page.vobjects:
                if vobj.is_table():
                    # TODO 如果表格内包含有图片，可以把图片擦除，写上figure_id值
                    # 在解析完毕后，获得cell.text==figure_id => 再匹配图片
                    img = page.crop(vobj.quad)
                    if img is not None:
                        item = (
                            FileInfo(file=img, params={"task": "table"}),
                            vobj.cache,
                        )
                        items.append(item)
            return items

        def parse_page(page: KPage):
            for vobj in page.vobjects:
                if not vobj.is_table():
                    continue
                result = vobj.cache.pop(self._table_llm_key, None)
                if not result:
                    continue
                table = KTable.from_text(page, vobj.quad, result["text"])
                table.vobject = vobj
                if table.row_num > 0 and table.col_num > 0:
                    page.objects.append(table)
                else:
                    # TODO 无法解析，截图？
                    pass

        self._table_llm.parse(doc, self._table_llm_key, handler=get_tables)
        self._do(parse_page, doc.working_pages, max_workers=max_workers)

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

class CellState:
    def __init__(self, cell: KCell):
        super().__init__()
        self.chars: list[KChar] = []
        self.raw_text: str = self.normalize_text(cell.text)

    @property
    def bbox(self) -> BBox:
        assert len(self.chars) > 0
        return BBox.join([c.bbox for c in self.chars])

    @classmethod
    def get(cls, cell: KCell) -> "CellState":
        return cast(CellState, cell.working_state)

    @classmethod
    def normalize_text(cls, text: str) -> str:
        # 全角转化为半角，去掉空格等
        return text


class LLMTable:
    def __init__(self):
        super().__init__()

    def build(self, table: KTable):
        """获得了表格的结构"""
        page: Final = table.page
        pdf_chars = table.bbox.get(page.pdf_chars, ratio=0.8)
        ocr_chars = table.vobject.ocr_chars if table.vobject else []
        chars: Final[list[KChar]] = []

        def get_chars(cell: KCell):
            text = re.sub(r"\s", cell.text, "", re.DOTALL)

        texts: list[str] = []
        for cell in table.cells:
            cell.working_state = CellState(cell)
            texts.append(cell.working_state.text)
        counter = Counter(texts)
        for k, c in counter.items():
            if c == 1:
                # 只出现一次的，先匹配
                pass

        x_axis: list[float] = [0] * (1 + table.col_num)
        y_axis: list[float] = [0] * (1 + table.row_num)
        x_axis[0] = table.bbox.x0
        x_axis[-1] = table.bbox.x1
        y_axis[0] = table.bbox.y1
        y_axis[-1] = table.bbox.y0
        step = table.bbox.width / table.col_num
        for i in range(1, table.col_num):
            x_axis[i] = x_axis[i - 1] + step
        step = table.bbox.height / table.row_num
        for i in range(table.row_num):
            y_axis[i] = y_axis[i - 1] - step

        for cell in table.cells:
            state = CellState.get(cell)
            if not cell.text:
                continue
            # 先获得开始的bbox
            x0 = x_axis[cell.col_index]
            x1 = x_axis[cell.col_index + cell.col_span]
            y1 = y_axis[cell.row_index]
            y0 = y_axis[cell.row_index + cell.row_span]

            # 获得这个区域可能大了，也可能小了
            bbox = BBox(x0, y0, x1, y1)
            cell_chars = bbox.get(chars, ratio=0.5)
            if cell_chars:
                tb = KTextbox.from_objects(cell_chars)
                if tb.text == cell.text:
                    pass

        grid: list[list[Group[KChar]]] = []
        for i in range(table.row_num):
            for j in range(table.col_num):
                pass
