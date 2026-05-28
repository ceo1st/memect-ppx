import copy
from enum import StrEnum, auto
import logging
import re
from dataclasses import dataclass
from typing import Any, Final, Mapping, Sequence

from memect.base import strs

from .xbase import XNo, XNode, XObject, XText, XTree


template = {
    "toc": {
        "enable": True,
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
    },
    "chapters": [
        {"title": "<首页>", "pages": [1]},
        {"style": {"bold": True, "fontsize": 20, "position": "center"}},
        {
            "titles": [
                r"重大事项提示|特别提示|重要提示",
                r"声明与承诺|声明",
                r"释义",
                r"前言",
            ]
        },
        {
            "titles": [
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
            ]
        },
        {"titles": [r"附件.+"]},
    ],
}

template2 = {
    "chapters": [
        {
            "title": "<首页>",
            # 有些可能有1-n页
            # "pages": [1],
        },
        {
            # 表示为目录章节，优先识别，不一定有
            "type": "toc"
        },
        # 不定义也可以
        {"title": "<正文>"},
        {
            "title": "<结尾>",
            # 表示在最后一页
            "pages": [-1],
            # 表示需要满足的正则表达式
            "keywords": [
                r"分析师声明|免责声明|法律声明",
                r"信息披露",
                r"评级说明|股票投资评级说明",
            ],
            # 表示需要至少满足2个关键字
            "min_keyword_size": 2,
        },
    ],
}


template3 = {
    # 如：信函等，就只是为一个总的章节，可以添加一个逻辑标题，如：<正文>
    # 或者就不需要了，直接添加到root下即可
    "chapters": [
        {"title": "<首页>", "pages": [1, 1]},
        {"title": "<信函>"},
        {"type": "toc"},
        {"title": "<正文>"},
    ]
}




default_template: dict[str, Any] = {
    "chapters": [
        {"title": "<首页>","type":"plain"},
        {
            "titles": [
                r"重大事项提示|特别提示|重要提示",
                r"声明与承诺|声明",
                r"释义",
                r"前言",
                r".*跟踪评级报告",
                  r".*评级报告",
            ],
            # 或者满足style的也是标题？
            "style": {
                "align": "center",
                "bold": False,
                "min_length": 2,
                "max_length": 80,
                "top": True,
                "top_ratio": 0.35,
            },
            "deep":False,

        },
        {
            # 目录，可以有0-n个目录章节
            "type": "toc"
        },
        {
            "titles": [
                r"^[第]?[\s]*([一二三四五六七八九十]{1,3})[\s]*[章]",
                r"^[第][\s]*([一二三四五六七八九十]{1,3})[\s]*[节]",
                r"^[第][\s]*([一二三四五六七八九十]{1,3})[\s]*[部]",
                r"^[第][\s]*([0-9]+)[\s]*部分",
                # ===============================
                r"^第([1-9][0-9]?)节",
            ],
            "style":{},
            "deep": True,
        },{
            "titles":[
                r'附件.+',
                r'附录.+'
            ],
            "deep":True
        },{
            "titles":[
                r'权利与免责声明'
            ],
            'deep':False
        }
    ]
}


class ChapterType(StrEnum):
    TOC=auto()
    """表示为目录章节，不需要解析层次"""
    PLAIN=auto()
    """表示为不需要解析层次，如：封面/首页等"""
    NORMAL=auto()
    """表示需要解析层次"""

class Chapter:
    def __init__(
        self,
        title: XText,
        start: int,
        end: int,
        *,
        anchor: int | None = None,
        fixed_end: bool = False,
        type:ChapterType=ChapterType.NORMAL
    ):
        super().__init__()
        self.title = title
        """章节标题。可以是原文标题，也可以是逻辑标题。"""
        self.start = start
        """第一个正文对象下标，包含。"""
        self.end = end
        """最后一个正文对象之后的下标，不包含。"""
        self.anchor = start if anchor is None else anchor
        """章节边界下标；原文标题章节为标题自身下标，逻辑章节为start。"""
        self.fixed_end = fixed_end
        """True表示end由明确规则决定，不再自动扩展到下一个章节。"""
        self.type=type
        """表示章节的类型"""
        self.provisional_end = False
        """True表示end只是当前搜索上界，后续章节还没确定。"""


class ChapterSpec:
    def __init__(self, data: Mapping[str, Any] | None = None):
        super().__init__()
        data = data or {}
        self.title: str | None = None
        """表示为逻辑标题"""
        self.titles: list[re.Pattern[str]] = []
        """标题的正则表达式"""
        self.style: Any = {}
        """如果定义了，文本满足条件的也认为是标题"""
        self.pages: Sequence[int] = []
        """[1]表示从第一页开始，结尾为下一个章节开始，[1,2]表示第一页和第二页，[-1]表示最后一页"""
        self.keywords: list[re.Pattern[str]] = []
        self.min_keyword_size: int = 2
        """表示需要满足多少个不同的keyword正则表达式"""

        self.type=ChapterType.NORMAL
        """toc表示为目录章节，normal表示普通章节，plain表示为不需要解析层次结构"""

        self.chapters: list[Chapter] = []
        """找到的章节"""
        self.done = False
        """True表示已经查找过了"""

        self.deep = True
        """True表示在根据正则表达式查找标题的时候，需要全页查找，否则仅仅查找每一页的第一个文本"""

        if data:
            self.title = data.get("title")
            self.titles = self.compile_patterns(data.get("titles") or [])
            self.style = data.get("style") or {}
            self.pages = list(data.get("pages") or [])
            self.keywords = self.compile_patterns(data.get("keywords") or [])
            self.min_keyword_size = int(data.get("min_keyword_size", 2))
            self.type = ChapterType(data.get("type", "normal"))
            self.deep = bool(data.get("deep", True))

    @classmethod
    def compile_patterns(
        cls, patterns: Sequence[str | re.Pattern[str]]
    ) -> list[re.Pattern[str]]:
        result: list[re.Pattern[str]] = []
        for pattern in patterns:
            if isinstance(pattern, str):
                pattern = re.compile(pattern)
            result.append(pattern)
        return result

    def match_title(self, xtext: XText) -> bool:
        if not self.titles and not self.style:
            return False
        if self.style and not self._match_style(xtext):
            return False
        if self.titles:
            text = strs.NText.get(xtext.text, mode="q2b", space="remove").text
            return any(
                self._match_pattern(pattern, text) for pattern in self.titles
            )
        return bool(self.style)

    def match_keywords(self, texts: Sequence[str]) -> bool:
        if not self.keywords:
            return False
        matched: set[int] = set()
        for text in texts:
            for i, pattern in enumerate(self.keywords):
                if i in matched:
                    continue
                if self._match_pattern(pattern, text):
                    matched.add(i)
            if len(matched) >= self.min_keyword_size:
                return True
        return False

    def matches_top_style(self) -> bool:
        return bool(self.style.get("top")) if self.style else False

    def _match_style(self, xtext: XText) -> bool:
        if not self.style:
            return True

        text = xtext.text.strip()
        min_length = self.style.get("min_length")
        if min_length is not None and len(text) < int(min_length):
            return False
        max_length = self.style.get("max_length")
        if max_length is not None and len(text) > int(max_length):
            return False

        bold = self.style.get("bold")
        if bold is True and not self._is_bold(xtext):
            return False

        fontsize = self.style.get("fontsize")
        if fontsize is not None and not self._match_fontsize(xtext, float(fontsize)):
            return False

        align = self.style.get("align", self.style.get("position"))
        if align is not None and not self._match_align(xtext, str(align)):
            return False

        if self.style.get("top") and not self._match_top(
            xtext, float(self.style.get("top_ratio", 0.35))
        ):
            return False

        return True

    def _is_bold(self, xtext: XText) -> bool:
        if bool(getattr(xtext, "bold", False)):
            return True
        for obj in xtext.objects:
            if bool(getattr(obj, "bold", False)):
                return True
            chars = getattr(obj, "chars", ())
            if chars and any(bool(getattr(char, "bold", False)) for char in chars):
                return True
        return False

    def _match_fontsize(self, xtext: XText, min_size: float) -> bool:
        sizes: list[float] = []
        for obj in xtext.objects:
            size = getattr(obj, "fontsize", None)
            if size is None:
                size = getattr(obj, "font_size", None)
            if size is not None:
                sizes.append(float(size))
                continue
            chars = getattr(obj, "chars", ())
            for char in chars:
                char_size = getattr(char, "fontsize", None)
                if char_size is None:
                    char_size = getattr(char, "font_size", None)
                if char_size is None:
                    char_size = getattr(char.bbox, "height", None)
                if char_size is not None:
                    sizes.append(float(char_size))
        return bool(sizes) and max(sizes) >= min_size

    def _match_align(self, xtext: XText, align: str) -> bool:
        if not xtext.objects:
            return False
        align = align.lower()
        if align not in {"center", "left", "right"}:
            return True

        bbox = xtext.objects[0].bbox
        page = xtext.objects[0].page
        page_bbox = getattr(getattr(page, "body", None), "content_bbox", None)
        if page_bbox is None:
            page_bbox = getattr(getattr(page, "body", None), "bbox", None)
        if page_bbox is None:
            page_bbox = page.bbox

        width = max(page_bbox.x1 - page_bbox.x0, 1)
        tol = width * 0.08
        if align == "center":
            return abs(bbox.cx - page_bbox.cx) <= tol
        if align == "left":
            return abs(bbox.x0 - page_bbox.x0) <= tol
        return abs(page_bbox.x1 - bbox.x1) <= tol

    def _match_top(self, xtext: XText, ratio: float) -> bool:
        if not xtext.objects:
            return False
        obj = xtext.objects[0]
        bbox = obj.bbox
        page = obj.page
        page_bbox = getattr(getattr(page, "body", None), "content_bbox", None)
        if page_bbox is None:
            page_bbox = getattr(getattr(page, "body", None), "bbox", None)
        if page_bbox is None:
            page_bbox = page.bbox

        ratio = min(max(ratio, 0.0), 1.0)
        min_y = page_bbox.y1 - page_bbox.height * ratio
        return bbox.y1 >= min_y

    def _match_pattern(self, pattern: re.Pattern[str], text: str) -> bool:
        return bool(pattern.fullmatch(text) or pattern.match(text))


class DocSpec:
    def __init__(self, data: Mapping[str, Any] | None = None):
        super().__init__()
        self.chapters: list[ChapterSpec] = []
        if data:
            self.chapters = [
                ChapterSpec(chapter) for chapter in data.get("chapters", [])
            ]

    def get_start(self, index: int) -> int:
        """获得指定章节的开始index"""
        i = index - 1
        while i >= 0:
            chapter = self.chapters[i]
            if chapter.done and chapter.chapters:
                last = chapter.chapters[-1]
                if last.provisional_end:
                    return last.start
                return last.end
            i -= 1
        return 0

    def get_end(self, index: int, end: int) -> int:
        i = index + 1
        while i < len(self.chapters):
            chapter = self.chapters[i]
            if chapter.done and chapter.chapters:
                return chapter.chapters[0].anchor
            i += 1
        return end


class Parser:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self, args: Mapping[str, Any] | None = None):
        super().__init__()


    def parse(self, xtree: XTree) -> bool:
        template = xtree.doc.params.tree.template 
        if not template:
            template = default_template
        elif not template.get('chapters'):
            #如果没有定义，就是对default_template的补充
            titles1 = template.get('titles1')
            titles2 = template.get('titles2')
            template=copy.deepcopy(default_template)
            if titles1:
                template['chapters'][1]['titles']=titles1
            if titles2:
                template['chapters'][3]['titles']=titles2
        else:
            pass
        doc_spec = DocSpec(template)
        
        xobjects = xtree.xobjects
        if not xobjects:
            return False

        # 1.解析指定页面范围的
        for i in range(len(doc_spec.chapters)):
            self._find_by_range(doc_spec, i, xobjects)

        # 2.解析目录
        for i in range(len(doc_spec.chapters)):
            self._find_tocs(doc_spec, i, xobjects)

        # 3.根据标题正则表达式/style找到标题
        for i in range(len(doc_spec.chapters)):
            self._find_by_titles(doc_spec, i, xobjects)

        # 4.根据逻辑标题划分
        for i in range(len(doc_spec.chapters)):
            self._find_by_title(doc_spec, i, xobjects)

        # 5.章节之间剩余的内容，都定义为正文
        chapters = self._normalize_chapters(doc_spec, len(xobjects))
        if not chapters:
            return False

        self._rebuild_tree(xtree, chapters)
        return True

    def _find_by_range(
        self, doc_spec: DocSpec, index: int, xobjects: Sequence[XObject]
    ):
        spec = doc_spec.chapters[index]
        if spec.done:
            return

        if not spec.pages:
            return

        if index + 1 >= len(xobjects):
            return

        # total_page = self._total_page(xobjects)
        total_page = xobjects[0].pages[0].doc.page_count
        start_page = self._resolve_page(spec.pages[0], total_page)
        if start_page is None:
            return

        fixed_end = len(spec.pages) >= 2
        end_page = (
            self._resolve_page(spec.pages[1], total_page) if fixed_end else start_page
        )
        if end_page is None:
            return
        if end_page == 0:
            fixed_end = False
            end_page = total_page

        start = self._first_index_on_page(xobjects, start_page)
        if start is None:
            return

        if spec.keywords and not self._range_matches_keywords(
            spec, start_page, end_page, xobjects
        ):
            return

        end = start
        search_end = doc_spec.get_end(index, len(xobjects))
        for i in range(start, search_end):
            xobj = xobjects[i]
            if not xobj.page_numbers:
                continue
            if start_page <= xobj.page_numbers[0] and xobj.page_numbers[-1] <= end_page:
                end = i + 1
            else:
                if xobj.page_numbers[0] > end_page:
                    break

        if end - start <= 0:
            return

        spec.chapters.append(
            Chapter(
                XText.create_title(spec.title or "<正文>"),
                start,
                end,
                fixed_end=fixed_end,
                type=spec.type
            )
        )
        spec.done = True

    def _find_tocs(self, doc_spec: DocSpec, index: int, xobjects: Sequence[XObject]):
        if index + 1 >= len(xobjects):
            return

        spec = doc_spec.chapters[index]
        if spec.done:
            return
        if spec.type != ChapterType.TOC:
            return

        start = doc_spec.get_start(index)
        end = doc_spec.get_end(index, len(xobjects))
        tocs = self._find_toc_chapters(spec, start, end, xobjects)

        for i in range(len(tocs)):
            toc = tocs[i]
            if i - 1 >= 0 and toc.start > tocs[i - 1].end:
                # 目录1
                # <正文> => 如果有多个目录，那么，中间的都认为是正文封装
                # 目录2
                spec.chapters.append(
                    Chapter(
                        XText.create_title("<正文>"),
                        tocs[i - 1].end,
                        toc.anchor,
                        fixed_end=True,
                    )
                )
            spec.chapters.append(toc)
        # if spec.chapters:
        # 不管是否有目录章节，读标记完成
        spec.done = True

    def _find_toc_chapters(
        self, spec: ChapterSpec, start: int, end: int, xobjects: Sequence[XObject]
    ) -> list[Chapter]:
        from .xtoc import XTOCParser

        chapters: list[Chapter] = []
        for toc_start, toc_end, _ in XTOCParser().find(xobjects[start:end]):
            anchor = start + toc_start
            end_index = start + toc_end
            title_obj = xobjects[anchor]
            if isinstance(title_obj, XText):
                title = title_obj
                content_start = anchor + 1
            else:
                title = XText.create_title(spec.title or "<目录>")
                content_start = anchor
            chapters.append(
                Chapter(
                    title,
                    content_start,
                    end_index,
                    anchor=anchor,
                    fixed_end=True,
                    type=ChapterType.TOC
                )
            )
        return chapters

    def _find_by_titles(
        self, doc_spec: DocSpec, index: int, xobjects: Sequence[XObject]
    ):
        spec = doc_spec.chapters[index]
        if spec.done:
            return
        if not spec.titles and not spec.style:
            return

        start = doc_spec.get_start(index)
        end = doc_spec.get_end(index, len(xobjects))

        titles: list[XText] = []
        i = start
        while i < end:
            xobj = xobjects[i]
            if not isinstance(xobj, XText):
                i += 1
                continue
            if (
                not spec.deep
                and not spec.matches_top_style()
                and not self._is_first_object(xobj)
            ):
                i += 1
                continue
            # 判断是否为标题，如果是
            if spec.match_title(xobj):
                titles.append(xobj)
            i += 1

        # 找到标题，可以检查是否正确
        for k, title in enumerate(titles):
            anchor = xobjects.index(title)
            i = anchor + 1
            if k + 1 < len(titles):
                j = xobjects.index(titles[k + 1])
            else:
                j = end
            chapter = Chapter(title, i, j, anchor=anchor,type=spec.type)
            spec.chapters.append(chapter)
        
        if spec.chapters and self._has_pending_next_spec(doc_spec, index):
            spec.chapters[-1].provisional_end = True

        #不管是否有章节，都标记为done
        spec.done = True

    def _has_pending_next_spec(self, doc_spec: DocSpec, index: int) -> bool:
        for spec in doc_spec.chapters[index + 1 :]:
            if spec.done and spec.chapters:
                return False
            if not spec.done:
                return True
        return False

    def _find_by_title(
        self, doc_spec: DocSpec, index: int, xobjects: Sequence[XObject]
    ):
        spec = doc_spec.chapters[index]
        if spec.done:
            return
        if not spec.title:
            return
        if (
            spec.pages
            or spec.titles
            or spec.style
            or spec.keywords
            or spec.type == ChapterType.TOC
        ):
            return

        start = doc_spec.get_start(index)
        end = doc_spec.get_end(index, len(xobjects))
        if start >= end:
            return
        spec.chapters.append(Chapter(XText.create_title(spec.title), start, end,type=spec.type))
        spec.done = True

    def _normalize_chapters(self, doc_spec: DocSpec, end: int) -> list[Chapter]:
        chapters: list[Chapter] = []
        for spec in doc_spec.chapters:
            chapters.extend(spec.chapters)
        if not chapters:
            return []

        chapters.sort(key=lambda c: (c.anchor, c.start, c.end))
        unique: list[Chapter] = []
        seen: set[tuple[int, int, str]] = set()
        for chapter in chapters:
            key = (chapter.anchor, chapter.start, chapter.title.text)
            if key in seen:
                continue
            seen.add(key)
            unique.append(chapter)

        for i, chapter in enumerate(unique):
            next_anchor = unique[i + 1].anchor if i + 1 < len(unique) else end
            if not chapter.fixed_end:
                chapter.end = next_anchor
            else:
                chapter.end = min(chapter.end, next_anchor)
            chapter.start = max(0, min(chapter.start, end))
            chapter.end = max(0, min(chapter.end, end))
            chapter.provisional_end = False

        normalized = [
            chapter
            for chapter in unique
            if chapter.start < chapter.end or chapter.title.objects
        ]
        return self._fill_body_chapters(normalized, end)

    def _fill_body_chapters(
        self, chapters: Sequence[Chapter], end: int
    ) -> list[Chapter]:
        result: list[Chapter] = []
        cursor = 0
        for chapter in chapters:
            chapter_start = self._chapter_coverage_start(chapter)
            if cursor < chapter_start:
                result.append(
                    Chapter(
                        XText.create_title("<正文>"),
                        cursor,
                        chapter_start,
                        fixed_end=True,
                    )
                )
            result.append(chapter)
            cursor = max(cursor, self._chapter_coverage_end(chapter))
        if cursor < end:
            result.append(
                Chapter(
                    XText.create_title("<正文>"),
                    cursor,
                    end,
                    fixed_end=True,
                )
            )
        return result

    def _chapter_coverage_start(self, chapter: Chapter) -> int:
        if chapter.title.objects:
            return chapter.anchor
        return chapter.start

    def _chapter_coverage_end(self, chapter: Chapter) -> int:
        if chapter.title.objects:
            return max(chapter.end, chapter.anchor + 1)
        return chapter.end

    def _rebuild_tree(self, xtree: XTree, chapters: Sequence[Chapter]):
        strict=True
        used: set[XObject] = set()
        for xobj in xtree.xobjects:
            xobj.node.deatch()

        for chapter in chapters:
            title = chapter.title
            if title.objects:
                title.as_title()
                used.add(title)
            xtree.root.add(title)

            children: list[XObject] = []
            for xobj in xtree.xobjects[chapter.start : chapter.end]:
                #TODO 如果出现下面的，应该是代码写错误了
                if xobj is title or xobj in used:
                    if strict:
                        raise RuntimeError('代码写错了')
                    continue
                children.append(xobj)
                used.add(xobj)
            if children:
                title.node.add(*children)
            
            if chapter.type==ChapterType.NORMAL:
                ChapterParser().parse(title.node)
        
        #
        remains = [xobj for xobj in xtree.xobjects if xobj not in used]
        if strict and remains:
            raise RuntimeError('代码写错了')
        if remains:
            xtree.root.add(*remains)

    def _range_matches_keywords(
        self,
        spec: ChapterSpec,
        start_page: int,
        end_page: int,
        xobjects: Sequence[XObject],
    ) -> bool:
        texts: list[str] = []
        for xobj in xobjects:
            if not isinstance(xobj, XText):
                continue
            if not xobj.page_numbers:
                continue
            if start_page <= xobj.page_numbers[0] <= end_page:
                texts.append(xobj.text)
        return spec.match_keywords(texts)

    def _total_page(self, xobjects: Sequence[XObject]) -> int:
        pages = [pn for xobj in xobjects for pn in xobj.page_numbers]
        return max(pages) if pages else 0

    def _resolve_page(self, page: int, total_page: int) -> int | None:
        if page == 0:
            return 0
        if page < 0:
            page = total_page + page + 1
        if page <= 0:
            return None
        return page

    def _first_index_on_page(
        self, xobjects: Sequence[XObject], page_number: int
    ) -> int | None:
        for i, xobj in enumerate(xobjects):
            if page_number in xobj.page_numbers:
                return i
        return None

    def _is_first_object(self, xtext: XText) -> bool:
        if not xtext.objects:
            return False
        obj = xtext.objects[0]
        try:
            return obj.page.objects.index(obj) == 0
        except ValueError:
            return False


@dataclass
class _TitleInfo:
    node: XNode
    xtext: XText
    no: XNo | None
    style_key: str | None
    compact_value: int | None = None


@dataclass
class _NoCandidate:
    text: str
    parse_text: str
    style_key: str
    compact_value: int | None = None


class ChapterParser:
    _no_extract_patterns: Final = (
        re.compile(
            r"^\s*(?P<no>第\s*[0-9〇一二三四五六七八九十零百千]+\s*"
            r"(?:部分|章节|章|节|篇|部|条|款|项))"
        ),
        re.compile(r"^\s*(?P<no>\d+(?:[.．]\d+)+(?:[.．])?)"),
        re.compile(r"^\s*(?P<no>\d+\s*[、.．])"),
        re.compile(r"^\s*(?P<no>[〇一二三四五六七八九十零百千]+\s*[、.．])"),
        re.compile(
            r"^\s*(?P<no>[（(]\s*(?:[0-9〇一二三四五六七八九十零百千]+|[A-Za-z])\s*[）)])"
        ),
        re.compile(r"^\s*(?P<no>[\u2460-\u2473\u2474-\u2487])"),
        re.compile(
            r"^\s*(?P<no>(?:I{1,3}|IV|VI{0,3}|IX|XI{0,3}|XIV|XV|XVI{0,3}|XIX|XX)"
            r"\s*[.．、])",
            re.IGNORECASE,
        ),
        re.compile(r"^\s*(?P<no>[A-Za-z]\s*[.．、])"),
    )
    _compact_number_pattern: Final = re.compile(
        r"^\s*(?P<value>[1-9]\d{0,2})(?P<body>\s*[\u4e00-\u9fff].*)$"
    )
    _compact_number_reject_pattern: Final = re.compile(r"^[年月日号%％万亿千百]")

    def __init__(self):
        super().__init__()

    def parse(self, node: XNode) -> bool:
        children = list(node.children)
        if not children:
            return False

        title_infos: dict[XNode, _TitleInfo] = {}
        compact_title_infos: dict[XNode, _TitleInfo] = {}
        for child in children:
            info = self._parse_title_info(child)
            if info is None:
                continue
            if info.compact_value is None or info.xtext.is_title():
                title_infos[child] = info
                continue
            compact_title_infos[child] = info

        self._supplement_compact_parent_titles(
            children,
            title_infos,
            compact_title_infos,
        )

        if not title_infos:
            return False

        for child in children:
            child.deatch()

        stack: list[XNode] = [node]
        style_levels: dict[str, int] = {}
        level_styles: dict[int, str] = {}

        for child in children:
            info = title_infos.get(child)
            if info is None:
                parent = stack[-1] if len(stack) > 1 else node
                parent.add(child)
                continue

            level = self._infer_level(info, stack, style_levels, level_styles)
            level = max(1, min(level, len(stack)))
            while len(stack) > level:
                stack.pop()

            if info.no is not None:
                info.xtext.no = info.no
            info.xtext.as_title()
            stack[-1].add(child)
            stack.append(child)

            if info.style_key is not None:
                style_levels[info.style_key] = level
                level_styles[level] = info.style_key

        return True

    def _parse_title_info(self, child: XNode) -> _TitleInfo | None:
        if not child.is_text():
            return None

        xtext = child.text
        if xtext.no is not None:
            return _TitleInfo(
                node=child,
                xtext=xtext,
                no=xtext.no,
                style_key=self._style_key(xtext.no.text),
            )

        candidate = self._extract_no(xtext.text)
        if candidate is not None:
            no = XNo.parse(
                candidate.parse_text,
                xobject=xtext,
                roman=candidate.style_key == "roman.",
            )
            if no is not None:
                return _TitleInfo(
                    node=child,
                    xtext=xtext,
                    no=no,
                    style_key=candidate.style_key,
                    compact_value=candidate.compact_value,
                )

        if xtext.is_title():
            return _TitleInfo(node=child, xtext=xtext, no=None, style_key=None)
        return None

    def _extract_no(self, text: str) -> _NoCandidate | None:
        text = self._normalize_title_text(text)
        for pattern in self._no_extract_patterns:
            match = pattern.match(text)
            if match is not None:
                no_text = match.group("no")
                style_key = self._style_key(no_text)
                body = text[match.end() :]
                if not self._is_valid_title_no_context(no_text, style_key, body):
                    continue
                return _NoCandidate(
                    text=no_text,
                    parse_text=no_text,
                    style_key=style_key,
                )
        compact_match = self._compact_number_pattern.match(text)
        if compact_match is not None:
            value = int(compact_match.group("value"))
            body = compact_match.group("body").strip()
            if self._looks_like_compact_number_title_body(body):
                return _NoCandidate(
                    text=compact_match.group("value"),
                    parse_text=f"{value}.",
                    style_key="number.",
                    compact_value=value,
                )
        return None

    def _looks_like_compact_number_title_body(self, body: str) -> bool:
        if not body:
            return False
        if self._compact_number_reject_pattern.match(body):
            return False
        if len(body) > 40:
            return False
        return not bool(re.search(r"[，,。；;]", body))

    def _is_valid_title_no_context(
        self, no_text: str, style_key: str, body: str
    ) -> bool:
        if style_key in {"number.", "number.dotted"}:
            if self._looks_like_non_title_number_body(body):
                return False
        if style_key == "paren.number":
            value_text = self._normalize_no_text(no_text).strip("()")
            if len(value_text) > 3:
                return False
            if body and not self._looks_like_title_body_after_no(body):
                return False
        return True

    def _looks_like_title_body_after_no(self, body: str) -> bool:
        if not body:
            return True
        if re.match(r"^[0-9+\-*/×xX（）()]", body):
            return False
        return True

    def _looks_like_non_title_number_body(self, body: str) -> bool:
        if not body:
            return False
        return bool(
            re.match(r"^\d", body)
            or re.match(r"^[-－–—~～至到]", body)
        )

    def _supplement_compact_parent_titles(
        self,
        children: Sequence[XNode],
        title_infos: dict[XNode, _TitleInfo],
        compact_title_infos: dict[XNode, _TitleInfo],
    ):
        for index, child in enumerate(children):
            info = title_infos.get(child)
            if info is None or info.style_key != "number.dotted":
                continue
            parent_value = self._dotted_parent_value(info)
            if parent_value is None:
                continue
            if self._has_number_parent_before(
                children,
                index,
                parent_value,
                title_infos,
            ):
                continue
            parent_info = self._find_compact_parent_before(
                children,
                index,
                parent_value,
                title_infos,
                compact_title_infos,
            )
            if parent_info is not None:
                title_infos[parent_info.node] = parent_info

    def _has_number_parent_before(
        self,
        children: Sequence[XNode],
        index: int,
        value: int,
        title_infos: dict[XNode, _TitleInfo],
    ) -> bool:
        for prev_child in reversed(children[:index]):
            prev_info = title_infos.get(prev_child)
            if prev_info is None or prev_info.no is None:
                continue
            if prev_info.style_key == "number." and prev_info.no.value == value:
                return True
        return False

    def _find_compact_parent_before(
        self,
        children: Sequence[XNode],
        index: int,
        value: int,
        title_infos: dict[XNode, _TitleInfo],
        compact_title_infos: dict[XNode, _TitleInfo],
    ) -> _TitleInfo | None:
        for prev_child in reversed(children[max(0, index - 8) : index]):
            prev_title = title_infos.get(prev_child)
            if prev_title is not None:
                if prev_title.style_key == "number.":
                    break
                if (
                    prev_title.style_key == "number.dotted"
                    and self._dotted_parent_value(prev_title) != value
                ):
                    break

            compact_info = compact_title_infos.get(prev_child)
            if compact_info is not None and compact_info.compact_value == value:
                return compact_info
        return None

    def _dotted_parent_value(self, info: _TitleInfo) -> int | None:
        if info.no is None:
            return None
        text = self._normalize_no_text(info.no.text)
        match = re.match(r"(?P<value>\d+)[.]\d+", text)
        if match is None:
            return None
        return int(match.group("value"))

    def _infer_level(
        self,
        info: _TitleInfo,
        stack: Sequence[XNode],
        style_levels: dict[str, int],
        level_styles: dict[int, str],
    ) -> int:
        if info.no is not None and info.no.level > 1:
            return info.no.level

        if info.style_key is None:
            return len(stack) if len(stack) > 1 else 1

        rank = self._style_rank(info.style_key)
        current_level = len(stack) - 1
        if current_level <= 0:
            return 1
        if rank <= 1:
            return 1

        current_style = level_styles.get(current_level)
        current_rank = self._style_rank(level_styles.get(current_level))
        if self._starts_new_child_sequence(info, current_style, rank, current_rank):
            return current_level + 1

        if info.style_key in style_levels:
            return style_levels[info.style_key]

        if current_rank == 0:
            return current_level + 1
        if rank > current_rank:
            return current_level + 1
        if rank == current_rank:
            return current_level

        parent_level = 0
        for level in range(current_level, 0, -1):
            level_rank = self._style_rank(level_styles.get(level))
            if level_rank and level_rank < rank:
                parent_level = level
                break
        return parent_level + 1

    def _starts_new_child_sequence(
        self,
        info: _TitleInfo,
        current_style: str | None,
        rank: int,
        current_rank: int,
    ) -> bool:
        return (
            info.no is not None
            and info.no.value == 1
            and info.style_key != current_style
            and current_rank > 0
            and rank >= current_rank
        )

    def _style_key(self, no_text: str) -> str:
        text = self._normalize_no_text(no_text)

        match = re.fullmatch(
            r"第[0-9〇一二三四五六七八九十零百千]+(?P<unit>部分|章节|章|节|篇|部|条|款|项)",
            text,
        )
        if match is not None:
            return f"第#{match.group('unit')}"
        if re.fullmatch(r"\d+(?:\.\d+)+(?:\.)?", text):
            return "number.dotted"
        if re.fullmatch(r"\d+、", text):
            return "number、"
        if re.fullmatch(r"\d+\.", text):
            return "number."
        if re.fullmatch(r"[〇一二三四五六七八九十零百千]+、", text):
            return "chinese、"
        if re.fullmatch(r"[〇一二三四五六七八九十零百千]+\.", text):
            return "chinese."
        if re.fullmatch(r"[（(][0-9]+[）)]", text):
            return "paren.number"
        if re.fullmatch(r"[（(][〇一二三四五六七八九十零百千]+[）)]", text):
            return "paren.chinese"
        if re.fullmatch(r"[（(][A-Za-z][）)]", text):
            return "paren.alpha"
        if re.fullmatch(r"[\u2460-\u2473]", text):
            return "circled.number"
        if re.fullmatch(r"[\u2474-\u2487]", text):
            return "paren.circled.number"
        if re.fullmatch(
            r"(?:I{1,3}|IV|VI{0,3}|IX|XI{0,3}|XIV|XV|XVI{0,3}|XIX|XX)[.、]",
            text,
            re.IGNORECASE,
        ):
            return "roman."
        if re.fullmatch(r"[A-Za-z][.、]", text):
            return "alpha."
        return text

    def _style_rank(self, style_key: str | None) -> int:
        if style_key is None:
            return 0
        if style_key in {"第#篇", "第#部分", "第#章节", "第#章", "第#部"}:
            return 1
        if style_key in {
            "第#节",
            "第#条",
            "第#款",
            "第#项",
            "chinese、",
            "chinese.",
            "roman.",
        }:
            return 2
        if style_key in {"paren.chinese"}:
            return 3
        if style_key in {"number.dotted", "number.", "number、"}:
            return 4
        if style_key in {"paren.number", "circled.number", "paren.circled.number"}:
            return 5
        if style_key in {"alpha.", "paren.alpha"}:
            return 6
        return 10

    def _normalize_no_text(self, text: str) -> str:
        return (
            self._normalize_title_text(text)
            .replace("．", ".")
            .replace("（", "(")
            .replace("）", ")")
            .replace("，", ",")
        )

    def _normalize_title_text(self, text: str) -> str:
        return strs.NText.get(text, mode="q2b", space="remove").text
