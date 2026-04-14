# coding=utf-8

from typing import Any, Final, Literal, Sequence

from memect.base.bbox import BBox

type _Axis = list[list[float]]


class Gap:
    def __init__(self, type: Literal['line', 'space'], dir: Literal['h', 'v'], bbox: BBox):
        super().__init__()
        # 表示间隔的类型，line or space
        self.type: Final = type
        # 表示间隔的方向，h（水平）or v（垂直）
        self.dir: Final = dir
        # 表示bbox
        self.bbox: Final = bbox


class Item:
    """
    对于需要使用新的bbox的对象，可以使用这个类来替代
    """

    def __init__(self, type: str, bbox: BBox, object: Any = None):
        super().__init__()
        self.type = type
        self.bbox = bbox
        self.object = object


class Plane:
    """
    计算平面上的水平或者垂直的gap
    """

    def __init__(self):
        pass

    def clip_rect(self, axis: _Axis, bbox: BBox, is_h: bool):
        """
        减去一个矩形，剩余的就是没有内容的空间
        axis:[] 坐标轴
        bbox:[],
        is_h: True表示x轴，False表示y轴
        """
        if is_h:
            x0, x1, y0, y1 = 0, 2, 1, 3
        else:
            x0, x1, y0, y1 = 1, 3, 0, 2

        def add(axis: _Axis, a: float, b: float):
            # for s in axis[:]:
            i = 0
            while i < len(axis):
                s = axis[i]
                if s[0] == s[1]:
                    # axis.remove(s)
                    del axis[i]
                    continue
                if a <= s[0] and b >= s[1]:
                    # axis.remove(s)
                    del axis[i]
                elif s[0] <= a and b <= s[1]:
                    # axis.remove(s)
                    del axis[i]
                    axis.insert(i, [s[0], a])
                    axis.insert(i+1, [b, s[1]])
                    # 不需要再继续
                    break
                elif s[0] <= a < s[1]:
                    s[1] = a
                    i += 1
                elif s[0] < b <= s[1]:
                    s[0] = b
                    # 不需要再继续
                    break
                elif b <= s[0]:
                    break
                else:
                    i += 1

        add(axis, bbox[x0], bbox[x1])

    def clip_rects(self, axis: _Axis, bboxes: Sequence[BBox], is_h: bool):
        for b in bboxes:
            self.clip_rect(axis, b, is_h)

    def get_row_gaps(self, bbox: BBox, items: Sequence[Item], min_height: int = 1, strict: bool = True):
        """
        bbox:[],
        items:[],
        min_height:表示空白的最小高度，小于该宽度的抛弃
        title:
        painter:
        strict: True表示gap考虑所有的item，False表示分2次计算，如果是非水平线的，考虑min_width，水平线的，min_width=1
        """
        return self.get_gaps(bbox, items, False, min_width=min_height, strict=strict)

    def get_column_gaps(self, bbox: BBox, items: Sequence[Item], *, min_width: int = 3, strict: bool = True):
        """
        bbox:[],
        items:[],
        min_width:表示空白的最宽度，小于该宽度的抛弃
        title:
        painter:
        strict: True表示gap考虑所有的item，False表示分2次计算，如果是非水平线的，考虑min_width，水平线的，min_width=1
        """
        return self.get_gaps(bbox, items, True, min_width=min_width, strict=strict)

    def get_gaps(self, bbox: BBox, items: Sequence[Item], is_h: bool, *, min_width: int = 3, strict: bool = True) -> list[BBox]:
        """
        bbox:[],
        items:[obj1,obj2], 可以获得bbox的对象，如：{bbox:[]} or [] or a.bbox
        is_h: True 表示在水平方向划分，获得列，False表示获得行
        min_width:表示空白的最宽度，小于该宽度的抛弃
        title:
        strict: True表示gap考虑所有的item，False表示分2次计算，如果是非水平线的，考虑min_width，水平线的，min_width=1
        """
        def process(items: Sequence[Item], min_width: int) -> list[BBox]:
            if is_h:
                x0, x1, y0, y1 = 0, 2, 1, 3
            else:
                x0, x1, y0, y1 = 1, 3, 0, 2

            axis = [[bbox[x0], bbox[x1]]]
            # self.x_axis = [[bbox[0], bbox[2]]]
            # 返回的是：[[a1,a2],[b1,b2],[c1,c3]] 从上到下排序 或者从右到左排序
            self.clip_rects(axis, [item.bbox for item in items], is_h)

            # 去掉前后的
            if axis and axis[0][0] == bbox[x0]:
                del axis[0]
            if axis and axis[-1][1] == bbox[x1]:
                del axis[-1]

            if is_h:
                # 从左到右
                axis = sorted(axis, key=lambda s: s[0])
            else:
                # 从上到下
                axis = sorted(axis, key=lambda s: s[0], reverse=True)
            # print('axis=',min_width,is_h,bbox[x0],bbox[x1], axis)
            if is_h:
                gaps = [BBox(s[0], bbox[1], s[1], bbox[3])
                        for s in axis if s[1] - s[0] >= min_width]
                cx = (bbox[2]+bbox[0])//2
                if not gaps and len(axis) == 1 and axis[0][1]-axis[0][0] >= 4 and axis[0][0]-cx <= 5 and axis[0][1] >= cx:
                    # -------|(中线)
                    # ---------[gap[0]][gap[1]]  -> 溢出中线一点距离
                    # print(cx,bbox,axis)
                    # 如果没有满足的，且刚好有一条，可能是居中了
                    gaps = [BBox(axis[0][0], bbox[1], axis[0][1], bbox[3])]
            else:
                gaps = [BBox(bbox[0], s[0], bbox[2], s[1])
                        for s in axis if s[1] - s[0] >= min_width]
            return gaps

        if strict:
            gaps = process(items, min_width=min_width)
        else:
            # strict=False,要求item必须存在type属性
            if len(items) > 0 and not hasattr(items[0], 'type'):
                raise ValueError('strict=True,items的元素必须包含type属性')
            # 为了支持如下
            # --------- ----------(存在水平线，水平间距很小)
            # xxxxxx     xxxxxx
            # --------- ----------
            # 先不考虑水平线，获得大的gaps
            # 再仅仅考虑水平线，获得小的gaps
            # 然后计算重叠的
            items2 = [item for item in items if item.type != 'line']
            items3 = [item for item in items if item.type == 'line']
            gaps1 = process(items2, min_width=min_width)
            gaps2 = process(items3, min_width=1)
            gaps: list[BBox] = []
            for gap1 in gaps1:
                for gap2 in gaps2:
                    if gap1.contains(gap2):
                        gaps.append(gap2)
                        break

        # self.draw_gaps(painter,title,bbox,gaps)
        return gaps
