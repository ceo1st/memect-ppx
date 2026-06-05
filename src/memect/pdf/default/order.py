import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Final, Iterator, Protocol, Self, Sequence

from memect.base import lists
from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.pdf.base import (
    Group,
    KBlock,
    KCell,
    KDocument,
    KFigure,
    KObject,
    KPage,
    KTable,
    KText,
)
from memect.pdf.sort import Sorter


class Item(Protocol):
    @property
    def bbox(self) -> BBox: ...


class ReadingOrder:
    _debugger=XDebugger(f'{__module__}.{__qualname__}')
    def __init__(self):
        super().__init__()

    def parse(self,doc:KDocument,max_workers:int=0):
        self._do(self._parse_page, doc.working_pages, max_workers=max_workers)
        if False:
            #暂时先不启用，因为很难判断
            _TableLayout().sort(doc)
    
    def _parse_page(self,page:KPage):
        #TODO 如果是多页的，还需要考虑表格布局的情况，跨几页
        def expand_objects(objs:Sequence[KObject])->Iterator[KObject]:
            for obj in objs:
                if isinstance(obj,KBlock):
                    yield from expand_objects(obj.objects)
                else:
                    yield obj

        debugger = self._debugger.bind(page=page.number)
        raw_objects = list(page.objects)
        method:str='towcolumn'
        columns = _TowCut().sort(page)
        if len(columns)==1:
            columns = _XYCut().sort(page.objects)
            method='xycut'
        column_bboxes:list[BBox]=[]
        page.objects.clear()
        for column in columns:
            bbox = BBox.join2(column)
            column_bboxes.append(bbox)
            #TODO 需要在这里就展开了吗？应该在章节树解析后
            page.objects.extend(expand_objects(column))

        page.set_blocks(column_bboxes)
        if debugger.allow('draw'):
            page.draw(
                ('page',None),
                (f'columns={method},{len(column_bboxes)}',column_bboxes,'number'),
                ('sections',page.sections,'number'),
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
class _XYCut:
    def __init__(self):
        super().__init__()

    def sort[T: Item](self, bboxes: Sequence[T]) -> list[Sequence[T]]:
        if not bboxes:
            return []
        return self._regroup([b for a in self._cut(bboxes) for b in a])

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
        y_gap = self._find_gap(sorted_y, axis='y')

        sorted_x = sorted(bboxes, key=lambda o: o.bbox.x0)
        x_gap = self._find_gap(sorted_x, axis='x')

        if x_gap is not None:
            left = [o for o in bboxes if o.bbox.x1 <= x_gap]
            right = [o for o in bboxes if o.bbox.x0 >= x_gap]
            mixed = [o for o in bboxes if o not in left and o not in right]
            if not mixed:
                # 纯双栏，列优先：先左列全部再右列全部
                return self._cut(left) + self._cut(right)

        if y_gap is not None:
            above = [o for o in bboxes if o.bbox.y0 >= y_gap]
            below = [o for o in bboxes if o.bbox.y1 <= y_gap]
            mixed = [o for o in bboxes if o not in above and o not in below]
            above += mixed
            return self._cut(above) + self._cut(below)

        if x_gap is not None:
            left = [o for o in bboxes if o.bbox.x1 <= x_gap]
            right = [o for o in bboxes if o.bbox.x0 >= x_gap]
            mixed = [o for o in bboxes if o not in left and o not in right]
            return self._cut(left) + self._cut(right) + (self._cut(mixed) if mixed else [])

        return [bboxes]

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


class Column:
    """表示一个分栏"""
    def __init__(self,page:KPage,bbox:BBox,index:int,columns:Sequence[Self]):
        super().__init__()
        self.page:Final=page
        self.bbox:Final=bbox
        self.index:Final=index
        self.columns:Final=columns
    
    @property
    def next(self)->Self|None:
        if self.index+1<len(self.columns):
            return self.columns[self.index+1]
        else:
            return None

    @property
    def prev(self)->Self|None:
        if self.index-1>=0:
            return self.columns[self.index-1]
        else:
            return None

class _TowCut:
    """双栏"""
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger = XDebugger(f'{__module__}.{__qualname__}')
    def __init__(self):
        super().__init__()
    
    def sort(self,page:KPage):
        #典型的页面通常是1栏，有些为2栏，3栏的很少
        #如果是2栏的，可能会插入一个分栏符，也就是如下
        #[col1][col2]
        #-------------分栏符
        #[--table--]   
        #------------------
        #[col3][col4]
        #--------------分页符
        #TODO 研报的首页更加复杂，页面有不少无序的文字（没有什么逻辑性）

        #debugger = self._debugger.bind(page=page.number)

        def cut(column:Group[KObject],ref_obj:KObject)->tuple[Group[KObject],Group[KObject]]:
            #[obj1]
            #[obj2]
            #[-----ref_obj-----]  被这个对象切开了
            #[obj3]
            #[obj4]
            #column已经按y0排序了
            end=len(column)
            for i in range(len(column)):
                obj = column[i]
                if obj.bbox.y0-ref_obj.bbox.y1>=-5:
                    #允许一点重叠
                    end=i
                    break
            
            top = column[end:]
            bottom = column[0:end]
            return Group[KObject](top),Group[KObject](bottom)

        def split_columns(objs:Sequence[KObject])->tuple[list[Group[KObject]],list[KObject]]:
            """快速的左右分栏"""
            #如何处理特别的情况，如：
            #下面这些不一定属于左右分栏，就是有些文本随意放置
            #    [title]
            #           | [span1]
            #   [span2] |
            #   [-----text-------]
            # 或者
            #          |[text]
            #    [text]|
            #   [------text----]
            #    [text]
            #
            bbox = page.bbox
            left_column = Group[KObject]()
            right_column = Group[KObject]()
            other_objs = list[KObject]()
            for obj in objs:
                if obj.bbox.width>100:
                    dx=10
                else:
                    dx=5
                if obj.bbox.x1-bbox.cx<=dx:
                    left_column.append(obj)
                    left_column.invalidate()
                elif obj.bbox.x0-bbox.cx>=-dx:
                    right_column.append(obj)
                    right_column.invalidate()
                else:
                    #包括线，表格，矩形等
                    #如是垂直线，忽略？
                    other_objs.append(obj)
            
            columns:list[Group[KObject]]=[]
            if left_column:
                columns.append(left_column)
            if right_column:
                columns.append(right_column)
            return columns,other_objs

        def adjust_groups(groups:list[Group[KObject]]):
            lists.remove2(groups,lambda i,groups:len(groups[i])==0)
            if len(groups)<=1:
                return 
            
            #按行排序
            lines:list[list[Group[KObject]]] = Sorter.get_lines(groups)
            
            #也可以不执行
            if True:
                for line in lines:
                    #[ text1
                    #              [text3]
                    # text2 
                    #]
                    #对于这种，合并为一个组更好？
                    if len(line)==2:
                        g1=line[0]
                        g2=line[1]
                        b1=g1.bbox.adjust(y0=g2.bbox.y0,y1=g2.bbox.y1)
                        if not b1.get(g1,ratio=0.1):
                            #[text1]
                            #[--blank--] [text3]   =>可以插入这里，然后合并
                            #[text2]
                            #if debugger.allow('gui'):
                                #page.show('before merge groups',objects=[g.bbox for g in line])

                            g1.extend(g2)
                            g1.invalidate()
                            Sorter.sort(g1)
                            lists.remove(groups,[g2],use_is=True)
                            del line[1]

                            #if debugger.allow('gui'):
                                #page.show('after merge groups',objects=g1)

                            pass
                    pass
            i=1
            while i<len(lines):
                #如果有并排
                line1=lines[i-1]
                line2=lines[i]
                if len(line1)==1 and len(line2)==1:
                    #        [group1]
                    #[group2]
                    #合并为一个组？
                    line1[0].extend(line2[0])
                    line1[0].invalidate()
                    del lines[i]
                    lists.remove(groups,line2,use_is=True)
                else:
                    i+=1

            #简单的是左右分栏，复杂的情况为
            #[----top----]      =>这个也需要记录
            #[left]|[right]
            #[----bottom-]      =>这个也需要记录
            #-----------------------分页
            #[left]|[right]
            

        def sort(objs:list[KObject]):
            lines = [Group[KObject](line) for line in Sorter.get_lines(objs)]
            i=0
            new_lines:list[list[KObject]]=[]
            while i<len(lines):
                line = lines[i]
                if len(line)==2 and isinstance(line[0],KText) and line[-1].bbox.height>=50 and isinstance(line[-1],(KBlock,KTable,KFigure)):
                    #TODO 可以不限制高度的
                    #[text] | [table] or [figure]
                    right_object = line[-1]
                    bbox = BBox.join2(line)
                    #assert bbox is not None
                    left_lines:list[Group[KObject]]=[]
                    for j in range(i+1,len(lines)):
                        line2 = lines[j]
                        if line2.bbox.x1<=right_object.bbox.x0 and line2.bbox.y1-bbox.y0>=5:
                            left_lines.append(line2)
                        else:
                            break

                    
                    if len(left_lines)>0:
                        new_lines.append(line[0:-1])
                        new_lines.extend(left_lines)
                        new_lines.append(line[-1:])
                        i+=1+len(left_lines)
                    else:
                        i+=1
                        new_lines.append(line)
                else:
                    i+=1
                    new_lines.append(line)
            
            objs.clear()
            for line in new_lines:
                objs.extend(line)
            return 


        objs = list(page.objects)

        columns,other_objs = split_columns(objs)
        groups:list[Group[KObject]]=[]
        
        if len(columns)<=1:
            #如果只有1列，可能为研报的首页，需要做更加复杂的处理？
            #也就是不一定需要水平居中，可能是7:3，3:7这样划分
            groups.append(Group(objs))
        else:
            #如果能够划分2列，表示为典型的双栏
            for column in columns:
                column.sort(key=lambda obj:obj.bbox.y0)
            
            other_objs.sort(key=lambda obj:obj.bbox.y1,reverse=True)

            row:Group[KObject]|None=None
            for obj in other_objs:
                if columns:
                    new_columns:list[Group[KObject]]=[]
                    for column in columns:
                        top,bottom=cut(column,obj)
                        
                        if top:
                            #表示需要插入一新行
                            row=None
                            groups.append(top)
                            
                        else:
                            pass

                        if bottom:
                            new_columns.append(bottom)
                            
                    
                    columns = new_columns

                if row is None:
                    #没有或者需要插入一个新行
                    row = Group()
                    groups.append(row)
            
                row.append(obj)
                row.invalidate()
            
            groups.extend(columns)
        

        adjust_groups(groups)
        for group in groups:
            #Sorter.sort(group)
            sort(group)
        
        return groups
        

class _TableLayout:
    _logger=logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger=XDebugger(f'{__module__}.{__qualname__}')
    def __init__(self):
        super().__init__()
    
    def sort(self,doc:KDocument):
        #对于有些使用表格布局且跨页的，需要在这里转换为表格，仅仅分栏无法还原顺序，如：
        #-----xxx-----
        #xxx|xxxxxxxxx
        #xxx|xxxxxxxxx
        #--------------跨页
        #   |xxxxxxx
        #--------------跨页
        #   |xxxxxx

        #目前没有太好的办法识别连续多个页面是否是使用表格布局的
        #通过语义判断意义也不大，因内容可以为任意
        pages = doc.working_pages
        i=0
        while i<len(pages):
            j=self._parse_once(i,pages)
            if j==-1:
                i+=1
            else:
                i=j

            
    def _parse_once(self,index:int,pages:Sequence[KPage]):

        def accept(c1:BBox,c2:BBox,c3:BBox|None,c4:BBox)->bool:
            #c1|c2
            #------
            #c3|c4
            cx=(c1.x1+c2.x0)/2
            if c3 is not None:
                if c3.x1>cx or c4.x0<cx:
                    return False
                return True
            else:
                if c4.x0<cx:
                    return False
                return True
        if index+1>=len(pages):
            return -1
        
        page1 = pages[index]
        page2 = pages[index+1]
        if page1.number+1!=page2.number:
            return -1

        #有太多的可能，现在仅仅处理最常见的一种
        #c1|c2   =>需要识别为表格[c1,c2]
        #-----分页
        #c3|c4   =>需要识别为表格[c3,c4]
        #------
        #  |c5   =>需要识别为表格[None,c5]
        #------分页
        #c6|     =>需要识别为表格[c6,None]

        columns1 = page1.columns or []
        c1,c2 = columns1[-2:]
        #先判断c1和c2
        #c1|c2
        if not (c1.x1<c2.x0 and c1.over('y',c2,d=100)):
            return -1
        
        tables:list[tuple[KPage,Sequence[BBox]]]=[]
        tables.append((page1,(c1,c2)))
        for page2 in pages[index+1:]:
            page1,(c1,c2) = tables[-1]
            if page2.number!=page1.number+1:
                break
            if not page1.bbox.align('x',page2.bbox,d=2):
                break
            columns2 = page2.columns or []
            if len(columns2)==1:
                #None|c4
                c3,c4=None,columns2[0]
            elif len(columns2)==2:
                #c3|c4
                c3,c4=columns2
            else:
                break


            if not accept(c1,c2,c3,c4):
                break

            if c3 is None:
                c3 = c1
            
            #调整一下对齐
            x1=max(c1.x1,c3.x1)
            x0=min(c2.x0,c4.x0)
            c1=c1.adjust(x1=x1)
            c3=c3.adjust(x1=x1)
            c2=c2.adjust(x0=x0)
            c4=c4.adjust(x0=x0)
            tables[-1]=(page1,(c1,c2))
            tables.append((page2,(c3,c4)))

        if len(tables)<2:
            return -1
        

        self._logger.info('第%s页面使用表格布局',[p.number for p,_ in tables])
        debugger = self._debugger.bind()
        #可以简单调整一下bboxes的对齐

        for page,bboxes in tables:
            cells:list[KCell]=[]
            for col_index,cell_bbox in enumerate(bboxes):
                objs = cell_bbox.get(page.objects,ratio=0.9,remove=True)
                cell=KCell(page,cell_bbox,row_index=0,col_index=col_index,objects=objs)
                cells.append(cell)
            table = KTable(page,BBox.join(bboxes),cells=cells)
            table.adjust()
            page.objects.append(table)
            if debugger.allow('draw',page=page.number):
                page.draw(
                    ('page',None),
                    ('columns',bboxes,'number'),
                    ('table',table.get_lines2()),
                    show_type=False,
                    dir='debug/default/tablelayout'
                )
            
        
        return index+len(tables)