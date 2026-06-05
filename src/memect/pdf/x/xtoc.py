import logging
import re
from dataclasses import dataclass, field
from typing import Sequence

from memect.pdf.base import KText, KTextline
from memect.pdf.x.xbase import XObject, XText


@dataclass
class XTOCItem:
    title: str
    no: str
    level: int
    xtext: XText
    page: int | None = None
    title_xobj: XText | None = None
    children: list["XTOCItem"] = field(default_factory=list)


class XTOCParser:
    """识别文档目录范围，并把目录内容解析为目录项。"""

    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _page_token_pattern = (
        r"(?:"
        r"\d{1,5}"
        r"|[ivxlcdmIVXLCDM]{1,12}"
        r"|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]{1,8}"
        r"|[一二三四五六七八九十百千万零〇]{1,12}"
        r"|错误\s*!\s*未定义书签。?"
        r")"
    )
    _page_ref_pattern = re.compile(
        rf"(?:[-－–—−]\s*)?(?P<page>{_page_token_pattern})(?:\s*[-－–—−])?\s*$"
    )
    _leader_page_pattern = re.compile(
        rf"(?:[.．…·•⋯]{{2,}}|[-－–—−_]{{2,}}|\s{{2,}}|\t+)\s*"
        rf"(?:[-－–—−]\s*)?(?P<page>{_page_token_pattern})(?:\s*[-－–—−])?\s*$"
    )
    _toc_page_ref_pattern = (
        rf"(?:[-－–—−]\s*)?{_page_token_pattern}(?:\s*[-－–—−])?"
    )
    _item_end_pattern = re.compile(
        rf"^.+?(?:[.．…·•⋯]{{2,}}|[-－–—−_]{{2,}}|\s{{2,}}|\t+)\s*"
        rf"{_toc_page_ref_pattern}\s*$"
    )
    _loose_item_end_pattern = re.compile(
        rf"^\s*(?:"
        r"第[一二三四五六七八九十百千万零〇\d]+[章节篇部分]"
        r"|[一二三四五六七八九十百千万零〇]+[、.．]"
        r"|\d+(?:\.\d+)*[、.．]?"
        r"|[（(][一二三四五六七八九十百千万零〇\d]+[）)]"
        r"|图\s*\d+(?:[-.]\d+)*"
        r"|表\s*\d+(?:[-.]\d+)*"
        r"|figure\s+\d+(?:[-.]\d+)*"
        r"|table\s+\d+(?:[-.]\d+)*"
        rf").+\s+{_toc_page_ref_pattern}\s*$",
        re.IGNORECASE,
    )
    _compact_item_end_pattern = re.compile(
        rf"^\s*(?:"
        r"第[一二三四五六七八九十百千万零〇\d]+[章节篇部分]"
        r"|[一二三四五六七八九十百千万零〇]+[、.．]"
        r"|\d+(?:\.\d+)*[、.．]?"
        r"|[（(][一二三四五六七八九十百千万零〇\d]+[）)]"
        r"|图\s*\d+(?:[-.]\d+)*"
        r"|表\s*\d+(?:[-.]\d+)*"
        r"|figure\s+\d+(?:[-.]\d+)*"
        r"|table\s+\d+(?:[-.]\d+)*"
        rf").+[-－–—−]\s*{_page_token_pattern}\s*$",
        re.IGNORECASE,
    )
    _dangling_dash_pattern = re.compile(r"^[-－–—−]+$")
    _may_have_split_trailing_dash_pattern = re.compile(
        rf"[-－–—−]\s*{_page_token_pattern}\s*$"
    )
    _toc_no_pattern = re.compile(
        r"^\s*(?P<no>"
        r"第[一二三四五六七八九十百千万零〇\d]+[章节篇部分]"
        r"|\d+(?:\.\d+)*"
        r"|[一二三四五六七八九十百千万零〇]+"
        r"|[（(][一二三四五六七八九十百千万零〇\d]+[）)]"
        r")\s*[、.．]?\s*"
    )
    _figure_table_pattern = re.compile(
        r"^\s*(?:图|表|figure|table)\s*\d+(?:[-.]\d+)*\s*[、.．:：]?",
        re.IGNORECASE,
    )
    _chapter_toc_heading_pattern = re.compile(
        r"^(?:目录|正文目录|内容目录|章节目录|目次|目录索引|"
        r"contents|tableofcontents|目录contents|contents目录)$",
        re.IGNORECASE,
    )
    _non_chapter_toc_heading_pattern = re.compile(
        r"^(?:图文目录|图表目录|图目录|表目录|插图目录|表格目录|图表索引|"
        r"listoffigures|listoftables)$",
        re.IGNORECASE,
    )

    def find(
        self, xobjects: Sequence[XObject]
    ) -> list[tuple[int, int, list[XObject]]]:
        """找到所有目录章节范围，返回相对输入序列的[start, end, xobjects]。"""
        page_entries = self._group_page_entries(xobjects)
        page_numbers = sorted(page_entries)
        ranges: list[tuple[int, int, list[XObject]]] = []

        page_pos = 0
        while page_pos < len(page_numbers):
            page_number = page_numbers[page_pos]
            entries = page_entries[page_number]
            heading_index = self._find_toc_heading_index(entries, page_number)
            if heading_index is None:
                page_pos += 1
                continue

            toc_items: list[str] = []
            toc_end_page: int | None = None
            seen_toc = False
            scan_pos = page_pos

            while scan_pos < len(page_numbers):
                next_page = page_numbers[scan_pos]
                if toc_end_page is not None and next_page != toc_end_page + 1:
                    break

                if next_page != page_number:
                    next_heading_index = self._find_toc_heading_index(
                        page_entries[next_page], next_page
                    )
                    if next_heading_index is not None:
                        break

                start_index = heading_index if next_page == page_number else None
                page_items = self._parse_page_any_toc_items(
                    page_entries[next_page],
                    next_page,
                    start_index=start_index,
                )
                page_is_toc = self._looks_like_any_toc(page_items)

                if next_page == page_number:
                    toc_items.extend(page_items)
                    toc_end_page = next_page
                    seen_toc = page_is_toc or self._looks_like_any_toc(toc_items)
                    scan_pos += 1
                    continue

                if page_is_toc:
                    toc_items.extend(page_items)
                    toc_end_page = next_page
                    seen_toc = True
                    scan_pos += 1
                    continue

                if seen_toc and self._looks_like_any_toc_continuation(
                    page_items
                ):
                    toc_items.extend(page_items)
                    toc_end_page = next_page
                    scan_pos += 1
                    continue

                if not seen_toc and page_items:
                    toc_items.extend(page_items)
                    toc_end_page = next_page
                    seen_toc = self._looks_like_any_toc(toc_items)
                    scan_pos += 1
                    continue

                break

            if self._looks_like_any_toc(toc_items) and toc_end_page is not None:
                toc_end = page_entries[toc_end_page][-1][0] + 1
                ranges.append(
                    (heading_index, toc_end, list(xobjects[heading_index:toc_end]))
                )
                while page_pos < len(page_numbers) and page_numbers[page_pos] <= toc_end_page:
                    page_pos += 1
            else:
                page_pos += 1
        return ranges

    def parse(self, xobjects: Sequence[XObject]) -> list[XTOCItem]:
        """把目录范围内的对象解析为目录项。非文本对象会被跳过但不影响find范围。"""
        page_entries = self._group_page_entries(xobjects)
        toc_xtexts: list[XText] = []
        for page_number in sorted(page_entries):
            toc_xtexts.extend(
                self._parse_page_toc_items(page_entries[page_number], page_number)
            )
        return self._parse_toc_items(toc_xtexts)

    def build_tree(self, items: Sequence[XTOCItem]) -> list[XTOCItem]:
        roots: list[XTOCItem] = []
        stack: list[XTOCItem] = []
        for item in items:
            while stack and stack[-1].level >= item.level:
                stack.pop()
            if stack:
                stack[-1].children.append(item)
            else:
                roots.append(item)
            stack.append(item)
        return roots

    def strip_page_ref(self, text: str) -> str:
        return self._strip_page_ref(text)

    def strip_no(self, text: str) -> str:
        return self._toc_no_pattern.sub("", text, count=1)

    def normalize_space(self, text: str) -> str:
        return re.sub(r"\s+", " ", text.replace("\u3000", " ")).strip()

    def _group_page_entries(
        self, xobjects: Sequence[XObject]
    ) -> dict[int, list[tuple[int, XObject]]]:
        page_entries: dict[int, list[tuple[int, XObject]]] = {}
        for index, xobj in enumerate(xobjects):
            for page_number in xobj.page_numbers:
                page_entries.setdefault(page_number, []).append((index, xobj))
        return page_entries

    def _find_toc_heading_index(
        self, entries: Sequence[tuple[int, XObject]], page_number: int
    ) -> int | None:
        for index, xobj in entries[:5]:
            if not isinstance(xobj, XText):
                continue
            if self._is_chapter_toc_heading(xobj.text):
                return index
            for text in self._iter_xtext_line_texts(xobj, page_number):
                if self._is_chapter_toc_heading(text):
                    return index
        return None

    def _parse_page_toc_items(
        self,
        entries: Sequence[tuple[int, XObject]],
        page_number: int,
        *,
        start_index: int | None = None,
    ) -> list[XText]:
        texts: list[str] = []
        for index, xobj in entries:
            if start_index is not None and index < start_index:
                continue
            if not isinstance(xobj, XText):
                continue
            line_texts = self._iter_xtext_line_texts(xobj, page_number)
            if line_texts:
                texts.extend(line_texts)
            else:
                texts.append(xobj.text)
        return self._parse_toc_texts(texts)

    def _parse_page_any_toc_items(
        self,
        entries: Sequence[tuple[int, XObject]],
        page_number: int,
        *,
        start_index: int | None = None,
    ) -> list[str]:
        texts: list[str] = []
        for index, xobj in entries:
            if start_index is not None and index < start_index:
                continue
            if not isinstance(xobj, XText):
                continue
            line_texts = self._iter_xtext_line_texts(xobj, page_number)
            if line_texts:
                texts.extend(line_texts)
            else:
                texts.append(xobj.text)
        return self._parse_any_toc_texts(texts)

    def _parse_any_toc_texts(self, texts: Sequence[str]) -> list[str]:
        items: list[str] = []
        pending: list[str] = []
        wait_for_trailing_dash = False

        def flush_pending():
            nonlocal pending, wait_for_trailing_dash
            if not pending:
                return
            text = self.normalize_space(" ".join(pending))
            if self._has_page_ref(text) and not self._is_any_toc_heading(text):
                items.append(text)
            pending = []
            wait_for_trailing_dash = False

        for raw_text in texts:
            text = self.normalize_space(raw_text)
            if not text:
                continue

            if wait_for_trailing_dash:
                if self._is_dangling_dash(text):
                    pending.append(text)
                    flush_pending()
                    continue
                flush_pending()

            if self._is_any_toc_heading(text):
                flush_pending()
                continue

            if self._is_dangling_dash(text):
                if pending:
                    pending.append(text)
                    flush_pending()
                continue

            if self._is_page_number_only(text):
                if pending:
                    pending.append(text)
                    flush_pending()
                continue

            pending.append(text)
            if self._is_item_end(text):
                if self._may_have_split_trailing_dash(text):
                    wait_for_trailing_dash = True
                else:
                    flush_pending()

        flush_pending()
        return items

    def _parse_toc_texts(self, texts: Sequence[str]) -> list[XText]:
        items: list[XText] = []
        pending: list[str] = []
        wait_for_trailing_dash = False

        def flush_pending():
            nonlocal pending, wait_for_trailing_dash
            if not pending:
                return
            text = self.normalize_space(" ".join(pending))
            xtext = XText(text)
            if self._parse_toc_item(xtext) is not None:
                items.append(xtext)
            pending = []
            wait_for_trailing_dash = False

        for raw_text in texts:
            text = self.normalize_space(raw_text)
            if not text:
                continue

            if wait_for_trailing_dash:
                if self._is_dangling_dash(text):
                    pending.append(text)
                    flush_pending()
                    continue
                flush_pending()

            if self._is_any_toc_heading(text):
                flush_pending()
                continue

            if self._is_dangling_dash(text):
                if pending:
                    pending.append(text)
                    flush_pending()
                continue

            if self._is_page_number_only(text):
                if pending:
                    pending.append(text)
                    flush_pending()
                continue

            pending.append(text)
            if self._is_item_end(text):
                if self._may_have_split_trailing_dash(text):
                    wait_for_trailing_dash = True
                else:
                    flush_pending()

        flush_pending()
        return items

    def _iter_xtext_line_texts(self, xtext: XText, page_number: int) -> list[str]:
        return [
            line.text
            for line in self._get_lines(xtext)
            if line.page.number == page_number
        ]

    def _get_lines(self, xtext: XText) -> list[KTextline]:
        lines: list[KTextline] = []
        for obj in xtext.objects:
            if isinstance(obj, KText):
                lines.extend(obj.lines)
            elif isinstance(obj, KTextline):
                lines.append(obj)
        return lines

    def _is_any_toc_heading(self, text: str) -> bool:
        normalized = self._normalize_toc_heading(text)
        return bool(
            self._chapter_toc_heading_pattern.fullmatch(normalized)
            or self._non_chapter_toc_heading_pattern.fullmatch(normalized)
        )

    def _is_chapter_toc_heading(self, text: str) -> bool:
        normalized = self._normalize_toc_heading(text)
        return bool(
            self._chapter_toc_heading_pattern.fullmatch(normalized)
            or self._non_chapter_toc_heading_pattern.fullmatch(normalized)
        )

    def _is_non_chapter_toc_heading(self, text: str) -> bool:
        normalized = self._normalize_toc_heading(text)
        return bool(
            self._non_chapter_toc_heading_pattern.fullmatch(normalized)
        )

    def _normalize_toc_heading(self, text: str) -> str:
        return re.sub(r"[\s:：\-－–—−_]+", "", text).lower()

    def _is_page_number_only(self, text: str) -> bool:
        return bool(re.fullmatch(self._toc_page_ref_pattern, text.strip()))

    def _is_dangling_dash(self, text: str) -> bool:
        return bool(self._dangling_dash_pattern.fullmatch(text.strip()))

    def _may_have_split_trailing_dash(self, text: str) -> bool:
        return bool(self._may_have_split_trailing_dash_pattern.search(text.strip()))

    def _is_item_end(self, text: str) -> bool:
        if self._item_end_pattern.match(text):
            return True
        if self._loose_item_end_pattern.match(text):
            return True
        return bool(self._compact_item_end_pattern.match(text))

    def _looks_like_chapter_toc(self, items: Sequence[XText]) -> bool:
        chapter_items = [
            item for item in items if self._parse_toc_item(item) is not None
        ]
        ref_items = [item for item in chapter_items if self._has_page_ref(item.text)]
        return len(chapter_items) >= 3 and len(ref_items) >= 2

    def _looks_like_any_toc(self, items: Sequence[str]) -> bool:
        ref_items = [item for item in items if self._has_page_ref(item)]
        return len(items) >= 3 and len(ref_items) >= 2

    def _looks_like_toc_continuation(self, items: Sequence[XText]) -> bool:
        chapter_items = [
            item for item in items if self._parse_toc_item(item) is not None
        ]
        if not chapter_items:
            return False
        ref_items = [item for item in chapter_items if self._has_page_ref(item.text)]
        return len(ref_items) == len(chapter_items)

    def _looks_like_any_toc_continuation(self, items: Sequence[str]) -> bool:
        if not items:
            return False
        ref_items = [item for item in items if self._has_page_ref(item)]
        return len(ref_items) == len(items)

    def _has_page_ref(self, text: str) -> bool:
        text = self.normalize_space(text)
        return bool(
            self._leader_page_pattern.search(text)
            or self._page_ref_pattern.search(text)
        )

    def _parse_toc_items(self, xtexts: Sequence[XText]) -> list[XTOCItem]:
        items: list[XTOCItem] = []
        for xtext in xtexts:
            item = self._parse_toc_item(xtext)
            if item is not None:
                items.append(item)
        return self._adjust_unnumbered_section_levels(items)

    def _adjust_unnumbered_section_levels(
        self, items: Sequence[XTOCItem]
    ) -> list[XTOCItem]:
        adjusted: list[XTOCItem] = []
        unnumbered_parent: XTOCItem | None = None

        for item in items:
            if self._is_unnumbered_top_item(item):
                item.level = 1
                unnumbered_parent = item
            elif unnumbered_parent is not None and self._is_numbered_toc_item(item):
                item.level += unnumbered_parent.level
            adjusted.append(item)

        return adjusted

    def _is_unnumbered_top_item(self, item: XTOCItem) -> bool:
        return item.level == 1 and not item.no

    def _is_numbered_toc_item(self, item: XTOCItem) -> bool:
        return bool(item.no)

    def _parse_toc_item(self, xtext: XText) -> XTOCItem | None:
        text = self.normalize_space(xtext.text)
        if not text or self._is_figure_table_item(text):
            return None

        page = self._parse_page(text)
        title = self._strip_page_ref(text)
        title = re.sub(r"[.．…·•⋯]{2,}\s*$", "", title).strip()
        title = re.sub(r"[-－–—−_]{2,}\s*$", "", title).strip()
        match = self._toc_no_pattern.match(title)
        if match is None:
            if not title or not self._has_page_ref(text) or self._is_any_toc_heading(title):
                return None
            return XTOCItem(title=title, no="", level=1, page=page, xtext=xtext)

        no = match.group("no")
        level = self._level(no)
        return XTOCItem(title=title, no=no, level=level, page=page, xtext=xtext)

    def _is_figure_table_item(self, text: str) -> bool:
        return bool(self._figure_table_pattern.match(text))

    def _parse_page(self, text: str) -> int | None:
        match = self._leader_page_pattern.search(text)
        if match is None:
            match = self._page_ref_pattern.search(text)
        if match is None:
            return None
        page_text = match.group("page")
        if page_text.isdigit():
            return int(page_text)
        return None

    def _strip_page_ref(self, text: str) -> str:
        text = self._leader_page_pattern.sub("", text).strip()
        text = self._page_ref_pattern.sub("", text).strip()
        return re.sub(r"[-－–—−]\s*$", "", text).strip()

    def _level(self, no: str) -> int:
        if re.fullmatch(r"\d+(?:\.\d+)*", no):
            return no.count(".") + 1
        if re.fullmatch(r"第.+[章节篇部分]", no):
            return 1
        if re.fullmatch(r"[（(].+[）)]", no):
            return 2
        return 1
