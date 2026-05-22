import logging
from typing import Final, Sequence

from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.base.pattern import XPattern
from memect.pdf.base import KDocument, KText
from .xbase import XObject, XText, XTree


class XTextParser:
    """跨页/跨栏文本合并"""

    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    def __init__(self):
        super().__init__()
        self._sentence_pattern: Final = sentence_pattern

    def parse(self, xtree: XTree):
        doc: Final = xtree.doc
        xobjects: Final = xtree.xobjects
        i = 0
        while i < len(xobjects):
            # 如果合并了，会修改xobjects
            j = self._parse_once(doc, i, xobjects)
            if j == -1:
                i += 1
            else:
                i = j

    def _parse_once(self, doc: KDocument, index: int, xobjects: list[XObject]) -> int:
        if index + 1 < len(xobjects):
            return -1

        xobj1 = xobjects[index]
        if not isinstance(xobj1, XText):
            return -1

        assert len(xobj1.objects) > 0

        i = index + 1
        while i < len(xobjects):
            xobj2 = xobjects[i]
            if not isinstance(xobj2, XText):
                break
            if not self._merge(xobj1, xobj2):
                break
            del xobjects[i]
            xobj1.add(*xobj2.objects)

        if len(xobj1.objects) > 1:
            debugger = self._debugger.bind(page=xobj1.page_numbers[0])
            if debugger.allow("info"):
                debugger.print((xobj1.page_numbers, xobj1.text[0:20]))

        return i

    def _merge(self, xobj1: XText, xobj2: XText) -> bool:
        assert len(xobj1.objects) > 0
        assert len(xobj2.objects) > 0

        t1 = xobj1.texts[-1]
        t2 = xobj2.texts[0]

        # 如果是来自默认解析的
        # 如果都缩进，不需要合并，表示为完整的文本
        # 如果都是常见句子，不需要合并
        # 如果obj1没有写完整空间，不需要合并

        # 如果是来自llm解析的，无法获得准确的位置，所以就不考虑上面这些
        # assert len(t1.objects)==1 and len(t2.objects)==1

        debugger = self._debugger.bind(page=t1.page.number)

        def accept1(t1: KText, t2: KText) -> bool:
            """同页跨栏"""
            # 跨栏
            # 宁愿合并错误
            # 所以需要使用严格的条件，因为分栏的情况太多了
            # 研报的首页，分成左右两边，而且几乎都是使用表格来布局的，也就是不存在跨栏
            # 典型的文档，是左右分栏
            # 杂志类的布局，更加难以判断a和b两个文本块是否需要合并了，从物理布局很难判断了，只能够从文字上
            if t1.page.number == 1:
                # 首页不需要
                return False

            if t1.page.number != t2.page.number:
                # 不同页
                return False

            if t1.bbox.width < 50:
                # 太少字符就不考虑是否合并了
                return False

            section1 = t1.page.get_section(t1)
            section2 = t2.page.get_section(t2)

            if section1 is not section2:
                return False

            column1 = section1.get_column(t1.bbox)
            column2 = section2.get_column(t2.bbox)
            if column1 is None or column2 is None or column1.index + 1 != column2.index:
                return False
            # 还需要考虑位置关系
            # t1必须为底部，t2必须为顶部
            # [  ]|[t2]
            # [t1]|
            # 严格的，column1和column2顶端对齐，但是可能column1有很多换行，导致空白
            if column2.bbox.y1 <= column1.bbox.y1 - 30:
                return False
            return True

        def accept2(t1: KText, t2: KText) -> bool:
            """跨页"""
            if t1.page.number + 1 != t2.page.number:
                return False

            if abs(t1.page.width - t2.page.width) >= 2:
                # TODO 页面宽度不一致，也是可以存在跨页的，现在要求严格吗？
                return False

            # 1.跨页不分栏
            # [---t1---]
            # -----------
            # [---t2----]  =>如果严格的，要求页面宽度一致，实际上也可能存在不一致
            # 2.跨页分栏 =>
            # [xx]|[t1]
            # -----------
            # [t2]|[xx] =>

            section1 = t1.page.get_section(t1)
            section2 = t2.page.get_section(t2)

            if not section1.alike(section2):
                return False

            if section1.col_num == 1:
                # [---t1---]
                # ------------
                # [---t2----]
                pass
            elif section1.col_num >= 2:
                # [x]|[t1]
                # ---------
                # [t2]
                if t2.bbox.x1 > t1.bbox.x0:
                    return False
            else:
                pass

            # 正常的页脚都有70
            if t1.bbox.y0 - max(70, get_bottom(t1)) >= 20:
                # 有些研报剩余的空间比较大，或者底部比较小
                # [t2]
                # [  ]   =>间距过大，表示还有空间，不需要合并
                # --footer--
                return False

            if get_top(t2) - t2.bbox.y1 >= 20:
                # ---header---
                # [  ] =>间距太大，表示并不需要合并
                # [t2]
                return False
            return True

        def get_bottom(t: KText) -> float:
            if t.page.footer.objects:
                bottom = t.page.footer.bbox.y1
            else:
                # 默认的为70
                bottom = min(t.page.bbox.y1, 100)

            if t.page.footnotes:
                # 如果是word97，跟随所在的分栏，所以需要获得右边分栏的，因为可能如下：
                # --|---------
                # --|-footnote-  跟随所在分栏
                # --|

                bboxes: list[BBox] = []
                for footnote in t.page.footnotes:
                    # 先判断是否有跟随的，如果有，就使用，如果没有，就使用全部的
                    # 目前仅仅考虑双栏，而且跨页合并的，t1只能够在第二列
                    cx = t.page.bbox.cx
                    if t.bbox.x0 - cx >= -5 and footnote.bbox.x0 - cx >= -5:
                        bboxes.append(footnote.bbox)
                if bboxes:
                    b = BBox.join(bboxes)
                    bottom = max(bottom, b.y1)
                else:
                    b = BBox.join2(t.page.footnotes)
                    bottom = max(bottom, b.y1)

            return bottom

        def get_top(t: KText) -> float:
            if t.page.header.objects:
                b = t.page.header.bbox
                return b.y0
            else:
                return max(0, t.page.bbox.y1 - 100)

        t1 = xobj1.texts[-1]
        t2 = xobj2.texts[0]

        # 如果是来自llm的，没有每一行的坐标，就不合并了，因为这个需要使用复杂的语义
        if not t1.lines or not t2.lines:
            return False

        # 如果有一个已经是常见句子，不合并
        if self._has_sentences(t1, t2):
            return False

        if accept1(t1, t2) or accept2(t1, t2):
            # 同页跨栏，跨页跨栏，跨页
            return self._is_alike(t1, t2)

        return False

    def _has_sentences(self, t1: KText, t2: KText) -> bool:
        if self._sentence_pattern.fullmatch(
            t1.text2
        ) or self._sentence_pattern.fullmatch(t2.text2):
            return True
        else:
            return False

    def _is_alike(self, t1: KText, t2: KText) -> bool:

        verbose = True
        debugger = self._debugger.bind(page=t1.page.number)

        def is_left_align(t: KText) -> bool:
            for i in range(1, len(t.lines)):
                line1 = t.lines[i - 1]
                line2 = t.lines[i]
                # 或者使用content_bbox?
                # b1 = line1.content_bbox
                # b2 = line2.content_bbox
                b1 = line1.bbox
                b2 = line2.bbox
                # 获得字符的宽度，使用字符串的高度即可？
                # cw1 = b1.height
                # cw2 = b2.height
                dx = max(5, b1.height, b2.height)
                if abs(b1.x0 - b2.x0) > dx:
                    return False
            return True

        def is_right_align(t: KText) -> bool:
            n = 0
            for i in range(1, len(t.lines)):
                line1 = t.lines[i - 1]
                line2 = t.lines[i]
                # 或者使用content_bbox?
                # b1 = line1.content_bbox
                # b2 = line2.content_bbox
                b1 = line1.bbox
                b2 = line2.bbox
                # 获得字符的宽度，使用字符串的高度即可？
                # cw1 = b1.height
                # cw2 = b2.height
                dx = max(5, b1.height, b2.height)
                # 可能存在单词太长break的情况，如何判断，如：
                # [xxxxxxxxxxxxx]
                # [xxx]  =>单词太长，整个到下一行
                # [xxxxxxxxxxxxx]
                # [xxxxxxxxxxxxx]
                if abs(b1.x1 - b2.x1) > dx:
                    return False
                else:
                    n += 1
            return True

        def is_indent(t: KText) -> bool:
            if len(t.lines) < 2:
                return False
            line1 = t.lines[0]
            line2 = t.lines[1]

            char_width = line1.bbox.height
            b1 = line1.bbox
            b2 = line2.bbox

            if b1.x0 - b2.x0 >= 1.5 * char_width:  # and is_left_align(t.lines[1:]):
                # 因为已经通过对象识别了，所以都不需要再判断后续的了
                #    [xxxx]
                # [xxxxxxxx]
                # [xxxxxxxx]
                return True
            elif b2.x0 - b1.x0 >= 1.5 * char_width:  # and is_left_align(t.lines[1:]):
                # [xxxxxxxxxxx]
                # [  xxxxxxxxx]
                # [  xxxxxxxxx]
                return True
            else:
                return False

        def is_full(t: KText) -> bool:
            """判断最后一行是否写完了全部的空间"""
            if len(t.lines) < 2:
                # 找到所在的分栏，进行判断
                return True

            # 判断最后一行是否书写了全部的空间，如果这里检查就，也会导致word break的错误，因为可能当前行空间不足，写到下一行
            b1 = t.lines[-1].content_bbox
            b2 = t.content_bbox
            assert b1 is not None
            assert b2 is not None
            if b2.x1 - b1.x1 <= 10:
                return True
            else:
                return False

        if is_indent(t1) and is_indent(t2):
            if verbose and debugger.allow("info"):
                debugger.print("t1,t2都缩进，不合并")
            # 都是完整的，不需要合并
            return False
        elif not is_full(t1):
            # 如果最后一行没有使用全部的空间，就不需要合并
            if verbose and debugger.allow("info"):
                debugger.print("t1没有使用所有空间，不合并")
            return False
        elif t1.page.number != t2.page.number:
            # 跨页
            # 只剩3种情况
            #   [xxxxx]
            # ----------- 如果是跨页的，可以再次考虑是否缩进，如果是跨栏的，就不考虑了
            # [xxxxxxxx]
            # 第二种
            # [xxxxxxx]
            # [xxxxxxx]
            # -----------
            #   [xxxx]
            # 第三种
            # [xxxxxxx]
            # -----------
            # [xxxxxxx]

            # 跨页且分栏
            char_width = int(t1.lines[-1].bbox.height)
            if verbose and debugger.allow("info"):
                debugger.print(
                    {"t1": t1.text[0:20], "t2": t2.text[0:20], "char_width": char_width}
                )

            line1 = t1.lines[0]
            line2 = t1.lines[-1]
            line3 = t2.lines[0]
            if t1.bbox.x0 - t2.bbox.x1 >= -5:
                # [   ]|[t1]
                # --------------
                # [t2] |
                return True
            # elif -5<=t1.bbox.x0-t2.bbox.x0<=char_width*3:
            elif abs(line2.bbox.x0 - line3.bbox.x0) <= 10:
                # 第一种
                #   ---line1---
                # ------line2---
                # -----------------分页
                # ------line2---
                # 第二种
                # -----line1----
                #   --line2----
                # -----------------分页
                #   --line3----

                #   ---t1---
                # ------t2---
                # 或者
                # ---t1---
                # ---t2---
                # 跨页不分栏
                # [  t1  ]
                # ----------
                # [  t2  ]
                # 或者跨页2列表格
                # []|[t1]
                # --------
                # []|[t2]
                return True
            else:
                return False
        else:
            # 分栏的情况
            if verbose and debugger.allow("info"):
                debugger.print("t1,t2分栏，合并")
            return True


sentence_pattern: Final = XPattern(
    "fullmatch",
    join=False,
    patterns=[
        r"([□√☑](适用|不适用|是|否|已达到|未达到))+",
        # 如果需要放宽，支持各种字符的，可以如下
        # r'.适用.不适用',
        r"[□√☑]法人[□√☑]自然人",
        # 现在严格一点，不允许包含标点符号，避免误判
        r"(表|图|图表|表格)[:：]?[0-9\-]{1,4}[^,;?!，；。?！]+",
        # 单位:元 币种:人民币
        r"[（(]?单位[:：].+?[)）]?",
        r"[（(]?币种[:：].+?[)）]?",
        # 目录项
        r".*[.]{10,}.*",
        r"[一二三四五六七八九十]+.*[.]{3,}[0-9]+",
        r"[(（]以下无正文[）)]",
        r"回复[:：]",
        r"答复[:：]",
        r"【答复】[:]?",
        r"(法定代表人|负责人)[:：].{2,4}",
        #
        # r'发行人报告期内主要财务数据及财务指标如下[:：]',
        r".*主要财务数据及财务指标如下[:：]",
        r"请(投资者|投资人)仔细阅读相关内容[,，]知悉相关风险。",
        # 广发
        r"注[0-9]*[:：]上述财务指标计算公式如下[:：]",
        r"注[0-9]*[:：]数据来源于各公司年度报告、招股说明书等公开资料。",
        # 注[0-9]*[:：]以上数据均来自WIND数据库、可比上市公司定期报告、招股说明书。
        r"注[0-9]*[:：]以上数据均来自.+报告、招股说明书。",
    ],
)
