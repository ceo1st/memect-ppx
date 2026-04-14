from enum import StrEnum, auto
from typing import Any, Final, Literal, Mapping, Sequence


from memect.base.bbox import BBox
from memect.base.utils import MyBaseModel
from memect.pdf.base import (
    KColor,
    KDocument,
    KLine,
    KPage,
)


class PdfProvider(StrEnum):
    PDF_OXIDE = auto()
    PYMUPDF = auto()


class PdfParserArgs(MyBaseModel):
    # or pymupdf or pdfminer??
    provider: PdfProvider = PdfProvider.PYMUPDF
    params: Mapping[str, Any] | None = None


class PdfParser:
    def __init__(self, args: PdfParserArgs | Mapping[str, Any] | None = None):
        super().__init__()
        self._args = PdfParserArgs.create(args)
        self._provider: Final = self._args.provider

    def parse(self, doc: KDocument):
        # 使用pdf的解析，工作量在于是否需要和视觉保存一致，现在使用第三方的库，就需要考虑下面几个问题是否处理了
        # 1. 支持了clip path吗？在clip path外的内容不可见
        # 2. 一个cid对应多个字符如何处理，可能是连体字，也可能是返回形似但是codepoint不同的，需要归一化
        # 3. cid没有对应的unicode如何处理？

        # 上面这些情况可能仅仅在个别古老的pdf中（都是因为pdf制作工具bug太多导致），随着时间的推移，这些问题都不需要解决了
        # 如：在项目使用中，某个pdf的某个表格多提取了一个字符，这个字符被clip，或者不可见，就不应该存在。
        # doc.pdf_provider = self._provider
        if self._provider == PdfProvider.PYMUPDF:
            from .pdf_pymupdf import Parser
            Parser().parse(doc)
        elif self._provider == PdfProvider.PDF_OXIDE:
            from .pdf_pdfoxide import Parser
            Parser().parse(doc)
        else:
            raise ValueError(f"不支持的provider:{self._provider}")


type _Rect = tuple[float, float, float, float]


class LineParser:
    """pdf的线可能是由很多个小矩形/小点等组成，这里合并为水平线和垂直线"""

    def __init__(
        self,
        *,
        max_width: float = 2,
        max_height: float = 2,
        h_threshold: float = 1,
        v_threshold: float = 1,
        min_length: float = 5,
        gap_threshold: float = 3,
    ):
        super().__init__()
        self._max_height = max_height  # 水平线的最大宽度
        self._max_width = max_width  # 垂直线的最大宽度
        self._h_threshold = h_threshold  # 水平线y坐标容差
        self._v_threshold = v_threshold  # 垂直线x坐标容差
        self._min_length = min_length  # 最小线长度
        self._gap_threshold = gap_threshold  # 线段间隙阈值，超过此值则分开

    def parse(self, page: KPage, bbox: BBox) -> tuple[list[KLine], list[KLine]]:
        # 线可能为很多个点，或者很多小段的线组成
        # 而且还包含文字的下划线，删除线
        # 第一步是先解析表格线
        # 然后去掉下划线/删除线？

        # 使用来自pymupdf的对象，还需要转换，为了简化实现，颜色后续再补充

        # https://pymupdf.readthedocs.io/en/latest/textpage.html#TextPage.extractRAWDICT
        # {'type':3,'color':0xffffff,'bbox':[]}
        # 现在采用按需解析，所以，一开始不会解析线，还是保留原始的数据结构

        paths = bbox.transform(page.to_lt()).get(page.pdf_paths, ratio=0.6)
        if not paths:
            return ([], [])
        # TODO 后续去掉这个函数
        from pymupdf import sRGB_to_rgb

        def get_color(bbox: Any):
            # 如果需要计算复杂，获得所有的颜色取平均值？这个意义又不大
            for p in paths:
                if p["bbox"] is bbox:
                    color = p["color"]
                    if isinstance(color, int):
                        rgb = sRGB_to_rgb(color)
                    else:
                        rgb = color
                    return KColor.from_list(rgb, is_float=False, colors=page.doc.colors)
            return KColor.BLACK

        # 这里可以使用左下角或者左上角的坐标，都不影响
        h_groups, v_groups = self.parse2([p["bbox"] for p in paths])

        def make_line(
            bboxes: Sequence[Any],
            use_avg: bool = False,
            is_h: bool = True,
            color: KColor = KColor.BLACK,
        ):
            if is_h:
                x0, y0, x1, y1 = 0, 1, 2, 3
            else:
                x0, y0, x1, y1 = 1, 0, 3, 2

            bbox = BBox.join(bboxes)
            if use_avg:
                # 使用平均值的方式
                line_width = sum(b[y1] - b[y0] for b in bboxes) / len(bboxes)
                y = sum((b[y1] + b[y0]) / 2 for b in bboxes) / len(bboxes)
            else:
                # 使用合并的方式
                line_width = bbox[y1] - bbox[y0]
                y = (bbox[y1] + bbox[y0]) / 2
            # 转换为左下角为原点
            bbox = bbox.set((y0, y), (y1, y)).transform(page.to_lb())
            return KLine(page, bbox, color=color, width=line_width)

        def make_lines(groups: list[list[Any]], is_h: bool = True):
            lines: list[KLine] = []
            for group in groups:
                # 认为在同一组都是相同的颜色
                color = get_color(group[0])
                line = make_line(group, is_h=is_h, color=color)
                lines.append(line)
            return lines

        h_lines = make_lines(h_groups, is_h=True)
        v_lines = make_lines(v_groups, is_h=False)
        return (h_lines, v_lines)

    def parse2(
        self, rects: Sequence[_Rect]
    ) -> tuple[list[list[_Rect]], list[list[_Rect]]]:
        """解析，然后返回水平线和垂直线(h_lines,v_lines)，为了后续使用，这里没有对线合并，只是分组"""
        if not rects:
            return [], []

        rects_list = list(rects)
        h_lines = self._merge_lines(rects_list, "h")
        v_lines = self._merge_lines(rects_list, "v")
        return h_lines, v_lines

    def _merge_lines(
        self, rects: list[_Rect], direction: Literal["h", "v"]
    ) -> list[list[_Rect]]:
        """合并线段"""
        filtered_rects: list[_Rect] = []
        if direction == "h":
            x0, y0, x1, y1 = 0, 1, 2, 3
            max_thickness = self._max_height
            coord_threshold = self._h_threshold
        else:
            x0, y0, x1, y1 = 1, 0, 3, 2
            max_thickness = self._max_width
            coord_threshold = self._v_threshold

        for rect in rects:
            if rect[y1] - rect[y0] <= max_thickness:
                filtered_rects.append(rect)
            else:
                # print('===>filter',direction,rect,max_thickness)
                pass

        if not filtered_rects:
            return []

        def cmp(a: _Rect, b: _Rect) -> int:
            cy1 = (a[y0] + a[y1]) / 2
            cy2 = (b[y0] + b[y1]) / 2
            if abs(cy1 - cy2) <= coord_threshold:
                if a[x0] <= b[x0]:
                    return -1
                else:
                    return 1
            elif cy1 < cy2:
                return -1
            else:
                return 1

        # lists.sort(filtered_rects,cmp=cmp)
        filtered_rects.sort(key=lambda r: (r[y0] + r[y1]) / 2)

        groups: list[list[_Rect]] = []
        current_group: list[_Rect] = [filtered_rects[0]]

        for rect in filtered_rects[1:]:
            # 使用第一个避免累积误差
            # 如果遇到的都是点，如：（1*1），当处理水平线的时候，可能就线
            first_rect = current_group[0]
            cy1 = (first_rect[y0] + first_rect[y1]) / 2
            cy2 = (rect[y0] + rect[y1]) / 2

            if abs(cy1 - cy2) <= coord_threshold:
                current_group.append(rect)
            else:
                if current_group:
                    groups.append(current_group)
                current_group = [rect]

        if current_group:
            groups.append(current_group)

        # 合并每组中的线段
        lines: list[list[_Rect]] = []
        for group in groups:
            merged_lines = self._merge_group(group, direction)
            lines.extend(merged_lines)
        return lines

    def _merge_group(
        self, group: list[_Rect], direction: Literal["h", "v"]
    ) -> list[list[_Rect]]:
        """合并同一方向的线段组，处理间隙分割"""
        if not group:
            return []

        # 索引映射：统一处理水平和垂直线
        if direction == "h":
            # 水平线：x0,y0,x1,y1 = 0,1,2,3
            x0, y0, x1, y1 = 0, 1, 2, 3
        else:
            # 垂直线：交换x和y的索引，x0,y0,x1,y1 = 1,0,3,2
            x0, y0, x1, y1 = 1, 0, 3, 2

        # 按主方向坐标排序
        group.sort(key=lambda r: r[x0])

        # 分割成连续的子组
        continuous_groups: list[list[_Rect]] = []
        current_subgroup: list[_Rect] = [group[0]]

        last_position = group[0][x1]
        for rect in group[1:]:
            # 检查间隙：当前线段的起点与前一个线段的终点的距离
            gap = rect[x0] - last_position
            if gap <= self._gap_threshold:
                # 粘连或间隙小，合并
                current_subgroup.append(rect)
                last_position = max(last_position, rect[x1])
            else:
                # 间隙过大，分开
                continuous_groups.append(current_subgroup)
                current_subgroup = [rect]
                last_position = rect[x1]

        continuous_groups.append(current_subgroup)

        # TODO 如果是由点组成的线，每一个点都很小，在水平线的时候，可能存在很短的垂直线
        def is_valid(group: list[_Rect]) -> bool:
            start = min(b[x0] for b in group)
            end = max(b[x1] for b in group)
            return end - start >= self._min_length

        return [g for g in continuous_groups if is_valid(g)]
        # return continuous_groups
