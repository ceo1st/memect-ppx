from typing import Final, TypeGuard

from memect.pdf.base import KDocument
from .xbase import XObject, XTable, XTree


class XTableParser:
    """跨页/跨栏表格合并"""

    def __init__(self):
        super().__init__()

    def parse(self, xtree: XTree):
        doc: Final = xtree.doc
        xobjects: Final = xtree.xobjects
        i = 0
        while i < len(xobjects):
            j = self._parse_once(doc, i, xobjects)
            if j == -1:
                i += 1
            else:
                i = j

    def _parse_once(self, doc: KDocument, index: int, xobjects: list[XObject]) -> int:
        """"""
        #这里只需要判断表格是否可以合并，然后进行合并即可
        #表格在按页解析的时候，已经对齐
        if index + 1 < len(xobjects):
            return -1
        xobj1 = xobjects[index]
        xobj2 = xobjects[index + 1]
        if not all([isinstance(xobj,XTable) for xobj in [xobj1,xobj2]]):
            return -1
        
        def is_ybk(xobj:XObject)->TypeGuard[XTable]:
            if isinstance(xobj,XTable) and xobj.subtype=='ybk':
                return True
            else:
                return False
            
        def is_wbk(xobj:XObject)->TypeGuard[XTable]:
            if isinstance(xobj,XTable) and xobj.subtype=='wbk':
                return True
            else:
                return False

        #1.判断是否可以合并
        #2.合并
        
        return -1
