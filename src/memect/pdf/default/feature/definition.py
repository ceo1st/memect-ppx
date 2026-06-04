from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Iterable, Sequence

from memect.base.bbox import BBox
from memect.pdf.base import KCell, KChar, KDocument, KObject, KPage, KTable, KText


class DefinitionAnchor(StrEnum):
    COLON = "colon"
    ZHI = "zhi"


@dataclass
class _Line:
    page: KPage
    bbox: BBox
    text: str
    chars: Sequence[KChar]


@dataclass
class _Anchor:
    line: _Line
    char: KChar
    mode: DefinitionAnchor


@dataclass
class _Layout:
    mode: DefinitionAnchor
    anchor_x: float
    anchor_width: float


@dataclass
class _Entry:
    page: KPage
    term: list[str] = field(default_factory=list)
    anchor: str = ""
    definition: list[str] = field(default_factory=list)
    term_bboxes: list[BBox] = field(default_factory=list)
    anchor_bboxes: list[BBox] = field(default_factory=list)
    definition_bboxes: list[BBox] = field(default_factory=list)

    @property
    def bboxes(self) -> list[BBox]:
        return [*self.term_bboxes, *self.anchor_bboxes, *self.definition_bboxes]

    @property
    def text(self) -> tuple[str, str, str]:
        return (
            "".join(self.term).strip(),
            self.anchor.strip(),
            "".join(self.definition).strip(),
        )


class DefinitionTableParser:
    """解析“释义”页里的无边框三列表格。

    支持两类常见布局：
    1. 术语 | 指 | 释义
    2. 术语 | ： | 释义

    连续页面会归为同一组，但由于 KTable 绑定单页，最终仍按页生成 KTable。
    """

    _COLON_CHARS: Final = {":", "："}
    _ZHI_CHARS: Final = {"指"}

    def parse(self, doc: KDocument, *, append: bool = True) -> list[KTable]:
        tables: list[KTable] = []
        group_index = 0
        current_group_pages = 0
        group_layout: _Layout | None = None

        for page in doc.working_pages:
            lines = self._get_lines(page)
            layout = self._detect_layout(lines)
            if layout is None:
                layout = group_layout if current_group_pages > 0 else None
                if layout is None:
                    current_group_pages = 0
                    group_layout = None
                    continue

            entries = self._parse_entries(lines, layout)
            min_entries = 1 if current_group_pages > 0 else 2
            if len(entries) < min_entries:
                current_group_pages = 0
                group_layout = None
                continue

            if current_group_pages == 0:
                group_index += 1
            current_group_pages += 1
            group_layout = layout

            table = self._make_table(page, entries, layout)
            table.subtype = "definition"
            table.cache["definition"] = {
                "group_index": group_index,
                "group_page_index": current_group_pages - 1,
                "mode": layout.mode.value,
                "anchor_x": layout.anchor_x,
            }
            tables.append(table)
            if append:
                page.objects.append(table)

        return tables

    def _get_lines(self, page: KPage) -> list[_Line]:
        lines: list[_Line] = []
        for obj in self._iter_objects(page.objects):
            if isinstance(obj, KText):
                if obj.lines:
                    for line in obj.lines:
                        text = line.text.strip()
                        if text:
                            lines.append(_Line(page, line.bbox, text, line.chars))
                else:
                    text = obj.text.strip()
                    if text:
                        lines.append(_Line(page, obj.bbox, text, tuple()))

        lines.sort(key=lambda line: (-line.bbox.y1, line.bbox.x0))
        return lines

    def _iter_objects(self, objects: Iterable[KObject]) -> Iterable[KObject]:
        for obj in objects:
            yield obj
            children = getattr(obj, "objects", None)
            if children and not isinstance(obj, KText):
                yield from self._iter_objects(children)

    def _detect_layout(self, lines: Sequence[_Line]) -> _Layout | None:
        anchors = self._collect_anchors(lines)
        colon = [a for a in anchors if a.mode == DefinitionAnchor.COLON]
        zhi = [a for a in anchors if a.mode == DefinitionAnchor.ZHI]

        layout = self._layout_from_anchors(colon, DefinitionAnchor.COLON)
        if layout is not None:
            return layout
        return self._layout_from_anchors(zhi, DefinitionAnchor.ZHI)

    def _collect_anchors(self, lines: Sequence[_Line]) -> list[_Anchor]:
        anchors: list[_Anchor] = []
        for line in lines:
            if self._is_ignored_line(line.text):
                continue
            for char in line.chars:
                text = char.text.strip()
                if text in self._COLON_CHARS:
                    anchors.append(_Anchor(line, char, DefinitionAnchor.COLON))
                elif text in self._ZHI_CHARS:
                    anchors.append(_Anchor(line, char, DefinitionAnchor.ZHI))
        return anchors

    def _layout_from_anchors(
        self, anchors: Sequence[_Anchor], mode: DefinitionAnchor
    ) -> _Layout | None:
        if len(anchors) < 3:
            return None

        clusters: list[list[_Anchor]] = []
        for anchor in sorted(anchors, key=lambda item: item.char.bbox.cx):
            if clusters and abs(anchor.char.bbox.cx - self._cluster_x(clusters[-1])) <= 8:
                clusters[-1].append(anchor)
            else:
                clusters.append([anchor])

        cluster = max(clusters, key=len)
        if len(cluster) < 3:
            return None
        anchor_x = self._cluster_x(cluster)
        widths = sorted(anchor.char.bbox.width for anchor in cluster)
        return _Layout(mode, anchor_x, widths[len(widths) // 2])

    def _cluster_x(self, anchors: Sequence[_Anchor]) -> float:
        return sum(anchor.char.bbox.cx for anchor in anchors) / len(anchors)

    def _parse_entries(self, lines: Sequence[_Line], layout: _Layout) -> list[_Entry]:
        entries: list[_Entry] = []
        current: _Entry | None = None
        pending_term: list[_Line] = []
        body_started = False

        for line in lines:
            if self._is_ignored_line(line.text):
                continue

            anchor = self._line_anchor(line, layout)
            if anchor is not None:
                left_text, left_bbox = self._line_text_before(line, anchor.char)
                right_text, right_bbox = self._line_text_after(line, anchor.char)
                term_texts = [l.text for l in pending_term]
                term_bboxes = [l.bbox for l in pending_term]
                pending_term.clear()

                if left_text:
                    term_texts.append(left_text)
                    if left_bbox is not None:
                        term_bboxes.append(left_bbox)

                if not term_texts:
                    continue

                current = _Entry(
                    line.page,
                    term=term_texts,
                    anchor=anchor.char.text,
                    definition=[right_text] if right_text else [],
                    term_bboxes=term_bboxes,
                    anchor_bboxes=[anchor.char.bbox],
                    definition_bboxes=[right_bbox] if right_bbox is not None else [],
                )
                entries.append(current)
                body_started = True
                continue

            if not body_started:
                continue

            if self._is_left_column(line, layout):
                pending_term.append(line)
            elif current is not None and self._is_definition_column(line, layout):
                current.definition.append(line.text)
                current.definition_bboxes.append(line.bbox)

        return [entry for entry in entries if all(entry.text)]

    def _line_anchor(self, line: _Line, layout: _Layout) -> _Anchor | None:
        best: tuple[float, KChar] | None = None
        chars = (
            self._COLON_CHARS
            if layout.mode == DefinitionAnchor.COLON
            else self._ZHI_CHARS
        )
        for char in line.chars:
            if char.text.strip() not in chars:
                continue
            distance = abs(char.bbox.cx - layout.anchor_x)
            if distance > max(8, layout.anchor_width * 3):
                continue
            if best is None or distance < best[0]:
                best = (distance, char)
        if best is None:
            return None
        return _Anchor(line, best[1], layout.mode)

    def _line_text_before(self, line: _Line, char: KChar) -> tuple[str, BBox | None]:
        chars = [c for c in line.chars if c.bbox.cx < char.bbox.x0]
        return self._chars_text_bbox(chars)

    def _line_text_after(self, line: _Line, char: KChar) -> tuple[str, BBox | None]:
        chars = [c for c in line.chars if c.bbox.cx > char.bbox.x1]
        return self._chars_text_bbox(chars)

    def _chars_text_bbox(self, chars: Sequence[KChar]) -> tuple[str, BBox | None]:
        text = "".join(char.text for char in chars).strip()
        if not text:
            return "", None
        return text, BBox.join2(chars)

    def _is_left_column(self, line: _Line, layout: _Layout) -> bool:
        return line.bbox.x1 < layout.anchor_x - max(6, layout.anchor_width * 2)

    def _is_definition_column(self, line: _Line, layout: _Layout) -> bool:
        return line.bbox.x0 > layout.anchor_x + max(3, layout.anchor_width)

    def _is_ignored_line(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return True
        if text in {"释义", "第一章释义", "第一章 释义"}:
            return True
        if len(text) <= 4 and text.isdigit():
            return True
        return False

    def _make_table(
        self, page: KPage, entries: Sequence[_Entry], layout: _Layout
    ) -> KTable:
        table_bbox = BBox.join([bbox for entry in entries for bbox in entry.bboxes])
        x0 = table_bbox.x0
        x1 = table_bbox.x1
        anchor_half = max(layout.anchor_width, 4)
        col0_x1 = layout.anchor_x - anchor_half
        col1_x0 = layout.anchor_x - anchor_half
        col1_x1 = layout.anchor_x + anchor_half
        col2_x0 = layout.anchor_x + anchor_half

        cells: list[KCell] = []
        for row_index, entry in enumerate(entries):
            row_bbox = BBox.join(entry.bboxes)
            term_text, anchor_text, definition_text = entry.text
            cols = [
                (BBox(x0, row_bbox.y0, col0_x1, row_bbox.y1), term_text),
                (BBox(col1_x0, row_bbox.y0, col1_x1, row_bbox.y1), anchor_text),
                (BBox(col2_x0, row_bbox.y0, x1, row_bbox.y1), definition_text),
            ]
            for col_index, (bbox, text) in enumerate(cols):
                cells.append(
                    KCell(
                        page,
                        bbox,
                        row_index=row_index,
                        col_index=col_index,
                        objects=[KText(page, bbox, text=text)],
                    )
                )

        return KTable(page, table_bbox, cells=cells)
