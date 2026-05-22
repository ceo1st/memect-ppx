from enum import StrEnum, auto
import json
import logging
import re
import threading
from typing import Any, Final, Mapping, Self, Sequence, override

from anthropic import Anthropic
from openai import OpenAI

from memect.base.pattern import XPattern
from memect.base.utils import MyBaseModel
from memect.pdf.base import KDocument, KObject, KText, TreeBackend
from memect.pdf.x.xchapter import XChapterParser
from .xbase import XNo, XNode, XObject, XText, XTree
from .xgroup import XGroupParser
from .xtable import XTableParser
from .xtext import XTextParser


class ParserArgs(MyBaseModel):
    pass


class Parser(XChapterParser):
    def __init__(self, args: Mapping[str, Any] | ParserArgs | None):
        super().__init__()

    def parse(self, xtree: XTree):
        # 先使用一个简单的实现，找到标题即可
        pass

    def _parse_chapter(self, node: XNode):
        # 先把所有直接子节点解析XNo，标记标题
        for child in list(node.children):
            if child.is_text():
                xno = XNo.parse(child.text.text)
                if xno is not None:
                    child.text.no = xno
                    child.text.as_title()

        # 按XNo.level重建层级结构
        # title_stack[i] 表示当前第i级的最近标题节点
        title_stack: list[XNode] = [node]

        for child in list(node.children):
            if not child.is_title():
                # 非标题挂到最近的标题下
                if len(title_stack) > 1:
                    node.remove(child)
                    title_stack[-1].add(child)
                continue

            xno = child.text.no
            level = xno.level if xno is not None else 1

            # 找到应该挂载的父节点：level级标题挂到level-1级下
            while len(title_stack) > level:
                title_stack.pop()

            parent = title_stack[-1]
            if parent is not node:
                node.remove(child)
                parent.add(child)

            # 截断比当前level更深的栈
            title_stack = title_stack[:level]
            title_stack.append(child)


# 划分章节的几种方式
# 1. 指定页面范围，如：有固定格式的文档
# 2. 有大概的格式，页面范围不固定，如：研报
# 3. 页面范围从标题1-标题2，如：第一章...第二章
# 4. 如果没有下一个标题，有些上一个标题只是某几页的内容，如：
# <目录>
# <正文> => 这个没有标题，需要找到目录章节的结束

# <首页> 1-2页
# <目录> 0-n个，可能在中间出现
# <正文> 0-n个
# <免责声明> 0-1个，最后面

template = {
    # <封面>
    # 重要声明|xxx|xxxx
    # 目录
    # 第一章
    # 第二章
    # 第三章
    # 附录
    "chapters": [
        {
            # 逻辑标题
            "title": "<首页>",
            # plain表示不需要解析层次结构，normal表示需要，toc表示为目录章节
            "type": "plain",
            # 表示从第一页开始，0表示未知，也就是到下一个标题
            "pages": [1, 0],
        },
        {
            # 标题的正则表达式
            "titles": [
                r"重大事项提示|特别提示|重要提示",
                r"声明与承诺|声明",
                r"释义",
                r"前言",
                r"目录",
                r"正文目录",
                r"图表目录|图目录",
                r"内容目录",
                r"插图目录",
                r"表格目录|表目录",
                r"目录索引",
                r"图表索引",
            ],
            "type": "plain",
            # 或者指定式样要求
            "style": {
                "bold": True,
                # left,right,center
                "align": "center",
                # 表示字体大小>=20
                "fontsize": 20,
                # 最少多少个字符
                "min_length": 2,
                # 最大多少个字符
                "max_length": 10,
            },
            # 或者该页出现下面的关键字，为正则表达式
            "keywords": [
                r"分析师声明|免责声明|法律声明",
                r"信息披露",
                r"评级说明|股票投资评级说明",
            ],
            # 至少需要出现2个关键字
            "min_keyword_size": 2,
        },
        {"titles": [r"第一章"]},
        {"titles": [r"附录"]},
    ]
}
template = {
    "chapters": [
        {"title": "<首页>", "type": "plain", "pages": [1, 1]},
        {
            "titles": [
                r"目录",
                r"正文目录",
                r"图表目录|图目录",
                r"内容目录",
                r"插图目录",
                r"表格目录|表目录",
                r"目录索引",
                r"图表索引",
            ],
            # 表示为目录，然后自动判断该目录的结束页面
            "type": "toc",
        },
        {
            # 没有任何规则，靠前后决定
            "title": "<正文>"
        },
        {
            "title": "<免责声明>",
            #-1表示最后一页
            "pages": [-1],
            # 关键字正则表达式
            "keywords": [
                r"分析师声明|免责声明|法律声明",
                r"信息披露",
                r"评级说明|股票投资评级说明",
            ],
            # 表示包含多少个关键字
            "min_keyword_size": 2,
        },
    ]
}

template = {
    "chapters": [
        {"type": "cover", "pages": [1]},
        {
            "type": "normal",
            "patterns": [
                r"重要声明",
                r"免责声明",
                r"前言",
                r"重大事项提示|声明与承诺|重要提示|声明|目录|释义",
            ],
            "toc_patterns": "default",
            "no_patterns": "default",
        },
    ]
}

template = {
    "chapters": [
        {"type": "cover"},
        {
            "type": "normal",
            "patterns": [
                r"^[第]?[\s]*([一二三四五六七八九十]{1,3})[\s]*[章]",
                r"^[第][\s]*([一二三四五六七八九十]{1,3})[\s]*[节]",
                # TODO 这个出现的概率很小，而且很容易误识别，如：第一条 xxxx条款
                # local/pingan-bug/pdfs/27.pdf
                # local/pingan-bug/pdfs/28.pdf
                # r'^[第][\s]*([一二三四五六七八九十]{1,2})[\s]*[条]',
                r"^[第][\s]*([一二三四五六七八九十]{1,3})[\s]*[部]",
                r"^[第][\s]*([0-9]+)[\s]*部分",
                # ===============================
                r"^第([1-9][0-9]?)节",
                # 只遇到一个文档，如果容易误判的，可以先去掉，然后在“目录”页面中判断是需要这种标题
                # local/法审报告/托管协议/pdfs/券商资管托管_托管协议_财通证券资管聚丰17号集合资产管理计划-托管协议_财通证券资管聚丰17号集合-托管协议.pdf
                # 下面这2个可能会引起误判
                # local/法审报告/资管合同/pdfs/基金专户托管_资管合同_创金合信长风1号集合资产管理计划合同法审_创金合信长风1号集合资产管理计划0512.pdf 87页开始
                # 但是有些文档也包含这些，所以，现在可以包含这些，但是需要更加严格的条件清除
                r"^[第][\s]*([一二三四五六七八九十]+)[\s]*条",
                # 合同的章节有些直接就使用一、xxxx，这个很容易误判
                # 这个可以考虑要求黑体
                r"^([一二三四五六七八九十]+)、.+",
            ],
        },
    ]
}


class ChapterType(StrEnum):
    NORMAL = auto()
    PLAIN = auto()
    TOC = auto()


class Rule:
    def __init__(
        self,
        *,
        title: str | None = None,
        titles: Sequence[str | re.Pattern[str]] | None = None,
        keywords: Sequence[str | re.Pattern[str]] | None = None,
        pages: Sequence[int] | None = None,
        min_length: int = 2,
        max_length: int = 30,
        min_keyword_size: int = 2,
        style: Any = None,
        type: str = "normal",
        deep: bool = False,
    ):
        super().__init__()
        self.title: str | None = title
        self.titles: Final[list[re.Pattern[str]]] = self._ensure_patterns(titles or [])
        self.keywords: Final[list[re.Pattern[str]]] = self._ensure_patterns(
            keywords or []
        )
        self.pages: Final = pages
        self.min_length: Final = min_length
        self.max_length: Final = max_length
        self.min_keyword_size: Final = min_keyword_size
        self.style: Final = style
        self.deep: Final = deep

    def _ensure_patterns(
        self, patterns: Sequence[str | re.Pattern[str]]
    ) -> list[re.Pattern[str]]:
        new_patterns: list[re.Pattern[str]] = []
        for p in patterns:
            if isinstance(p, str):
                p = re.compile(p)
            new_patterns.append(p)
        return new_patterns

    def accept(self, tb: XText) -> bool:
        for p in self.titles:
            m = p.fullmatch(tb.text)
            if m:
                text = m.group("no")
                value = m.group("value")
                no: XNo | None = None
                if text and value:
                    no = XNo(text, value, body=tb)
                title = XTitle(tb.text, body=tb, no=no)
                self.titles.append(title)
                return True
        return False

    def check(self):
        # 从严格的角度，可以检查字体，是否居中等，这样可以增加准确度，但是对于特殊的案例，就无法支持了
        # 从宽松的角度，不做任何检查了，这样可以支持丢失，重复了标题等情况
        debug = utils.Debug({"info": True})
        for title in self.titles:
            # 如果标题都是有序号的
            # 如果标题都是无序号的，检查一下？居中对齐，粗体？避免误判，但是也会降低了泛化性
            if title.no is not None:
                pass
            pass
        pass

    @classmethod
    def build(cls, chapters: Sequence[Any]) -> list[Self]:
        rules: list[Rule] = []
        for chapter in chapters:
            rule = Rule(title=chapter.get("title"), patterns=chapter.get("patterns"))
            rules.append(rule)

        for i, rule in enumerate(rules):
            if i > 0 and not rule.patterns:
                raise ValueError(
                    f"除了第一个，后续的章节定义都必须有patterns，错误的为第{i + 1}个"
                )

        return rules


class _Analyzer:
    def parse(self, doc: KDocument):
        pass


class _Analyzer2(_Analyzer):
    """根据模板来划分章节"""

    def __init__(self):
        super().__init__()

    @override
    def parse(self, doc: KDocument) -> list[Any]:
        """
        解析章节，所谓章节，就是一个按逻辑划分内容，主要有下面2种：
        1. 原文有明确的章节划分的
           也就是作者在写文档的时候遵守一定的格式，思路清晰
           但是文档的格式还是种类繁多的，如：
           普通的文档
           书信

        2. 原文没有明确的章节划分
           可能全文就一个章节，或者全文混乱，没有什么条理性，想到什么写什么，
           又或者任意排版，如：逻辑上应该为连续的内容，可以间隔很大的区域再写，
           总之就是无厘头。对于这种文档，如果硬要按“自己”或者“统一”的方式划分章节，
           是很困难的，因为作者在书写的时候就没有这个概念。

        3. 多语言的支持
        """

        template = {"chapters": []}
        rules = Rule.build(template["chapters"])
        self._scan1(tree, rules)
        self._build_chapters(tree, rules)

    def _scan1(self, tree: XTree):

        def is_first_object(xobj: XText) -> bool:
            t = xobj.objects[0]
            i = t.page.objects.index(t)
            return i == 0

        xobjects = tree.xobjects
        rules: list[Rule] = []

        # 表示规则使用到哪一条了
        object_index = 0
        rule_index = 0

        # 对于多数文档，章节标题都是在每一页的第一行（可能有些有其他内容影响）
        # 当然也有些文档忘记插入分页符了，导致章节并没有分页
        # 或者有些可能就不分页，而是连续的书写
        # 从算法上
        # 先考虑标准的情况，这样可以提高速度和减少误识别（只需要先检查每一页前面的几行）
        # 然后如果章节有丢失，再考虑不分页的情况（要求遍历所有的文本）
        strict = False
        while rule_index < len(rules):
            # 先使用就近原则，测试相连的规则，而不是全部
            rule = rules[rule_index]
            for i in range(object_index, len(xobjects)):
                tb = xobjects[i]
                if not isinstance(tb, XText):
                    continue

                # 逻辑文本？目前应该不会有
                if len(tb.objects) == 0:
                    continue

                # TODO 如果严格的，可以判断是否为每一页的第一行
                if not rule.deep and not is_first_object(tb):
                    continue
                # 先判断是否满足当前的和下一个的

                if rule.pages:
                    # 指定了页码范围，如：[1,2,3,0]，表示跳过前面1，2，3页
                    if tb.page_numbers[-1] in rule.pages:
                        continue

                if not rule.titles or not rule.keywords:
                    # 如果没有模式，跳过到下一个规则
                    rule_index += 1
                elif rule.accept(tb):
                    object_index = i + 1
                elif rule_index + 1 < len(rules) and rules[rule_index + 1].accept(tb):
                    # 下一个规则匹配
                    rule_index += 1
                    object_index = i + 1
                else:
                    # 如果当前规则一直没有匹配，下一个规则也没有，那么，后续的规则将没有机会匹配
                    # 然后再外部再继续循环，就可以让所有的规则都完成
                    pass

            # 匹配了所有的对象后，可以递增到下一个规则（因为可能还没有测试过）
            # 严格的说，可以+2，因为+1的已经测试过了
            rule_index += 2

        for rule in rules:
            # 可以再统一检查过滤一下错误的标题，或者丢失的标题？
            rule.check()

        start = 0
        for i, rule in enumerate(rules):
            if not rule.patterns:
                # 表示没有模式，标题就是逻辑上的标题
                assert not rule.titles
                assert rule.title
                obj = xobjects[start]
                bbox = obj.pages[0].body.content_bbox or obj.pages[0].body.bbox
                bbox = bbox.copy(x1=bbox.x0, y1=bbox.y0)
                rule.titles.append(
                    XTitle(rule.title, placeholder=Placeholder(obj.pages[0], bbox))
                )
            elif rule.title and rule.titles:
                # 如果定义了逻辑标题且根据模式找到多个标题，那么，就仅仅使用这个逻辑标题，如：
                # <附录>
                #  附录1  =>找到了这个标题
                #  附录2  =>找到了这个标题
                bbox = rule.titles[0].body.objects[0].bbox
                bbox = bbox.copy(x1=bbox.x0, y1=bbox.y0)
                title = XTitle(rule.titles[0].pages[0], bbox)
                # 同时把这些也做为子标题了
                for title in rule.titles:
                    pass
                pass
            else:
                pass

        for rule in rules:
            for title in rule.titles:
                assert title.body is not None
                # 替换对象为title
                i = xobjects.index(title.body)
                xobjects[i] = title

    def _scane2(self, tree: XTree):
        # 先扫描每一页的首行（或者前几行），获得章节的标题
        # 如果有丢失，再扫描前后章节之间的内容

        pass

    def _scan3(self, tree: XTree):
        # 按默认的方式查找章节
        # 每一页的第一行，如果满足
        # 文字居中
        # 粗体
        # 字体大
        # 可能就是标题
        # 当然，还有很多其他的可能性
        # 如果没有分页符的，可能就是在某一页的中间
        # 或者标题没有居中，也没有加粗，字体也没有更大
        for obj in tree.objects:
            if not isinstance(obj, XText):
                continue
            tb = obj

        pass

    def _build_chapters(self, tree: XTree, rules: Sequence[Rule]):
        # 最前面的章节可以没有

        def make_title(tb: XText) -> XTitle:
            return XTitle(tb.text, body=tb)

        def make_title2(text: str, placeholder: Placeholder) -> XTitle:
            return XTitle(text, placeholder=placeholder)

        objects: Final = tree.objects
        chapters: Final[list[XChapter]] = []
        start = 0
        end = len(objects)
        for i, rule in enumerate(rules):
            if rule.title:
                # 如果定义了标题，表示满足该规则的都属于同一个章节
                if i + 1 < len(rules):
                    next_rule = rules[i + 1]
                    if next_rule.titles:
                        end = objects.index(next_rule.titles[0].body)
                    else:
                        end = len(objects)
                else:
                    end = len(objects)
                if end - start > 0:
                    if rule.tbs:
                        placeholder = Placeholder(
                            rule.tbs[0].pages[0], rule.tbs[0].chars[0].bbox
                        )
                    else:
                        # 如果没有原文，就是取第一个对象所在页面的开始？
                        # 严格的说，取第一个对象的bbox，但是因为现在只有前面的才允许没有tbs，所以取开始页面坐标也可以
                        placeholder = Placeholder(
                            objects[start].pages[0],
                            objects[start].pages[0].bbox.copy(x1=0, y1=0),
                        )
                        pass
                    title = make_title2(rule.title, placeholder)
                    chapter = XChapter(title, objects=objects[start:end])
                    chapters.append(chapter)
                else:
                    # 没有对象，不需要创建
                    pass
                start = end
            else:
                # 如果没有定义标题，表示有多个章节
                # 注意：第一个章节并不需要从tbs[0]开始，而是从start开始，如：首页的标题等，不一定来自第一行
                for j, title in enumerate(rule.titles):
                    if j + 1 < len(rule.titles):
                        assert rule.titles[j + 1].body is not None
                        end = objects.index(rule.titles[j + 1].body)
                    else:
                        end = len(objects)

                    if end - start > 0:
                        chapter = XChapter(title, objects=objects[start:end])
                        chapters.append(chapter)
                    start = end


class _ChapterDef:
    """单条章节定义"""
    def __init__(self, data: dict[str, Any]):
        self.title: str | None = data.get("title")
        self.type: str = data.get("type", "normal")
        self.pages: list[int] = data.get("pages", [])
        self.deep: bool = data.get("deep", False)
        self.min_keyword_size: int = data.get("min_keyword_size", 2)
        self.style: dict | None = data.get("style")

        raw_titles: list[str] = data.get("titles", [])
        self.title_patterns: list[re.Pattern[str]] = [re.compile(p) for p in raw_titles]

        raw_keywords: list[str] = data.get("keywords", [])
        self.keyword_patterns: list[re.Pattern[str]] = [re.compile(p) for p in raw_keywords]

    def _match_style(self, xobj: XText) -> bool:
        if self.style is None:
            return True
        s = self.style
        text = xobj.text
        if "min_length" in s and len(text) < s["min_length"]:
            return False
        if "max_length" in s and len(text) > s["max_length"]:
            return False
        return True

    def match_title(self, xobj: XText) -> bool:
        """判断xobj是否匹配titles规则"""
        #目前不存在逻辑文本
        assert len(xobj.objects)>0
        if not self.title_patterns:
            return False
        def is_first_object(xobj:XText)->bool:
            t = xobj.objects[0]
            #TODO 严格的为第一个对象，放宽的可以为第一个文本对象？去掉图片？
            return t.page.objects.index(t)==0
        if not self.deep and not is_first_object(xobj):
            return False
        text = xobj.text
        for p in self.title_patterns:
            if p.fullmatch(text):
                return self._match_style(xobj)
        return False

    def match_keywords(self, texts: Sequence[str]) -> bool:
        """判断该页满足多少个不同的关键字正则（每个pattern最多计一次），达到min_keyword_size即通过"""
        if not self.keyword_patterns:
            return False
        matched: set[int] = set()
        for text in texts:
            for i, p in enumerate(self.keyword_patterns):
                if i in matched:
                    continue
                if p.fullmatch(text):
                    matched.add(i)
            if len(matched) >= self.min_keyword_size:
                return True
        return False


class _Parser2(XChapterParser):
    """
    根据章节定义模板解析XTree，将xobjects挂载到root下对应的逻辑章节节点。

    支持三种章节定位方式：
    1. pages 手动指定页码
    2. titles 正则匹配标题（deep=True全文，False仅首个文本）
    3. keywords 页面关键字匹配
    """

    def __init__(self, chapters: list[dict[str, Any]]):
        super().__init__()
        self._defs: list[_ChapterDef] = [_ChapterDef(c) for c in chapters]

    def parse(self, xtree: XTree):
        xobjects = xtree.xobjects
        root = xtree.root

        page_objs: dict[int, list[XObject]] = {}
        for xobj in xobjects:
            for pn in xobj.page_numbers:
                page_objs.setdefault(pn, []).append(xobj)

        all_pages = sorted(page_objs.keys())
        total_pages = all_pages[-1] if all_pages else 0
        n = len(xobjects)

        # Phase 1: 优先处理 pages 规则，得到固定锚点
        # hits: (def_idx, start_idx) 列表，会在 Phase 2 中追加
        hits: list[tuple[int, int]] = []
        anchor_def_indices: set[int] = set()
        for di, cdef in enumerate(self._defs):
            if not cdef.pages:
                continue
            anchor_def_indices.add(di)
            idx = self._resolve_pages_anchor(cdef, xobjects, page_objs, total_pages)
            if idx is not None:
                hits.append((di, idx))

        # Phase 2: 顺序扫描，rule_ptr 在剩余规则上前进
        # 当前规则命中 -> 记录（允许同一规则多次命中，如：前言/目录/图表目录）
        # 当前规则不命中但下一规则命中 -> 跳过当前，rule_ptr 前进
        pattern_def_indices = [
            di for di, cdef in enumerate(self._defs)
            if di not in anchor_def_indices
            and (cdef.title_patterns or cdef.keyword_patterns)
        ]
        if pattern_def_indices:
            rule_ptr = 0
            for i, xobj in enumerate(xobjects):
                if rule_ptr >= len(pattern_def_indices):
                    break
                cur_di = pattern_def_indices[rule_ptr]
                if self._rule_matches(self._defs[cur_di], xobj, page_objs):
                    hits.append((cur_di, i))
                elif rule_ptr + 1 < len(pattern_def_indices):
                    next_di = pattern_def_indices[rule_ptr + 1]
                    if self._rule_matches(self._defs[next_di], xobj, page_objs):
                        rule_ptr += 1
                        hits.append((next_di, i))

        hits.sort(key=lambda h: h[1])

        # Phase 3: 计算初始 end_idx，处理 toc 自动结束
        triplets: list[tuple[int, int, _ChapterDef]] = []
        for j, (di, start) in enumerate(hits):
            cdef = self._defs[di]
            next_start = hits[j + 1][1] if j + 1 < len(hits) else n
            if cdef.type == "toc":
                end = self._find_toc_end(start, xobjects, page_objs, next_start, total_pages)
            else:
                end = next_start
            triplets.append((start, end, cdef))

        # Phase 4: 填充无规则但有逻辑标题的章节（如：<正文>）
        # 按 def 在 self._defs 中的位置确定前后邻居
        for di, cdef in enumerate(self._defs):
            if cdef.pages or cdef.title_patterns or cdef.keyword_patterns:
                continue
            if not cdef.title:
                continue
            prev_end = 0
            next_start = n
            for j, (hit_di, hit_start) in enumerate(hits):
                hit_end = triplets[j][1]
                if hit_di < di and hit_end > prev_end:
                    prev_end = hit_end
                if hit_di > di and hit_start < next_start:
                    next_start = hit_start
            if prev_end < next_start:
                triplets.append((prev_end, next_start, cdef))

        triplets.sort(key=lambda r: r[0])

        # Phase 5: 构建 xtitle，挂载对象，组装 ranges=(start, end, xtitle)
        ranges: list[tuple[int, int, XText]] = []
        for i, (start_idx, end_idx, cdef) in enumerate(triplets):
            if cdef.title:
                xtitle = XText.create_title(cdef.title)
                first_idx = start_idx
            else:
                head = xobjects[start_idx]
                if isinstance(head, XText) and cdef.match_title(head):
                    head.as_title()
                    xtitle = head
                    first_idx = start_idx + 1
                else:
                    xtitle = XText.create_title(f'<章节{i+1}>')
                    first_idx = start_idx

            root.add(xtitle)
            children = [xo for xo in xobjects[first_idx:end_idx] if xo is not xtitle]
            if children:
                xtitle.node.add(*children)
            ranges.append((start_idx, end_idx, xtitle))
        
        for start_idx,end_idx,xtitle in ranges:
            xtree.root.add(xtitle)
            xtitle.node.add(*xobjects[start_idx:end_idx])

        return 

    def _resolve_pages_anchor(
        self,
        cdef: _ChapterDef,
        xobjects: list[XObject],
        page_objs: dict[int, list[XObject]],
        total_pages: int,
    ) -> int | None:
        """根据 pages 配置（含 -1、keywords 反向扩展）解析锚点 xobject 索引"""
        p = cdef.pages[0]
        if p == -1:
            p = total_pages
        if cdef.keyword_patterns:
            if not self._page_matches_keywords(cdef, p, page_objs):
                return None
            while p > 1 and self._page_matches_keywords(cdef, p - 1, page_objs):
                p -= 1
        return self._first_index_on_page(xobjects, p)

    def _rule_matches(
        self,
        cdef: _ChapterDef,
        xobj: XObject,
        page_objs: dict[int, list[XObject]],
    ) -> bool:
        """顺序扫描使用的匹配判定：标题正则 或 (页面首对象 + 关键字匹配)"""
        if cdef.title_patterns and isinstance(xobj, XText):
            if cdef.match_title(xobj):
                return True
        if cdef.keyword_patterns:
            pns = xobj.page_numbers
            if pns:
                pn = pns[0]
                objs_on_page = page_objs.get(pn, [])
                if objs_on_page and objs_on_page[0] is xobj:
                    if self._page_matches_keywords(cdef, pn, page_objs):
                        return True
        return False

    def _find_toc_end(
        self,
        start_idx: int,
        xobjects: list[XObject],
        page_objs: dict[int, list[XObject]],
        default_end_idx: int,
        total_pages: int,
    ) -> int:
        """从start_idx对应的xobject所在页向后扫描，返回TOC最后一个xobject之后的索引（exclusive）"""
        start_xobj = xobjects[start_idx]
        if not start_xobj.page_numbers:
            return default_end_idx
        last_toc_page = start_xobj.page_numbers[0]
        pn = last_toc_page + 1
        while pn <= total_pages:
            if self._page_looks_like_toc(pn, page_objs):
                last_toc_page = pn
                pn += 1
            else:
                break
        # 把last_toc_page（含）之前的xobjects都纳入，遇到更后面的页则停止
        end_idx = start_idx + 1
        for i in range(start_idx, default_end_idx):
            xobj = xobjects[i]
            pns = xobj.page_numbers
            if not pns:
                continue
            if pns[0] > last_toc_page:
                break
            end_idx = i + 1
        return end_idx

    # 目录条目：标题尾部接省略号/空白后跟页码，如 "第一章 引言 ........ 12"
    _toc_line_pattern: Final = re.compile(r'.+[.\s…·]+\d+\s*$')

    def _page_looks_like_toc(
        self,
        pn: int,
        page_objs: dict[int, list[XObject]],
    ) -> bool:
        """判断该页是否为目录页：包含较多 '标题...页码' 形式的行"""
        texts = [xobj for xobj in page_objs.get(pn, []) if isinstance(xobj, XText)]
        if not texts:
            return False
        matched = sum(1 for t in texts if self._toc_line_pattern.search(t.text))
        return matched >= max(3, len(texts) // 3)

    def _first_index_on_page(self, xobjects: list[XObject], pn: int) -> int | None:
        """获取指定页面上第一个xobject在xobjects中的索引"""
        for i, xobj in enumerate(xobjects):
            if pn in xobj.page_numbers:
                return i
        return None

    def _page_matches_keywords(
        self,
        cdef: _ChapterDef,
        pn: int,
        page_objs: dict[int, list[XObject]],
    ) -> bool:
        """判断该页中有多少个XText对象命中关键字"""
        texts = [
            xobj.text for xobj in page_objs.get(pn, []) if isinstance(xobj, XText)
        ]
        return cdef.match_keywords(texts)
