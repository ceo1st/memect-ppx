import logging
import re
import unicodedata
from typing import Sequence

from memect.pdf.x.xbase import XNode, XObject, XText, XTree
from memect.pdf.x.xtoc import XTOCItem, XTOCParser


class Parser:
    """根据文档中的目录构建章节树"""

    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self):
        super().__init__()
        self._toc = XTOCParser()

    def parse(self, xtree: XTree):
        tocs = self._toc.find(xtree.xobjects)
        if not tocs:
            return

        toc_start = tocs[0][0]
        toc_end = max(toc[1] for toc in tocs)
        items: list[XTOCItem] = []
        for _, _, toc_xobjects in tocs:
            items.extend(self._toc.parse(toc_xobjects))
        if not items:
            return

        roots = self._toc.build_tree(items)
        roots = self._fill_titles(roots, xtree.xobjects, toc_end)
        if not roots:
            self._logger.warning("无法根据目录匹配正文标题")
            return

        self._rebuild_tree(xtree, roots, toc_start, toc_end)

    def _fill_titles(
        self,
        items: Sequence[XTOCItem],
        xobjects: Sequence[XObject],
        start_index: int,
    ) -> list[XTOCItem]:
        items, _ = self._fill_titles_from(items, xobjects, start_index)
        return items

    def _fill_titles_from(
        self,
        items: Sequence[XTOCItem],
        xobjects: Sequence[XObject],
        start_index: int,
    ) -> tuple[list[XTOCItem], int]:
        result: list[XTOCItem] = []
        index = start_index
        for item in items:
            title = self._find_title(item, xobjects, index)
            if title is None:
                self._logger.warning(
                    "无法找到目录标题，放弃节点及子节点: %s", item.title
                )
                continue
            title.as_title()
            item.title_xobj = title
            index = xobjects.index(title) + 1
            item.children, index = self._fill_titles_from(
                item.children, xobjects, index
            )
            result.append(item)
        return result, index

    def _find_title(
        self, item: XTOCItem, xobjects: Sequence[XObject], start_index: int
    ) -> XText | None:
        best: XText | None = None
        best_score = 0
        target = self._normalize_title(item.title)
        target_without_no = self._normalize_title(self._toc.strip_no(item.title))

        for i in range(start_index, len(xobjects)):
            xobj = xobjects[i]
            if not isinstance(xobj, XText):
                continue

            score = self._match_score(xobj.text, target, target_without_no)
            if item.page is not None and xobj.page_numbers and score > 0:
                first_page = xobj.page_numbers[0]
                if first_page == item.page:
                    score += 5
                elif abs(first_page - item.page) <= 3:
                    score += 2
            if score > best_score:
                best_score = score
                best = xobj
                if score >= 100:
                    break

        return best if best_score >= 70 else None

    def _match_score(self, text: str, target: str, target_without_no: str) -> int:
        source = self._normalize_title(text)
        source_without_no = self._normalize_title(self._toc.strip_no(text))
        if source == target:
            return 100
        if source_without_no == target_without_no and target_without_no:
            return 95
        score = self._first_char_tolerant_score(source, target)
        if score > 0:
            return score
        if target_without_no:
            score = self._first_char_tolerant_score(
                source_without_no, target_without_no
            )
            if score > 0:
                return min(score, 90)
        if source.startswith(target) or target.startswith(source):
            return 85
        if (
            target_without_no
            and (
                source_without_no.startswith(target_without_no)
                or target_without_no.startswith(source_without_no)
            )
        ):
            return 80
        return 0

    def _first_char_tolerant_score(self, source: str, target: str) -> int:
        if not source or not target or source == target:
            return 0

        if len(source) > 1 and source[1:] == target:
            return (
                92
                if self._can_ignore_leading_title_char(source[0], len(target))
                else 0
            )
        if len(target) > 1 and target[1:] == source:
            return (
                92
                if self._can_ignore_leading_title_char(target[0], len(source))
                else 0
            )
        if len(source) == len(target) and len(source) > 1:
            tail = source[1:]
            if tail == target[1:] and (
                len(tail) >= 4
                or self._is_unreliable_title_char(source[0])
                or self._is_unreliable_title_char(target[0])
            ):
                return 90
        return 0

    def _can_ignore_leading_title_char(self, ch: str, tail_len: int) -> bool:
        return tail_len >= 4 or self._is_unreliable_title_char(ch)

    def _is_unreliable_title_char(self, ch: str) -> bool:
        if "\ue000" <= ch <= "\uf8ff":
            return True
        category = unicodedata.category(ch)
        return category[0] in {"P", "S", "C"}

    def _rebuild_tree(
        self,
        xtree: XTree,
        toc_roots: Sequence[XTOCItem],
        toc_start: int,
        toc_end: int,
    ):
        used_titles = {
            item.title_xobj for item in self._flatten(toc_roots) if item.title_xobj
        }
        for obj in xtree.xobjects:
            obj.node.deatch()
        xtree.root.add(*xtree.xobjects[:toc_end])

        for item in toc_roots:
            self._add_title_node(xtree.root, item)

        self._fill_others(xtree, xtree.root, toc_end=toc_end, used_titles=used_titles)

    def _add_title_node(self, parent: XNode, item: XTOCItem):
        if item.title_xobj is None:
            return
        title = item.title_xobj
        parent.add(title)
        for child in item.children:
            self._add_title_node(title.node, child)

    def _fill_others(
        self,
        xtree: XTree,
        node: XNode,
        *,
        toc_end: int,
        used_titles: set[XObject],
    ):
        xobjects = xtree.xobjects

        if node.is_root():
            start = 0
        elif node.object in xobjects:
            node_index = xobjects.index(node.object)
            if node_index < toc_end:
                return
            start = node_index + 1
        else:
            start = -1

        for child in node.children:
            self._fill_others(xtree, child, toc_end=toc_end, used_titles=used_titles)

        if start < 0:
            return

        end = self._find_content_end_index(node, xobjects, start)
        objs = [
            obj
            for obj in xobjects[start:end]
            if obj not in used_titles and obj.node.parent is None
        ]
        if objs:
            node.add(*objs, index=0)

    def _find_content_end_index(
        self, node: XNode, xobjects: Sequence[XObject], start: int
    ) -> int:
        end = self._first_xobject_index(node.children, xobjects, start)
        if end is not None:
            return end

        current_node: XNode | None = node
        while current_node is not None and not current_node.is_root():
            next_node = current_node.next()
            if next_node is not None:
                end = self._first_xobject_index([next_node], xobjects, start)
                if end is not None:
                    return end
            current_node = current_node.parent

        return len(xobjects)

    def _first_xobject_index(
        self, nodes: Sequence[XNode | None], xobjects: Sequence[XObject], start: int
    ) -> int | None:
        for node in nodes:
            if node is None:
                continue
            index = self._xobject_index(node.object, xobjects)
            if index is not None and index >= start:
                return index
            index = self._first_xobject_index(node.children, xobjects, start)
            if index is not None:
                return index
        return None

    def _xobject_index(
        self, xobject: XObject, xobjects: Sequence[XObject]
    ) -> int | None:
        try:
            return xobjects.index(xobject)
        except ValueError:
            return None

    def _flatten(self, items: Sequence[XTOCItem]) -> list[XTOCItem]:
        result: list[XTOCItem] = []
        for item in items:
            result.append(item)
            result.extend(self._flatten(item.children))
        return result

    def _normalize_title(self, text: str) -> str:
        text = self._toc.strip_page_ref(self._toc.normalize_space(text))
        text = re.sub(r"[.．…·•⋯\-－–—−_\s:：、,，。；;()（）《》<>]", "", text)
        return text.lower()
