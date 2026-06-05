import logging
import re
from typing import Any, Sequence

from memect.pdf.base import PDFNode
from memect.pdf.x.xbase import XNode, XObject, XText, XTree


class Parser:
    """根据pdf的outlines构建章节树"""

    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self,args:Any|None=None):
        super().__init__()

    def parse(self, xtree: XTree):
        toc = xtree.doc.pdf_toc
        if not toc or toc.size == 0:
            return
        self._fill_titles(xtree, xtree.root, toc)
        # 再挂靠剩余对象
        self._fill_others(xtree, xtree.root)
        # print(xtree.root.stringify())

    def _fill_titles(self, xtree: XTree, xnode: XNode, node: PDFNode):
        last_node = xnode
        for child in node.children:
            assert child.page_number>0
            index = (
                0 if last_node.is_root() else xtree.xobjects.index(last_node.object) + 1
            )
            xtitle = self._find_title(child, index, xtree.xobjects)
            if xtitle is None:
                self._logger.warning(
                    "无法找到page=%s,title=%s,to=%s",
                    child.page_number,
                    child.title,
                    child.point,
                )
                continue
            xnode.add(xtitle)
            self._fill_titles(xtree, xtitle.node, child)
            last_node = xtitle.node.flat()[0]

    def _find_title(
        self, node: PDFNode, index: int, xobjects: Sequence[XObject]
    ) -> XText | None:
        for i in range(index, len(xobjects)):
            xobj = xobjects[i]
            if not isinstance(xobj, XText):
                continue
            if self._is_title(node, xobj):
                xobj.as_title()
                return xobj
        return None

    def _is_title(self, node: PDFNode, xobj: XText) -> bool:
        # 严格的是node.page_number==xobj.page_numbers[0]
        if node.page_number != xobj.page_numbers[0]:
            return False
        # 或者放更宽一些？
        # 文字也比较一下？
        def normalize(s: str) -> str:
            #私有区分的字符
            #s = re.sub(r'^[\u2700-\u27bf]','',s)
            #s = re.sub(r'^[\u25a0-\u25ff]','',s)
            #s = re.sub(r'^[\uE000-\uF8FF]','',s)
            return re.sub(r"[\s]", "", s)

        def cmp_text() -> bool:
            # 可能存在错别字
            # 如果是通过ocr识别的，还可能存在标点符号识别不一致等
            # 对于wingdings，使用私有unicode，在解析的时候会进来转换为标准的（如果可以，也就是看起来一致的）
            # 但是outline中的没有转换，在这里比较，就会出现不一致，通常为第一个字符
            # 简单的做法也是需要在这里进行同样的转换？或者根本就不比较第一个字符了？
            s1=normalize(xobj.text)
            s2=normalize(node.title)
            if s1==s2:
                return True
            
            if len(s1)==len(s2) and len(s1)>=5 and s1[1:]==s2[1:]:
                #去掉第一个私有区域字符
                return True
            return False

        def cmp_position() -> bool:
            # 可能会比bbox高一些
            assert node.point is not None
            point = node.point
            bbox = xobj.objects[0].bbox
            if bbox.y0 <= point[1] <= bbox.y1 + 30:
                # 不需要比较x，因为point表示为滚动到哪个地方而已
                return True
            else:
                return False

        if cmp_text() and cmp_position():
            # 文字一致，位置一致
            return True
        return False

    def _fill_others(self, xtree: XTree, node: XNode):
        """把没有用到的xobjects，挂靠在node上"""
        xobjects = xtree.xobjects
        end_node: XNode | None = None

        start = 0 if node.is_root() else xobjects.index(node.object) + 1
        end: int = len(xobjects)
        if node.size > 0:
            # 如果有子
            end_node = node.children[0]
        else:
            current_node: XNode | None = node
            while current_node is not None and not current_node.is_root():
                next_node = current_node.next()
                if next_node is not None:
                    end_node = next_node
                    break
                current_node = current_node.parent

        if end_node:
            end = xobjects.index(end_node.object, start)

        for child in node.children:
            self._fill_others(xtree, child)

        node.add(*xobjects[start:end], index=0)
