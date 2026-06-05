import logging
from enum import StrEnum, auto
from typing import Final

from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.base.pattern import XPattern
from memect.pdf.base import KDocument, KText
from .xbase import XObject, XText, XTree


class _MergeScene(StrEnum):
    SAME_PAGE_CROSS_COLUMN = auto()
    CROSS_PAGE_SAME_COLUMN = auto()
    CROSS_PAGE_CROSS_COLUMN = auto()
    UNKNOWN = auto()


class XTextParser:
    """跨页/跨栏文本合并"""

    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    def __init__(self):
        super().__init__()
        self._sentence_pattern: Final = sentence_pattern
        self._title_pattern: Final = title_pattern

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
        if index + 1 >= len(xobjects):
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

        # 如果是来自llm的，没有每一行的坐标，就不合并，因为这里依赖版面连续性。
        if not t1.lines or not t2.lines:
            return False

        scene = self._detect_scene(t1, t2)
        if scene == _MergeScene.UNKNOWN:
            return False

        if self._has_hard_reject(t1, t2):
            return False

        score, reasons = self._score_continuity(t1, t2, scene)
        threshold = self._threshold(scene)
        accept = score >= threshold
        debugger = self._debugger.bind(page=t1.page.number)
        if debugger.allow("info"):
            debugger.print(
                {
                    "scene": scene,
                    "score": score,
                    "threshold": threshold,
                    "accept": accept,
                    "reasons": reasons,
                    "t1": t1.text[:20],
                    "t2": t2.text[:20],
                }
            )
        return accept

    def _detect_scene(self, t1: KText, t2: KText) -> _MergeScene:
        if t1.page.number == t2.page.number:
            return self._detect_same_page_scene(t1, t2)
        elif t1.page.number + 1 == t2.page.number:
            return self._detect_cross_page_scene(t1, t2)
        else:
            return _MergeScene.UNKNOWN

    def _detect_same_page_scene(self, t1: KText, t2: KText) -> _MergeScene:
        # 首页经常是复杂表格式布局，跨栏误合并风险高。
        if t1.page.number == 1:
            return _MergeScene.UNKNOWN
        if t1.bbox.width < 50:
            return _MergeScene.UNKNOWN

        section1 = t1.page.get_section(t1)
        section2 = t2.page.get_section(t2)
        if section1 is not section2:
            return _MergeScene.UNKNOWN

        column1 = section1.get_column(t1.bbox)
        column2 = section2.get_column(t2.bbox)
        if column1 is None or column2 is None:
            return _MergeScene.UNKNOWN
        if column1.index + 1 != column2.index:
            return _MergeScene.UNKNOWN

        # t2 所在栏顶部如果明显低于 t1 所在栏顶部，通常不是自然跨栏续写。
        if column2.bbox.y1 <= column1.bbox.y1 - 30:
            return _MergeScene.UNKNOWN
        return _MergeScene.SAME_PAGE_CROSS_COLUMN

    def _detect_cross_page_scene(self, t1: KText, t2: KText) -> _MergeScene:
        if abs(t1.page.width - t2.page.width) >= 2:
            return _MergeScene.UNKNOWN

        section1 = t1.page.get_section(t1)
        section2 = t2.page.get_section(t2)
        if not section1.alike(section2):
            return _MergeScene.UNKNOWN

        if t1.bbox.y0 - max(70, self._get_bottom(t1)) >= 30:
            return _MergeScene.UNKNOWN
        if self._get_top(t2) - t2.bbox.y1 >= 20:
            return _MergeScene.UNKNOWN

        if section1.col_num == 1:
            return _MergeScene.CROSS_PAGE_SAME_COLUMN

        column1 = section1.get_column(t1.bbox)
        column2 = section2.get_column(t2.bbox)
        if column1 is None or column2 is None:
            return _MergeScene.UNKNOWN

        if column1.index == column2.index:
            return _MergeScene.CROSS_PAGE_SAME_COLUMN
        if column1.index + 1 == section1.col_num and column2.index == 0:
            return _MergeScene.CROSS_PAGE_CROSS_COLUMN

        return _MergeScene.UNKNOWN

    def _get_bottom(self, t: KText) -> float:
        if t.page.footer.objects:
            bottom = t.page.footer.bbox.y1
        else:
            bottom = min(t.page.bbox.y1, 100)

        if t.page.footnotes:
            # 如果脚注跟随分栏，优先取与当前文本同侧的脚注。
            bboxes: list[BBox] = []
            for footnote in t.page.footnotes:
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

    def _get_top(self, t: KText) -> float:
        if t.page.header.objects:
            return t.page.header.bbox.y0
        return max(0, t.page.bbox.y1 - 100)

    def _has_hard_reject(self, t1: KText, t2: KText) -> bool:
        if self._has_sentences(t1, t2):
            return True
        if self._looks_like_title_start(t1):
            return True
        if self._looks_like_title_start(t2):
            return True
        return False

    def _looks_like_title_start(self, t: KText) -> bool:
        text = t.text2.strip()
        if not text:
            return True
        if len(text) > 60:
            return False

        title_prefixes = (
            "第",
            "一、",
            "二、",
            "三、",
            "四、",
            "五、",
            "六、",
            "七、",
            "八、",
            "九、",
            "十、",
        )
        if text.startswith(title_prefixes):
            return True
        if self._title_pattern.fullmatch(text):
            return True

        if len(t.lines) == 1:
            b = t.lines[0].bbox
            page_cx = t.page.bbox.cx
            if abs(b.cx - page_cx) <= max(10, b.height * 2):
                return True

        return False

    def _threshold(self, scene: _MergeScene) -> int:
        if scene == _MergeScene.CROSS_PAGE_CROSS_COLUMN:
            return 7
        return 6

    def _score_continuity(
        self, t1: KText, t2: KText, scene: _MergeScene
    ) -> tuple[int, list[str]]:
        score = 0
        reasons: list[str] = []

        def add(value: int, reason: str):
            nonlocal score
            score += value
            reasons.append(reason)

        if scene == _MergeScene.SAME_PAGE_CROSS_COLUMN:
            add(2, "same page cross column")
        elif scene == _MergeScene.CROSS_PAGE_SAME_COLUMN:
            add(2, "cross page same column")
        elif scene == _MergeScene.CROSS_PAGE_CROSS_COLUMN:
            add(2, "cross page cross column")

        if self._is_full(t1):
            add(2, "t1 last line is full")
        else:
            add(-3, "t1 last line is not full")

        if self._line_start_aligned(t1, t2, scene):
            add(2, "line start aligned")

        if self._font_size_alike(t1, t2):
            add(1, "font size alike")

        if not self._ends_with_strong_punctuation(t1):
            add(1, "t1 has no strong terminal punctuation")
        else:
            add(-2, "t1 has strong terminal punctuation")

        if self._is_indent(t2):
            add(-2, "t2 looks indented")

        if self._is_indent(t1) and self._is_indent(t2):
            add(-2, "both texts look complete by indent")

        if scene == _MergeScene.CROSS_PAGE_CROSS_COLUMN:
            # 跨页跨栏误合并风险更高，要求 t1 在末栏、t2 在首栏时额外加分。
            add(1, "strict cross page column order")

        return score, reasons

    def _has_sentences(self, t1: KText, t2: KText) -> bool:
        if self._sentence_pattern.fullmatch(
            t1.text2
        ) or self._sentence_pattern.fullmatch(t2.text2):
            return True
        else:
            return False

    def _is_indent(self, t: KText) -> bool:
        if len(t.lines) < 2:
            return False

        line1 = t.lines[0]
        line2 = t.lines[1]
        char_width = line1.bbox.height
        b1 = line1.bbox
        b2 = line2.bbox

        if b1.x0 - b2.x0 >= 1.5 * char_width:
            return True
        elif b2.x0 - b1.x0 >= 1.5 * char_width:
            return True
        else:
            return False

    def _is_full(self, t: KText) -> bool:
        """判断最后一行是否写满了当前文本块的可用宽度。"""
        if len(t.lines) < 2:
            return True

        line_bbox = t.lines[-1].content_bbox or t.lines[-1].bbox
        text_bbox = t.content_bbox or t.bbox
        assert line_bbox is not None
        assert text_bbox is not None
        return text_bbox.x1 - line_bbox.x1 <= 10

    def _line_start_aligned(
        self, t1: KText, t2: KText, scene: _MergeScene
    ) -> bool:
        line1 = t1.lines[-1]
        line2 = t2.lines[0]

        if scene == _MergeScene.SAME_PAGE_CROSS_COLUMN:
            return True
        if scene == _MergeScene.CROSS_PAGE_CROSS_COLUMN:
            return t1.bbox.x0 - t2.bbox.x1 >= -5

        return abs(line1.bbox.x0 - line2.bbox.x0) <= 10

    def _font_size_alike(self, t1: KText, t2: KText) -> bool:
        h1 = t1.lines[-1].bbox.height
        h2 = t2.lines[0].bbox.height
        return abs(h1 - h2) <= max(1.5, min(h1, h2) * 0.2)

    def _ends_with_strong_punctuation(self, t: KText) -> bool:
        return t.text2.rstrip().endswith(("。", "！", "？", ".", "!", "?", "；", ";"))

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

title_pattern: Final = XPattern(
    "fullmatch",
    join=False,
    patterns=[
        r"[0-9]+([.．][0-9]+)*[.．、]\s*\S.{0,50}",
        r"[（(]?[0-9]+[）)]\s*\S.{0,50}",
        r"[一二三四五六七八九十]+[、.．]\s*\S.{0,50}",
    ],
)
