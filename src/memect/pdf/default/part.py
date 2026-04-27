import re
from dataclasses import dataclass, field

from memect.base.bbox import BBox
from memect.pdf.base import KFigure, KObject, KPage, KTable, KTextbox

_KContent = KFigure | KTable


@dataclass
class FigureBlock:
    title: KTextbox | None = None
    figures: list[_KContent] = field(default_factory=list[_KContent])
    source: KTextbox | None = None

    @property
    def objects(self) -> list[KObject]:
        result: list[KObject] = []
        if self.title:
            result.append(self.title)
        result.extend(self.figures)
        if self.source:
            result.append(self.source)
        return result

    @property
    def bbox(self) -> BBox:
        return BBox.join([o.bbox for o in self.objects])


class FigureParser:
    _title_re = re.compile(
        r'^(图表|图|插图|示意图|流程图|表|Figure|Fig\.?|Table|Tab\.?)\s*[\d一二三四五六七八九十]',
        re.IGNORECASE,
    )
    _source_re = re.compile(
        r'^((?:资料来源|数据来源|信息来源|来源|出处|引自|备注|注释|Data\s*source|Source|Notes?)\s*[：:]?'
        r'|(?:注)\s*[：:])',
        re.IGNORECASE,
    )

    def __init__(self):
        super().__init__()

    def parse(self, page: KPage) -> list[FigureBlock]:
        objs = list(page.objects)
        figures = [o for o in objs if isinstance(o, (KFigure, KTable))]
        if not figures:
            return []

        # 以每个figure为锚点，寻找关联的title/source
        blocks: list[FigureBlock] = []
        used: set[int] = set()
        for fig in figures:
            title = self._find_title(fig, objs, used)
            source = self._find_source(fig, objs, used)
            block = FigureBlock(title=title, figures=[fig], source=source)
            used.update(id(o) for o in block.objects)
            blocks.append(block)

        # 校验：block的bbox不能与非block对象相交或包含
        block_obj_ids = {id(o) for b in blocks for o in b.objects}
        others = [o for o in objs if id(o) not in block_obj_ids]
        validated: list[FigureBlock] = []
        for block in blocks:
            bbox = block.bbox
            if any(bbox.intersect(o.bbox) is not None for o in others):
                # 尝试剥离title/source后再验证
                block = self._shrink(block, others)
            if block.figures:
                validated.append(block)
        return validated

    def _find_title(self, fig: _KContent, objs: list[KObject], used: set[int]) -> KTextbox | None:
        """在figure上方找title候选：y0>=fig.y1，x方向有重叠，且最近。"""
        candidates: list[tuple[float, KTextbox]] = []
        for o in objs:
            if id(o) in used or not isinstance(o, KTextbox):
                continue
            if not self._is_title(o):
                continue
            if o.bbox.y0 < fig.bbox.y1:
                continue
            if not self._x_overlap(o.bbox, fig.bbox):
                continue
            dist = o.bbox.y0 - fig.bbox.y1
            candidates.append((dist, o))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    def _find_source(self, fig: _KContent, objs: list[KObject], used: set[int]) -> KTextbox | None:
        """在figure下方找source候选：y1<=fig.y0，x方向有重叠，且最近。"""
        candidates: list[tuple[float, KTextbox]] = []
        for o in objs:
            if id(o) in used or not isinstance(o, KTextbox):
                continue
            if not self._is_source(o):
                continue
            if o.bbox.y1 > fig.bbox.y0:
                continue
            if not self._x_overlap(o.bbox, fig.bbox):
                continue
            dist = fig.bbox.y0 - o.bbox.y1
            candidates.append((dist, o))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    def _shrink(self, block: FigureBlock, others: list[KObject]) -> FigureBlock:
        """如果block与其他对象相交，依次剥离title/source。"""
        for drop_title, drop_source in [(False, True), (True, False), (True, True)]:
            b = FigureBlock(
                title=None if drop_title else block.title,
                figures=list(block.figures),
                source=None if drop_source else block.source,
            )
            if not b.objects:
                continue
            bbox = b.bbox
            if not any(bbox.intersect(o.bbox) is not None for o in others):
                return b
        return FigureBlock(figures=list(block.figures))

    def _is_title(self, obj: KTextbox) -> bool:
        return bool(self._title_re.match(obj.text.strip()))

    def _is_source(self, obj: KTextbox) -> bool:
        return bool(self._source_re.match(obj.text.strip()))

    def _x_overlap(self, a: BBox, b: BBox) -> bool:
        return a.x1 > b.x0 and b.x1 > a.x0
