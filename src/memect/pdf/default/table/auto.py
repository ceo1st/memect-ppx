

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Final, Sequence

from memect.pdf.base import KDocument, KPage, VObject
from memect.pdf.model import ModelExecutor


class Parser:
    def __init__(self):
        super().__init__()
        self._table_cls: Final = ModelExecutor.get("table_cls")
        self._table_cls_key: Final = "cache/default/table_cls"
    
    def parse(self,doc:KDocument,*,max_workers:int=0):
        # 如果没有水平线和垂直线，按无边框解析
        # 如果有，需要判断是否为完整的有边框表格？

        #通过模型判断是有边框还是无边框，准确度也不是很高，90%左右
        #如果只有水平线，按无边框
        #如果有垂直线，可能只是局部？使用有边框和无边框解析，然后对比结果，每个表格需要解析2次，耗时

        def is_wbk():
            pass
        def is_ybk():
            pass
        
        def parse_page(page: KPage):
            #识别出来的表格位置准确度90%左右，有些粘连在一起的表格，可能需要分成多个，也可能需要合并为一个
            #不一定有什么逻辑，纯作者的爱好，喜欢分开还是合并为一个
            for vobj in page.vobjects:
                if vobj.is_table():
                    pass
            pass
        pass

    def parse_page(self,page:KPage):
        pass

    def parse_table(self,page:KPage,vobj:VObject):
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