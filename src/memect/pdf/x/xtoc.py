
from typing import Sequence

from memect.pdf.base import KTextline
from memect.pdf.x.xbase import XObject, XText


class XTOCParser:
    def __init__(self):
        super().__init__()
    
    def parse(self,xobjects:Sequence[XObject]):
        i=0
        while i<len(xobjects):
            xobj=xobjects[i]
            lines:list[KTextline]=[]
            if isinstance(xobj,XText):
                for t in xobj.texts:
                    lines.extend(t.lines)
                i+=1
            else:
                i+=1
                #拆开，然后再重新合并，如：
                #第一章xxxxxxxxxxxx (textlin1)
                #  xxxx...........1(textlin1)
                #  1.xxxxx........2(textlin2)
                #  2.xxxxx........3(textlin3)
                #合并为text1=[textline1,textline2]
                #text2=[textline1]
                #text3=[textline2]
        pass
