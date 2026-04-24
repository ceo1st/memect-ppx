from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Protocol, Sequence

from memect.base import lists
from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.pdf.base import KDocument, KPage


class Item(Protocol):
    @property
    def bbox(self) -> BBox: ...


class ReadingOrder:
    _debugger=XDebugger(f'{__module__}.{__qualname__}')
    def __init__(self):
        super().__init__()

    def parse(self,doc:KDocument,max_workers:int=0):
        self._do(self._parse_page, doc.working_pages, max_workers=max_workers)
    
    def _parse_page(self,page:KPage):
        #TODO 如果是多页的，还需要考虑表格布局的情况，跨几页
        debugger = self._debugger.bind(page=page.number)
        raw_objects = list(page.objects)
        columns = XYCut().layout(page.objects)
        column_bboxes:list[BBox]=[]
        
        page.objects.clear()
        for column in columns:
            bbox = BBox.join2(column)
            column_bboxes.append(bbox)
            page.objects.extend(column)

        if debugger.allow('draw'):
            page.draw(
                ('page',None),
                ('columns',column_bboxes),
                (f'raw_objects={len(raw_objects)}',raw_objects,'number'),
                (f'objects={len(page.objects)}',page.objects,'number'),
                show_type=False,
                dir='debug/default/order',
                
            )



    def _do(
        self, fn: Callable[[KPage], None], pages: Sequence[KPage], max_workers: int = 0
    ):
        if max_workers == 0:
            for page in pages:
                fn(page)
        else:
            # 在free-threaded后才真正使用多核心
            with ThreadPoolExecutor(
                max_workers, thread_name_prefix=fn.__name__
            ) as executor:
                for _ in executor.map(fn, pages):
                    pass
class XYCut:
    def __init__(self):
        super().__init__()

    def layout[T: Item](self, bboxes: Sequence[T]) -> list[Sequence[T]]:
        if not bboxes:
            return []
        groups = self._cut(bboxes)
        flat: list[T] = [o for g in groups for o in g]
        return self._regroup(flat)

    def _regroup[T: Item](self, items: list[T]) -> list[Sequence[T]]:
        if not items:
            return []
        done: list[Sequence[T]] = []
        cur: list[T] = [items[0]]
        cur_bbox = items[0].bbox
        for i, o in enumerate(items[1:], 1):
            candidate = cur_bbox.union(o.bbox)
            #这里为除了当前的
            #remaining = [x.bbox for x in items[i + 1:]]
            other = list(items)
            lists.remove(other,cur,[o],use_is=True)
            remaining = [x.bbox for x in other]
            if o.bbox.y1<=cur_bbox.y0 and all(candidate.intersect(b) is None for b in remaining):
                cur.append(o)
                cur_bbox = candidate
            else:
                done.append(cur)
                cur = [o]
                cur_bbox = o.bbox
        done.append(cur)
        return done

    def _cut[T: Item](self, bboxes: Sequence[T]) -> list[Sequence[T]]:
        if len(bboxes) <= 1:
            return [bboxes]

        sorted_y = sorted(bboxes, key=lambda o: o.bbox.y1, reverse=True)
        gap = self._find_gap(sorted_y, axis='y')
        if gap is not None:
            top = [o for o in bboxes if o.bbox.y0 >= gap]
            bottom = [o for o in bboxes if o.bbox.y1 <= gap]
            mixed = [o for o in bboxes if o not in top and o not in bottom]
            top += mixed
            return self._cut(top) + self._cut(bottom)

        sorted_x = sorted(bboxes, key=lambda o: o.bbox.x0)
        gap = self._find_gap(sorted_x, axis='x')
        if gap is not None:
            left = [o for o in bboxes if o.bbox.x1 <= gap]
            right = [o for o in bboxes if o.bbox.x0 >= gap]
            mixed = [o for o in bboxes if o not in left and o not in right]
            return self._cut(mixed) + self._cut(left) + self._cut(right)

        return [bboxes]

    def _find_gap[T: Item](self, sorted_objs: Sequence[T], axis: str) -> float | None:
        if axis == 'y':
            min_start = sorted_objs[0].bbox.y0
            for o in sorted_objs[1:]:
                if o.bbox.y1 <= min_start:
                    return (o.bbox.y1 + min_start) / 2
                min_start = min(min_start, o.bbox.y0)
        else:
            max_end = sorted_objs[0].bbox.x1
            for o in sorted_objs[1:]:
                if o.bbox.x0 >= max_end:
                    return (o.bbox.x0 + max_end) / 2
                max_end = max(max_end, o.bbox.x1)
        return None
