import re
from statistics import median
from typing import Final, Sequence, TypeGuard

from memect.base.bbox import BBox

from ..base import KDocument, KLine, KObject, KPage, KPageFootnote, KText


class PageFootnoteParser:
    _bottom_ratio: Final = 0.28
    _marked_expand_x: Final = 8
    _marked_expand_y: Final = 6
    _line_expand_y: Final = 4
    _line_text_gap: Final = 45
    _region_hit_ratio: Final = 0.45
    _small_font_ratio: Final = 0.88
    _footnote_start_pattern: Final = re.compile(
        r"^\s*(?:(?:\d{1,3}(?!\.\d)[\s、.．）)]*)|[\[\(（【]\d{1,3}[\]\)）】]|[①②③④⑤⑥⑦⑧⑨⑩]|[*＊†‡])"
    )
    _section_title_pattern: Final = re.compile(r"^\s*\d+(?:\.\d+)+\.?\s+")
    _source_pattern: Final = re.compile(
        r"^\s*(?:(?:资料|数据|信息|图表|图片|表格|本文|报告|素材|案例|参考)?来源|"
        r"来源(?:资料|数据|信息)?|"
        r"source|data\s+source)\s*[:：]",
        flags=re.IGNORECASE,
    )

    def __init__(self):
        super().__init__()

    def parse(self, doc: KDocument) -> None:
        # Word 6.x/95/97 默认脚注是跟随分栏的，如：
        # -----------|-----------
        # -----------|-footnote-     => 如果没有空间，到下一页的分栏后
        # -----跨页-----------
        # -----------|---------
        # --footnote-|

        # 之后的脚注（分栏）
        # ------------|-----------
        # -footnote---|-footnote--   => 跨栏
        # --------跨页-------------

        # 不分栏
        # ------------------
        # ---footnote------- 如果没有足够的空间，到下一页
        # ----跨页-----------
        # ------------------
        # ---footnote-------
        prev_has_footnote = False
        for page in doc.working_pages:
            self._parse_page(page, prev_has_footnote=prev_has_footnote)
            prev_has_footnote = len(page.footnotes) > 0
        
        #TODO 然后汇总所有的脚注，建立一个全局的footnotes，然后变成这样：
        #1.xxxxx       =>在引用页面找到“1”字符，建立关联KFootnoteRef()
        #2.xxxxx
        #3.xxxxx

        #有些脚注开启了list模式，变成
        #(i)  1xxxx    =>1才是引用序号
        #(ii) 2xxx


    def _parse_page(self, page: KPage, *, prev_has_footnote: bool = False) -> None:
        page.footnotes.clear()

        marked = self._collect_marked(page.objects)
        regions: list[BBox] = []
        if marked:
            regions.extend(self._regions_from_marked(page, marked))

        line_regions = self._regions_from_lines(page, prev_has_footnote=prev_has_footnote)
        regions.extend(line_regions)

        regions = self._merge_regions(regions)
        if not regions:
            return

        footnotes = self._take_objects(page, regions, marked)
        page.footnotes.extend(footnotes)
        
    def _collect_marked(self, objects: Sequence[KObject]) -> list[KObject]:
        return [
            obj
            for obj in objects
            if obj.vobject is not None and obj.vobject.is_footnote()
        ]

    def _regions_from_marked(self, page: KPage, marked: Sequence[KObject]) -> list[BBox]:
        regions: list[BBox] = []
        if page.sections:
            for section in page.sections:
                for column in section.columns:
                    objs = [obj for obj in marked if self._center_in(obj.bbox, column)]
                    if objs:
                        regions.append(self._expand_marked_region(page, BBox.join2(objs), column=column))
        else:
            regions.append(self._expand_marked_region(page, BBox.join2(marked)))
        return regions

    def _regions_from_lines(self, page: KPage, *, prev_has_footnote: bool = False) -> list[BBox]:
        regions: list[BBox] = []
        body_font_size = self._body_font_size(page.objects)
        for line in self._find_footnote_lines(page):
            line_bbox = line.bbox
            column = self._find_column(page, line_bbox)
            region = self._region_below_line(page, line_bbox, column=column)
            text_candidates = self._near_line_texts(page.objects, line_bbox, region)
            if not text_candidates:
                continue
            if body_font_size is not None:
                sizes = [s for obj in text_candidates if (s := self._font_size(obj)) is not None]
                if sizes and median(sizes) > body_font_size * self._small_font_ratio:
                    numbered = any(self._looks_like_footnote_start(obj) for obj in text_candidates)
                    if not numbered and not prev_has_footnote:
                        continue
            text_bbox = BBox.join2(text_candidates)
            regions.append(self._expand_to_footnote_region(page, text_bbox, column=column))
        return regions

    def _find_footnote_lines(self, page: KPage) -> list[KLine]:
        bottom = self._bottom_bbox(page)
        lines: list[KLine] = []
        for line in page.pdf_lines:
            if not line.is_h():
                continue
            bbox = line.bbox
            if not bottom.contains(bbox.center):
                continue
            if bbox.width < 20:
                continue
            if bbox.width > page.bbox.width * 0.75:
                continue
            if self._is_table_line(page, bbox):
                continue
            # 脚注分隔线下方需要有内容，否则更可能是页脚装饰线。
            below = self._region_below_line(page, bbox, column=self._find_column(page, bbox))
            if self._near_line_texts(page.objects, bbox, below):
                lines.append(line)
        return lines

    def _region_below_line(self, page: KPage, line: BBox, *, column: BBox | None = None) -> BBox:
        x0 = column.x0 if column is not None else max(page.bbox.x0, line.x0 - self._marked_expand_x)
        x1 = column.x1 if column is not None else min(page.bbox.x1, max(line.x1 + self._marked_expand_x, line.x0 + page.bbox.width * 0.35))
        y1 = max(page.bbox.y0, line.y0 - self._line_expand_y)
        y0 = page.bbox.y0
        footer_bbox = page.footer.content_bbox
        if footer_bbox is not None and footer_bbox.y1 < y1:
            y0 = max(y0, footer_bbox.y1 + 1)
        if y1 <= y0:
            y0 = page.bbox.y0
        return BBox(x0, y0, x1, y1)

    def _expand_to_footnote_region(self, page: KPage, bbox: BBox, *, column: BBox | None = None) -> BBox:
        x0 = column.x0 if column is not None else max(page.bbox.x0, bbox.x0 - self._marked_expand_x)
        x1 = column.x1 if column is not None else min(page.bbox.x1, bbox.x1 + self._marked_expand_x)
        y0 = max(page.bbox.y0, bbox.y0 - self._marked_expand_y)
        y1 = min(page.bbox.y1, bbox.y1 + self._marked_expand_y)
        footer_bbox = page.footer.content_bbox
        if footer_bbox is not None and footer_bbox.y1 < y1:
            y0 = max(y0, footer_bbox.y1 + 1)
        return BBox(x0, y0, x1, y1)

    def _expand_marked_region(self, page: KPage, bbox: BBox, *, column: BBox | None = None) -> BBox:
        x0 = column.x0 if column is not None else max(page.bbox.x0, bbox.x0 - self._marked_expand_x)
        x1 = column.x1 if column is not None else min(page.bbox.x1, bbox.x1 + self._marked_expand_x)
        y0 = page.bbox.y0
        y1 = min(page.bbox.y1, bbox.y1 + self._marked_expand_y)
        footer_bbox = page.footer.content_bbox
        if footer_bbox is not None and footer_bbox.y1 < y1:
            y0 = max(y0, footer_bbox.y1 + 1)
        return BBox(x0, y0, x1, y1)

    def _bottom_bbox(self, page: KPage) -> BBox:
        return BBox(
            page.bbox.x0,
            page.bbox.y0,
            page.bbox.x1,
            page.bbox.y0 + page.bbox.height * self._bottom_ratio,
        )

    def _take_objects(
        self, page: KPage, regions: Sequence[BBox], marked: Sequence[KObject]
    ) -> list[KPageFootnote]:
        marked_ids = {id(obj) for obj in marked}
        groups: list[list[KObject]] = [[] for _ in regions]

        i = 0
        while i < len(page.objects):
            obj = page.objects[i]
            region_index = self._match_region(obj, regions, force=id(obj) in marked_ids)
            if region_index is None:
                i += 1
                continue
            groups[region_index].append(obj)
            del page.objects[i]

        footnotes: list[KPageFootnote] = []
        for region, objects in zip(regions, groups):
            if not objects:
                continue
            bbox = BBox.join2(objects)
            footnote = KPageFootnote(page, bbox.to_quad())
            footnote.objects.extend(sorted(objects, key=lambda obj: (-obj.bbox.y1, obj.bbox.x0)))
            footnotes.append(footnote)
        return footnotes

    def _match_region(
        self, obj: KObject, regions: Sequence[BBox], *, force: bool = False
    ) -> int | None:
        if self._is_ignored(obj):
            return None
        best_index: int | None = None
        best_ratio = 0.0
        for i, region in enumerate(regions):
            ratio = self._intersect_ratio(region, obj.bbox)
            if ratio > best_ratio:
                best_ratio = ratio
                best_index = i
            if region.contains(obj.bbox.center):
                best_ratio = max(best_ratio, self._region_hit_ratio)
                best_index = i
        if force and best_index is not None:
            return best_index
        if best_ratio >= self._region_hit_ratio:
            return best_index
        return None

    def _merge_regions(self, regions: Sequence[BBox]) -> list[BBox]:
        merged: list[BBox] = []
        for region in regions:
            if region.area2 <= 0:
                continue
            for i, existing in enumerate(merged):
                if existing.intersect(region) is not None or abs(existing.y0 - region.y0) <= 3 and abs(existing.y1 - region.y1) <= 3:
                    merged[i] = BBox.join([existing, region])
                    break
            else:
                merged.append(region)
        return merged

    def _objects_in_region(
        self, objects: Sequence[KObject], region: BBox, *, min_ratio: float
    ) -> list[KObject]:
        return [
            obj
            for obj in objects
            if not self._is_ignored(obj)
            and (
                self._intersect_ratio(region, obj.bbox) >= min_ratio
                or region.contains(obj.bbox.center)
            )
        ]

    def _near_line_texts(
        self, objects: Sequence[KObject], line: BBox, region: BBox
    ) -> list[KText]:
        texts: list[KText] = []
        for obj in self._objects_in_region(objects, region, min_ratio=0.2):
            if not self._is_text(obj):
                continue
            gap = line.y0 - obj.bbox.y1
            if 0 <= gap <= self._line_text_gap:
                texts.append(obj)
        return texts

    def _find_column(self, page: KPage, bbox: BBox) -> BBox | None:
        for section in page.sections:
            for column in section.columns:
                if column.contains(bbox.center):
                    return column
        return None

    def _body_font_size(self, objects: Sequence[KObject]) -> float | None:
        sizes = [
            size
            for obj in objects
            if self._is_text(obj)
            and (obj.vobject is None or not obj.vobject.is_footnote())
            and (size := self._font_size(obj)) is not None
        ]
        if not sizes:
            return None
        return float(median(sizes))

    def _font_size(self, obj: KObject) -> float | None:
        if isinstance(obj, KText):
            sizes = [char.bbox.height for char in obj.chars]
            if sizes:
                return float(median(sizes))
        return None

    def _text(self, obj: KObject) -> str:
        if isinstance(obj,KText):
            return obj.text
        return ''

    def _looks_like_footnote_start(self, obj: KObject) -> bool:
        text = self._text(obj)
        if self._section_title_pattern.match(text):
            return False
        return bool(self._footnote_start_pattern.match(text))

    def _is_text(self, obj: KObject) -> TypeGuard[KText]:
        return isinstance(obj,KText)

    def _is_ignored(self, obj: KObject) -> bool:
        if obj.vobject is not None and (obj.vobject.is_header() or obj.vobject.is_footer()):
            return True
        text = self._text(obj).strip()
        if self._section_title_pattern.match(text):
            return True
        if re.match(r"^(?:https?://|www\.)", text, flags=re.IGNORECASE):
            return True
        if self._source_pattern.match(text):
            return True
        return obj.type in {"table", "figure", "formula", "pageheader", "pagefooter"}

    def _bottom_text_cluster(self, objects: Sequence[KText]) -> list[KText]:
        sorted_objects = sorted(objects, key=lambda obj: obj.bbox.y0)
        if not sorted_objects:
            return []
        cluster = [sorted_objects[0]]
        last = sorted_objects[0]
        for obj in sorted_objects[1:]:
            if obj.bbox.y0 - last.bbox.y1 > self._line_text_gap:
                break
            cluster.append(obj)
            last = obj
        return cluster

    def _is_table_line(self, page: KPage, line: BBox) -> bool:
        for obj in page.objects:
            if obj.type != "table":
                continue
            table = obj.bbox
            table_area = BBox(table.x0 - 2, table.y0 - 3, table.x1 + 2, table.y1 + 3)
            if not table_area.contains(line.center):
                continue
            overlap = max(0.0, min(line.x1, table.x1) - max(line.x0, table.x0))
            if line.width <= 0 or overlap / line.width >= 0.5:
                return True
        return False

    def _intersect_ratio(self, region: BBox, bbox: BBox) -> float:
        inter = region.intersect(bbox)
        if inter is None:
            return 0.0
        return inter.area2 / bbox.area2

    def _center_in(self, bbox: BBox, region: BBox) -> bool:
        return region.contains(bbox.center)
