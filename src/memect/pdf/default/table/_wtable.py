import inspect
import logging
import math
import os
from typing import Any, Final, Iterable, Literal, Sequence, overload

import cv2
import numpy as np
import PIL
import PIL.ImageDraw
from memect.base import lists, utils
from PIL.Image import Image

from memect.base.bbox import BBox, Group
from memect.base.matrix import Matrix
from memect.base.debug import XDebugger

from memect.pdf.base import KObject
from memect.pdf.grid import Grid
#from ._xbase import XTable

#允许修改
type _Line=list[float]
type _Range=tuple[int,int,int,int]

#local/嘉实bugs/pdfs/issue12-1.pdf


class WObject:
    """表示单元格中包含的对象，支持跨页/跨栏合并后，使用新的BBox"""
    def __init__(self,bbox:BBox,*,object:KObject):
        super().__init__()
        self.bbox=bbox
        self.object:Final=object

class WCell:
    """
    单元格，可以表示单页，跨页，跨栏的单元格
    """
    def __init__(self,bbox:BBox):
        super().__init__()
        #self._bbox = bbox
        self.objects:list[WObject]=[]
        """包含的对象"""
        self.col_index:int=0
        self.row_index:int=0
        self.col_span:int=1
        self.row_span:int=1
        #self.table:WTable

        self._text:str|None=None
        self._text2:str|None=None

        self.bbox = bbox

        #这两个是给分行分列的时候使用新的bbox
        self.row_bbox:BBox|None=None
        self.col_bbox:BBox|None=None
        self.invalidate()
    
    @property
    def bbox(self)->BBox:
        return self._bbox
    
    @bbox.setter
    def bbox(self,b:BBox):
        self._bbox = b
        dx=0
        dy=0
        if b.width>=8:
            dx=-1
        if b.height>=8:
            dy=-1
        self.min_bbox = b.adjust(dx=dx,dy=dy)
    
    @property
    def content_bbox(self)->BBox|None:
        if self._content_bbox is None:
            self._content_bbox = BBox.join2(self.objects)
        return self._content_bbox
    
    @property
    def full_bbox(self)->BBox:
        """如果内容小于bbox，返回内容的bbox，否则返回内容和bbox的交集"""
        cb = self._content_bbox
        if not cb:
            return self.bbox
        #content_bbox可能溢出，所以取交集
        xb = self.bbox.intersect([cb])
        if xb is None:
            #如果内容全部溢出，返回None还是self.bbox
            return self.bbox
        else:
            return xb

    @property
    def pages(self)->Sequence[Page]:
        #TODO 如果为空白的单元格，如何获得？
        pages:list[Page]=[]
        for obj in self.objects:
            if obj.page not in pages:
                pages.append(obj.page)
        
        pages.sort(key=lambda page:page.number)
        return pages
    
    @property
    def text(self)->str:
        if self._text is None:
            buf:list[str]=[]
            for t in self.objects:
                if isinstance(t.object,Text):
                    buf.append(t.object.text)
            self._text=''.join(buf)
        return self._text

    @property
    def text2(self)->str:
        if self._text2 is None:
            buf:list[str]=[]
            for t in self.objects:
                if isinstance(t.object,Text):
                    buf.append(t.object.text2)
            self._text2=''.join(buf)
        return self._text2
    
    def is_blank(self)->bool:
        """判断是否为空白的单元格"""
        return len(self.objects)==0

    def invalidate(self):
        self._text=None
        self._text2=None
        self._content_bbox=None
        self._layout()
    
    def _layout(self):
        """单元格合并后，可能还需要重新排序"""
        #如果只有文本，简单的变成char再排序即可
        #如果有图片
        if len(self.objects)<2:
            #只有一个对象不需要layout
            #在合并单元格后，可能变成多个，需要layout
            return
        
        #先按block划分
        def split_blocks(objs:list[WObject])->dict[Block,list[WObject]]:
            blocks:dict[Block,list[WObject]]={}
            for obj in objs:
                group = blocks.setdefault(obj.block,[])
                group.append(obj)
            return blocks
        
        def make_text(block:Block,wobjs:Sequence[WObject])->WObject:
            assert len(wobjs)>0
            if len(wobjs)==1:
                return wobjs[0]
            objs:list[TObject]=[]
            for wobj in wobjs:
                assert isinstance(wobj.object,Text)
                objs.extend(wobj.object.objects)
            text=Text.create(objects=objs)
            bbox = BBox.x_union(wobjs)
            assert bbox is not None
            return WObject(bbox,object=text,block=block)
        
        #如果需要支持复杂的跨页跨栏合并，可能不需要划分blocks，直接排序更好
        #如：
        #[text11||text21]
        #------------------跨页了，阅读顺序可能为[text11,text12,text21,text22]
        #[text12||text22]
        method=1
        if method==1:
            pass
        blocks = split_blocks(self.objects)
        #如果只是简单，可以使用如下排序即可
        #Sorter.sort()
        #但是目前因为存在文本，对文本进行合并再处理
        new_wobjs:list[WObject]=[]
        for block,wobjs in blocks.items():
            
            lines:list[list[WObject]]=Sorter.get_lines(wobjs)
            group:list[WObject]=[]
            for line in lines:
                for wobj in line:
                    if isinstance(wobj.object,Text):
                        group.append(wobj)
                    else:
                        if len(group)>0:
                            new_wobjs.append(make_text(block,group))
                            group.clear()
                        new_wobjs.append(wobj)
                        group.clear()

            if len(group)>0:
                new_wobjs.append(make_text(block,group))
        self.objects.clear()
        self.objects.extend(new_wobjs)
        


class _Item:
    """适配Grid的接口对象"""
    def __init__(self,wcell:WCell):
        super().__init__()
        self.bbox = wcell.bbox
        self.wcell = wcell


class WTable:
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger=XDebugger(f'{__module__}.{__qualname__}')
    def __init__(self,cells:Sequence[WCell],*,bbox:BBox|None=None,page:Page|None=None,image:Image|None=None):
        super().__init__()
        assert len(cells)>0
        self.image = image
        """跨页表格的图片，可以为几个跨页/跨栏表格合并为一个，表示bbox是相对这个图片"""
        self.page = page
        """单页表格所在的页面，表示bbox相对页面"""
        self.h_lines:list[_Line]=[]
        self.v_lines:list[_Line]=[]
        self.cells:Sequence[WCell]=tuple(cells)

        if bbox is None:
            bbox = BBox.x_union(cells)
            assert bbox is not None
        else:
            #确保包含所有的cells
            bbox =  bbox.union(cells)
            #同时需要调整bbox对齐
            top=max(cells,key=lambda cell:cell.bbox.y1)
            bottom=min(cells,key=lambda cell:cell.bbox.y0)
            right=max(cells,key=lambda cell:cell.bbox.x1)
            left=min(cells,key=lambda cell:cell.bbox.x0)
            top.bbox=top.bbox.copy(y1=bbox.y1)
            bottom.bbox=bottom.bbox.copy(y0=bbox.y0)
            right.bbox=right.bbox.copy(x1=bbox.x1)
            left.bbox=left.bbox.copy(x0=bbox.x0)

        self.bbox = bbox

        self.col_num:int=0
        self.row_num:int=0
        self._grid:list[list[WCell]]=[]

        self._build()

    @property
    def pages(self)->Sequence[Page]:
        pages:list[Page]=[]

        #TODO 如果所有的单元格的内容都为空，允许存在这样的无边框表格吗？
        for cell in self.cells:
            for page in cell.pages:
                if page not in pages:
                    pages.append(page)
        
        pages.sort(key=lambda page:page.number)
        #至少有一个单元格有内容，也就是在具体的一个页面上
        assert len(pages)>0
        return pages
    
    def _adjust_cells(self):
        #单元格相邻区域可能重叠，需要调整一下，否则无法准确画线
        for cell in self.cells:
            cell.bbox=cell.bbox.large
        
    
    def _build(self):
        #必须先获得，因为后面会清空self.cells
        first_page_number = self.pages[0].number
        debugger=self._debugger.bind(page=first_page_number)
        #需要先调整一下单元格，避免出现夹空问题
        self._adjust_cells()    
        TableSketch().draw(self)
        LineCleaner().clean(self)
        # 更新self.bbox，因为可能调整了
        bbox=BBox.x_union([*self.v_lines,*self.h_lines])
        assert bbox is not None
        self.bbox = bbox

        if debugger.allow('gui'):
            self.show('final result')
        #计算表格的结构
        lines:list[tuple[float,float,float,float]]=[]
        for line in self.h_lines:
            lines.append((line[0],line[1],line[2],line[3]))
        for line in self.v_lines:
            lines.append((line[0],line[1],line[2],line[3]))
        grid = Grid(lines,items=[_Item(wcell) for wcell in self.cells])
        self.row_num = grid.row_num
        self.col_num = grid.col_num
        self.cells = []
        strict=False
        for cell in grid.cells:
            wcell = WCell(cell.bbox)
            wcell.row_index=cell.row_index
            wcell.col_index=cell.col_index
            wcell.row_span = cell.row_span
            wcell.col_span = cell.col_span
            if len(cell.objects)>1:
                #只能够包含一个0-1个对象
                #错误了，不应该包含多个对象，要么是grid.fill错误了，要么是前面的lines错误了
                #多数情况是单元格重叠了，导致画线后为一个，如：
                #[---a---|---b---]  => b和a有重叠，所以就无法画线，变成一个单元格，然后这里就选择2个对象 
                #或者a和b垂直重叠
                #[--a--]
                #[--b--]
                self._logger.warning('单元格包含了多个对象,page=%s,objects=%s',first_page_number,len(cell.objects))
                if debugger.allow('info'):
                    with debugger.group('cell objects'):
                        for obj in cell.objects:
                            debugger.console.print((obj.wcell.bbox,obj.wcell.text))
                if strict:
                    #方便开发的时候发现问题
                    raise RuntimeError(f'编程错误，请检查代码')
            
            for item in cell.objects:
                assert isinstance(item,_Item)
                #TODO 如果是两个Text对象合并的，还需要重新组合排序，如：
                #[xxxxx]|[vvvvv] => [xxxxx,vvvvv]
                wcell.objects.extend(item.wcell.objects)
            wcell.invalidate()
            self.cells.append(wcell)
        #为了更好的性能，建立一个n*m的grid，可以更快速的访问
        self.rebuild_grid()

    def _rebuild_cells(self):
        """grid的为最新的，根据grid更新"""
        cells:list[WCell]=self[:,:]
        lists.distinct(cells)
        self._sort_cells(cells)
        self.cells = tuple(cells)
    
    def _sort_cells(self,cells:list[WCell]):
        """按顺序排序"""
        def cmp(c1:WCell,c2:WCell)->int:
            if c1.row_index<c2.row_index:
                return -1
            elif c1.row_index>c2.row_index:
                return 1
            else:
                return c1.col_index-c2.col_index
        
        return lists.sort(cells,cmp)

    def rebuild_grid(self):
        """如果对cell进行了调整，可以简单重新rebuild，轻量级操作"""
        self._grid.clear()
        for i in range(self.row_num):
            row:list[WCell]=[None]*self.col_num # type: ignore
            self._grid.append(row)
        for cell in self.cells:
            for i in range(cell.row_index,cell.row_index+cell.row_span):
                for j in range(cell.col_index,cell.col_index+cell.col_span):
                    self._grid[i][j]=cell
        
        for row in self._grid:
            for cell in row:
                if cell is None: # type: ignore
                    raise RuntimeError(f'编程错误，提供的cells丢失列或者行')

    def merge_cells(self,start_row_index:int,end_row_index:int,start_col_index:int,end_col_index:int,*,strict:bool=True):
        """合并指定范围的单元格为1个"""
        if strict:
            if not self.can_merge(start_row_index,end_row_index,start_col_index,end_col_index):
                raise ValueError(f'无法合并指定范围的单元格')
        else:
            start_row_index,end_row_index,start_col_index,end_col_index = self.ensure_range(start_row_index,end_row_index,start_col_index,end_col_index)
        
        #如果只有一个，不需要改变
        used_cells = self[start_row_index:end_row_index,start_col_index:end_col_index]
        lists.distinct(used_cells)
        if len(used_cells)<=1:
            return 
        bbox = BBox.x_union(used_cells)
        assert bbox is not None
        cell = WCell(bbox)
        cell.row_index = start_row_index
        cell.row_span = end_row_index-start_row_index
        cell.col_index = start_col_index
        cell.col_span = end_col_index-start_col_index
        for c in used_cells:
            cell.objects.extend(c.objects)
        #替换指定范围的单元格为同一个
        self[start_row_index:end_row_index,start_col_index:end_col_index]=cell
        #self.cells也需要同步更新
        self._rebuild_cells()
        return cell

        

    def can_merge(self,start_row_index:int,end_row_index:int,start_col_index:int,end_col_index:int)->bool:
        """判断在指定范围内的单元格是否可以合并"""
        for i in range(start_row_index,end_row_index):
            for j in range(start_col_index,end_col_index):
                cell = self[i,j]
                if start_row_index<=cell.row_index and cell.row_index+cell.row_span<=end_row_index and start_col_index<=cell.col_index and cell.col_index+cell.col_span<=end_col_index:
                    pass
                else:
                    return False
        return True
    
    def split_cell(self,cell:WCell,row_index:int|None=None,col_index:int|None=None):
        """把一个单元格分成2个（按行或者按列），因为需要切分内容是很困难的，特别是已经构造为文本行等"""

        def replace_cells(cells:Sequence[WCell]):
            for cell in cells:
                self[cell.row_index:cell.row_index+cell.row_span,cell.col_index:cell.col_index+cell.col_span]=cell
            #self.cells也需要更新
            self._rebuild_cells()
        
        def split_objects(cell:WCell,c1:WCell,c2:WCell):
            #考虑到在实际中，应该有一个为空的，因为如果不为空，就不应该切割了

            #TODO 在有些情况下，内容溢出单元格，如下计算就不正确了
            if len(cell.objects)==0:
                return
            
            b1=c1.bbox
            b2=c2.bbox

            #TODO 这么切分实际上是不正确的，如果为文本行，无法切分，只能够回到字符
            #目前如下切分仅仅是支持把一个大的单元格，切分为一个有内容，一个没有内容，如：
            #[xxxxxx     ] => [xxxxxx][    ]
                #两个都没有对象，就看交集
            cb = cell.content_bbox
            assert cb is not None
            a1=b1.intersect([cb])
            a2=b2.intersect([cb])
            objs1=[]
            objs2=[]
            if a1 is None:
                objs2 = cell.objects
            elif a2 is None:
                objs1 = cell.objects
            else:
                if a1.area>=a2.area:
                    objs1 = cell.objects
                else:
                    objs2 = cell.objects
            
            cell1.objects.extend(objs1)
            cell2.objects.extend(objs2)
            cell1.invalidate()
            cell2.invalidate()

        if row_index is not None:
            assert cell.row_span>1
            assert cell.row_index<row_index<cell.row_index+cell.row_span
            for c in self.cells:
                if c.row_index==row_index:#and c.row_span==1:
                    break
            else:
                #如果没有，那么和下一行的合并
                raise RuntimeError(f'编程错误')
            
            b1 = cell.bbox.copy(y0=c.bbox.y1)
            b2 = cell.bbox.copy(y1=c.bbox.y1)
            cell1 = WCell(b1)
            cell1.row_index=cell.row_index
            cell1.row_span=row_index-cell.row_index
            cell1.col_index = cell.col_index
            cell1.col_span = cell.col_span

            #
            cell2 = WCell(b2)
            cell2.row_index = row_index
            cell2.row_span = cell.row_span-cell1.row_span
            cell2.col_index = cell.col_index
            cell2.col_span = cell.col_span

            split_objects(cell,cell1,cell2)
            replace_cells([cell1,cell2])
            
        elif col_index is not None:
            assert cell.col_span>1
            assert cell.col_index<col_index<cell.col_index+cell.col_span
            for c in self.cells:
                if c.col_index==col_index:#and c.col_span==1:
                    break
            else:
                raise RuntimeError(f'编程错误')
            
            b1 = cell.bbox.copy(x1=c.bbox.x0)
            b2 = cell.bbox.copy(x0=c.bbox.x0)
            cell1 = WCell(b1)
            cell2 = WCell(b2)
            cell1.col_index=cell.col_index
            cell1.col_span=col_index-cell.col_index
            cell1.row_index = cell.row_index
            cell1.row_span = cell.row_span

            #
            cell2.col_index = col_index
            cell2.col_span = cell.col_span-cell1.col_span
            cell2.row_index = cell.row_index
            cell2.row_span = cell.row_span

            split_objects(cell,cell1,cell2)
            replace_cells([cell1,cell2])
        else:
            raise ValueError(f'row_index,col_index必须设置一个')


    def ensure_range(self,start_row_index:int,end_row_index:int,start_col_index:int,end_col_index:int)->tuple[int,int,int,int]:
        """确保指定的单元格范围行列对齐"""
        method=1
        if method==1:
            cells = self[start_row_index:end_row_index,start_col_index:end_col_index]
            assert len(cells)>0
            i1=min(c.row_index for c in cells)
            i2=max(c.row_index+c.row_span for c in cells)
            j1=min(c.col_index for c in cells)
            j2=max(c.col_index+c.col_span for c in cells)
        else:
            #对于数据量大的，这种算法慢一些，因为比较的次数更多
            cells:list[WCell]=[]
            for cell in self.cells:
                #支持溢出
                if cell.row_index<end_row_index and cell.row_index+cell.row_span>start_row_index \
                    and cell.col_index<end_col_index and cell.col_index+cell.col_span>start_col_index:
                    cells.append(cell)
            i1=cells[0].row_index
            i2=cells[-1].row_index+cells[-1].row_span
            j1=cells[0].col_index
            j2=cells[-1].col_index+cells[-1].col_span

        if i1==start_row_index and i2==end_row_index and j1==start_col_index and j2 == end_col_index:
            return (i1,i2,j1,j2)
        else:
            return self.ensure_range(i1,i2,j1,j2)

    def get_row(self,index:int,*,strict:bool=False)->list[WCell]:
        """获得指定行的单元格，跨列的只返回一个

        index： 第几列，0表示第一列

        strict: True表示返回的单元格的row_index==index
        """
        row:list[WCell]=[]
        for c in self._grid[index]:
            if c not in row and (not strict or c.row_index==index):
                row.append(c)
        return row
    
    def get_column(self,index:int,*,strict:bool=False)->list[WCell]:
        column:list[WCell]=[]
        for row in self._grid:
            c = row[index]
            if c not in column and (not strict or c.col_index==index):
                column.append(c)
        return column
    
    def __setitem__(self,key:tuple[int,int]|tuple[slice,slice],cell:WCell):
        i,j = key
        if isinstance(i,int) and isinstance(j,int):
            self._grid[i][j]=cell
        elif isinstance(i,slice) and isinstance(j,slice):
            start = j.start if j.start is not None else 0
            stop = j.stop if j.stop is not None else self.col_num
            step = j.step if j.step is not None else 1
            n=len(range(start,stop,step))
            for row in self._grid[i]:
                #=> row[0:4]=[c0,c1,c2,c3]
                row[j]=[cell]*n
        else:
            raise ValueError(f'不支持的key={key}')

        #同时需要更新self.cells
        new_cells:list[WCell]=[]
        for i in range(self.row_num):
            for j in range(self.col_num):
                cell = self[i,j]
                if cell not in new_cells:
                    new_cells.append(cell)
        self.cells = new_cells
        
    @overload
    def __getitem__(self,key:tuple[int,int])->WCell:
        pass

    @overload
    def __getitem__(self,key:tuple[slice,slice])->list[WCell]:
        pass

    def __getitem__(self,key:tuple[int,int]|tuple[slice,slice])->WCell|list[WCell]:
        i,j = key
        if isinstance(i,int) and isinstance(j,int):
            return self._grid[i][j]
        elif isinstance(i,slice) and isinstance(j,slice):
            cells:list[WCell]=[]
            for row in self._grid[i]:
                cells.extend(row[j])
            return cells
        else:
            raise ValueError(f'不支持的key={key}')
    
    def rebuild(self,*,lines:bool=True):
        """如果对cells等进行了修改，可以重新计算row_num,col_num，目前仅仅是对于合并（也就是删除行列）"""

        def adjust_column1(i:int)->int:
            column = self.get_column(i,strict=True)
            if column:
                return i+1
            
            column = self.get_column(i,strict=False)
            for c in column:
                assert c.col_span>1
                c.col_span-=1
            
            for row in self._grid:
                del row[i]

            for cell in self.cells:
                if cell.col_index>i:
                    cell.col_index-=1
            self.col_num-=1
            return i
        
        def adjust_column2(i:int)->int:
            column:list[WCell]=[]
            for j in range(self.row_num):
                c1=self[j,i]
                c2=self[j,i+1]
                if c1 is not c2:
                    return i+1
                if c1 not in column:
                    column.append(c1)

            for cell in column:
                cell.col_span-=1
            
            for row in self._grid:
                del row[i+1]

            for cell in self.cells:
                if cell.col_index>i:
                    cell.col_index-=1
            self.col_num-=1
            return i

        def adjust_columns():
            i=0
            while i<self.col_num:
                i=adjust_column1(i)

            i=0
            while i<self.col_num-1:
                i=adjust_column2(i)

        def adjust_row1(i:int)->int:
            row = self.get_row(i,strict=True)
            if row:
                return i+1
            
            row = self.get_row(i,strict=False)
            for c in row:
                c.row_span-=1
            
            del self._grid[i]

            for cell in self.cells:
                if cell.row_index>i:
                    cell.row_index-=1
            self.row_num-=1
            return i
        def adjust_row2(i:int)->int:
            row:list[WCell]=[]
            for j in range(self.col_num):
                c1=self[i,j]
                c2=self[i+1,j]
                if c1 is not c2:
                    return i+1
                if c1 not in row:
                    row.append(c1)
                
            #表示可以调整一行
            for cell in row:
                cell.row_span-=1
            
            del self._grid[i+1]

            for cell in self.cells:
                if cell.row_index>i:
                    cell.row_index-=1
            
            self.row_num-=1
            return i
        
        def adjust_rows():
            i=0
            while i<self.row_num:
                i=adjust_row1(i)
            
            i=0
            while i<self.row_num-1:
                i=adjust_row2(i)

        def split_groups(cells:list[WCell],is_row:bool)->list[Group[WCell]]:
            if is_row:
                cells.sort(key=lambda c:c.col_index)
            else:
                #cells为列
                cells.sort(key=lambda c:c.row_index)
            groups:list[Group[WCell]]=[]
            group=Group[WCell]()
            for c in cells:
                if not group:
                    group.append(c)
                    groups.append(group)
                elif (is_row and c.col_index==group[-1].col_index+group[-1].col_span) or (not is_row and c.row_index == group[-1].row_span+group[-1].row_index):
                    group.append(c)
                else:
                    group=Group[WCell]()
                    group.append(c)
                    groups.append(group)
            for group in groups:
                group.invalidate()
            
            return groups
            
                
        def adjust_h_lines():
            h_lines:list[_Line]=[]
            h_lines.append(self.bbox.top.to_list())
            for i in range(1,self.row_num):
                row = self.get_row(i,strict=True)
                for group in split_groups(row,True):
                    h_lines.append(group.bbox.top.to_list())
            
            h_lines.append(self.bbox.bottom.to_list())

            self.h_lines = h_lines
        
        def adjust_v_lines():
            v_lines:list[_Line]=[]
            v_lines.append(self.bbox.left.to_list())
            for i in range(1,self.col_num):
                col = self.get_column(i,strict=True)
                for group in split_groups(col,False):
                    v_lines.append(group.bbox.left.to_list())
            
            v_lines.append(self.bbox.right.to_list())
            self.v_lines = v_lines
        
        adjust_columns()
        adjust_rows()
        #不执行也可以，因为上面自动调整了
        #self._setup_grid()
        #重新计算h_lines,v_lines
        if lines:
            #在连续的调整中，可以不需要马上rebuild lines
            #节约一丁点时间
            adjust_h_lines()
            adjust_v_lines()
    
        
        
    def beautify(self,*,clean:bool=True):
        debugger = self._debugger.bind(page=self.pages[0].number)
        timer = utils.Timer.start()

        #TODO 如果是来自pdf的，可以直接获得线，矩形，然后根据这些调整，可以获得更完美的结果
        #TODO 如果是来自图片，可以使用opencv来获得垂直线/水平线，甚至彩色矩形，获得更完美的结果
        #使用opencv获得线，速度很快，也不需要做太精确的计算了，只需要判断相邻区域是否有垂直线，然后调整到垂直线位置就可以

        h_lines:list[Line]=[]
        v_lines:list[Line]=[]
        x_rects:list[Rect]=[]
        y_rects:list[Rect]=[]
        #表示当前表格来自这个页面，所以直接使用页面原始的线
        #如果是图片表格，可以在这里识别线？
        if self.page is not None:
            #获得在表格区域内的线，可以让范围更加小一些
            always_use_ocr=False
            if always_use_ocr or self.page.type==PageType.IMAGE:
                #TODO 可以使用opencv来解析图片中的线和矩形
                from ._vline import LineParser

                #h_lines = LineParser().get_h_lines(self.page,self.bbox,objects=[obj.bbox for cell in self.cells for obj in cell.objects])
                #h_lines.sort(key=lambda line:line.bbox.y0)
                h_lines=[]
                
            elif self.page.type==PageType.PDF:
                lines = self.bbox.adjust(dx=3,dy=3).get(self.page.lines)
                h_lines,v_lines = Line.split(lines)
                h_lines.sort(key=lambda line:line.bbox.y0)
                v_lines.sort(key=lambda line:line.bbox.x0)

                #获得矩形
                rects = self.bbox.adjust(dx=3,dy=3).get(self.page.rects)
                #按x0排序
                x_rects = sorted(rects,key=lambda rect:rect.bbox.x0)
                y_rects = sorted(rects,key=lambda rect:rect.bbox.y0)
            else:
                pass
            


        def get_y_by_lines(y1:float,y2:float)->float|None:
            for h_line in h_lines:
                if y1<=h_line.bbox.y0<=y2:
                    return h_line.bbox.y0
                
                if h_line.bbox.y0>y2:
                    #已经排序过
                    break
            return None
        
        def get_y_by_rects(y1:float,y2:float)->float|None:
            for rect in y_rects:
                if rect.bbox.width>8 and rect.bbox.height>8:
                    if y1<=rect.bbox.y1<=y2:
                        return rect.bbox.y1
                    
                    if y1<=rect.bbox.y0<=y2:
                        return rect.bbox.y0
            return None

        def get_y(y1:float,y2:float,mode:str='line')->float:
            #TODO 可以做得更完美
            #1.判断有y1和y2之间是否有水平线，如果有，使用水平线的y值
            #2.判断y1和y2之间是否有彩色矩形，如果有，取彩色矩形的边界线
            #都没有，取中间值
            if mode=='line':
                #计算中间行，先考虑线，再考虑矩形
                fns=[get_y_by_lines,get_y_by_rects]
            else:
                #计算表格上下边界，先考虑矩形，再考虑线？
                fns=[get_y_by_rects,get_y_by_lines]
            for fn in fns:
                y=fn(y1,y2)
                if y is not None:
                    return y
            #
            return (y1+y2)/2
        
        def get_x_by_lines(x1:float,x2:float)->float|None:
            for v_line in v_lines:
                if x1<=v_line.bbox.x0<=x2:
                    #如果有多条呢？
                    return v_line.bbox.x0
                
                if v_line.bbox.x0>x2:
                    #已经排序过
                    break
            return None
        
        def get_x_by_rects(x1:float,x2:float)->float|None:
            #对于特别的情况，就不考虑，如：
            #[cell][---rect--][cell] => 这种是特殊的，使用一个大矩形表示线
            #[---rect--cell--]
            for rect in x_rects:
                if rect.bbox.width>8 and rect.bbox.width>8:
                    if x1<=rect.bbox.x1<=x2:
                        return rect.bbox.x1
                    if x1<=rect.bbox.x0<=x2:
                        return rect.bbox.x0
            return None

        def get_x(x1:float,x2:float,mode:str='line')->float:
            #TODO 可以做得更完美
            #1.判断x1和x2之间是否有垂直线，如果有，使用垂直线的x值
            #2.判断x1和x2之间是否有彩色矩形，如：x1--|-x2--   =>取彩色矩形的边界
            if mode=='line':
                #计算中间行，先考虑线，再考虑矩形
                fns=[get_x_by_lines,get_x_by_rects]
            else:
                #计算表格上下边界，先考虑矩形，再考虑线？
                fns=[get_x_by_rects,get_x_by_lines]
            for fn in fns:
                x=fn(x1,x2)
                #print((x1,x2),x)
                if x is not None:
                    return x
            return (x1+x2)/2
        
        #下面是调整列
        def adjust_columns():
            def get_x_axis()->list[int]:
                axis:list[Any] = [None]*self.col_num
                for cell in self.cells:
                    i = cell.col_index
                    if axis[i] is None:
                        axis[i] = cell.bbox[0]
                axis.append(self.bbox[2])
                return axis

            x_axis = get_x_axis()
            for i in range(1,self.col_num):
                #       x1     x2
                #|[col1]|[col2]|
                col1:list[BBox]=[]
                col2:list[BBox]=[]
                for c in self.cells:
                    if c.content_bbox is None:
                        continue
                    if c.col_index+c.col_span==i:
                        col1.append(c.content_bbox)
                    elif c.col_index==i:
                        col2.append(c.content_bbox)
                    else:
                        pass
                if not col1 or not col2:
                    continue
                x0=x_axis[i-1]
                x1=x_axis[i]
                x2=x_axis[i+1]
                k1 = max(c[2] for c in col1)
                k2 = min(c[0] for c in col2)
                #print('=>>col',i,(x0,x1,x2),(k1,k2))
                #TODO 可以考虑允许误差k1-1<=x1<=k2+1 and k2-k1>=2
                #因为col2可以存在错位，如：
                #[xxxxxxxx][xxxxxx]
                #[xxx][        xxx] =>当i=1，也就是第一列的时候，k2>x2
                #[xxx][xxxxxxxxxxx] =>如果有了这一列，k2<x2
                #所以如下限制
                k2=min(k2,x2)
                if k1>x0 and k1<k2 and k1-1<=x1<=k2+1:
                    #|xxxxx|   |   |xxxxxxx|
                    #x0    k1  x1  k2      x2
                    #TODO
                    #k = (k1+k2)//2
                    
                    k = get_x(k1,k2)
                    for c in self.cells:
                        if c.col_index+c.col_span==i:
                            c.bbox = c.bbox.copy(x1=k)
                        elif c.col_index==i:
                            c.bbox = c.bbox.copy(x0=k)
                        else:
                            pass

        def adjust_rows():
            def get_y_axis()->list[int]:
                axis:list[Any] = [None]*self.row_num
                for cell in self.cells:
                    i = cell.row_index
                    if axis[i] is None:
                        axis[i] = cell.bbox[3]
                axis.append(self.bbox[1])
                return axis

            y_axis = get_y_axis()
            for i in range(1,self.row_num):
                #[row1]
                #[row2]
                row1:list[BBox]=[]
                row2:list[BBox]=[]
                dy=0

                #如果需要支持对齐更小的内容，可以设置为更小，如：5
                max_blank_height=5
                for c in self.cells:
                    #TODO 如果没有内容，构造一个？高度就默认为10或者其他？
                    b=c.content_bbox
                    #if b is None:
                        #continue
                    if c.row_index+c.row_span==i:
                        if b is None:
                            #支持空白的单元格
                            if c.bbox.height>=max_blank_height:
                                b = c.bbox.copy(y0=c.bbox.y0+(c.bbox.height-max_blank_height))
                            else:
                                b = c.bbox
                        else:
                            dy=max(dy,c.bbox.y0-b.y0)
                        row1.append(b)
                    elif c.row_index==i:
                        if b is None:
                            #支持空白的单元格
                            if c.bbox.height>=max_blank_height:
                                b=c.bbox.copy(y1=c.bbox.y1-(c.bbox.height-max_blank_height))
                            else:
                                b=c.bbox
                        row2.append(b)
                    else:
                        pass
                
                #TODO 如果前面没有清除空白行，这里就
                if not row1 or not row2:
                    continue

                #----y0----
                #   row1
                #----y1----
                #   row2
                #----y2----    
                y0=y_axis[i-1]
                y1=y_axis[i]
                y2=y_axis[i+1]
                k1 = min(c[1] for c in row1)
                k2 = max(c[3] for c in row2)

                #TODO 有些cell.bbox可能会小于cell.content_bbox
                #这里简单调整一点误差(y1-2)，忽略也可以，只是不那么精确的美观
                #也不想去调整其它地方计算的bbox
                if dy<0:
                    dy=0
                else:
                    #万一太大了？计算错误？
                    dy=min(2,math.ceil(dy))
                k2=max(k2,y2)
                if k1<y0 and k1>k2 and k2<=y1<=k1+dy:
                    #k = (k1+k2)//2
                    k = get_y(k2,k1)
                    for c in self.cells:
                        if c.row_index+c.row_span==i:
                            c.bbox = c.bbox.copy(y0=k)
                        elif c.row_index==i:
                            c.bbox = c.bbox.copy(y1=k)
                        else:
                            pass

        def adjust_table_bbox():
            #调整表格的4条边界
            #优先矩形，然后再到线
            #因为经常出现
            #------------- h_line
            #[rect]|[rect]|[rect]  => 使用矩形更加好一点
            bbox=self.bbox

            #top
            row=self.get_row(0)
            row_bbox = BBox.x_union([c.content_bbox for c in row],strict=False)
            if row_bbox is not None and bbox.y1>row_bbox.y1:
                #矩形优先
                bbox=bbox.copy(y1=get_y(row_bbox.y1,bbox.y1,mode='rect')).large
                for cell in row:
                    cell.bbox = cell.bbox.copy(y1=bbox.y1)
                
            #bottom
            row = self.get_row(-1)
            row_bbox = BBox.x_union([c.content_bbox for c in row],strict=False)
            if row_bbox is not None and bbox.y0<row_bbox.y0:
                #矩形优先
                bbox=bbox.copy(y0=get_y(bbox.y0,row_bbox.y0,mode='rect')).large
                for cell in row:
                    cell.bbox = cell.bbox.copy(y0=bbox.y0)
            
            #left
            col = self.get_column(0)
            col_bbox = BBox.x_union([c.content_bbox for c in col],strict=False)
            if col_bbox is not None and bbox.x0<col_bbox.x0:
                bbox=bbox.copy(x0=get_x(bbox.x0,col_bbox.x0,mode='rect')).large
                for cell in col:
                    cell.bbox = cell.bbox.copy(x0=bbox.x0)
            

            #right
            col = self.get_column(-1)
            col_bbox = BBox.x_union([c.content_bbox for c in col],strict=False)
            if col_bbox is not None and col_bbox.x1<bbox.x1:
                bbox=bbox.copy(x1=get_x(col_bbox.x1,bbox.x1,mode='rect')).large
                for cell in col:
                    cell.bbox = cell.bbox.copy(x1=bbox.x1)


            self.bbox = bbox

        
        
        if clean:
            self.clean()
        
        timer.mark('start adjust')
        adjust_columns()
        adjust_rows()
        adjust_table_bbox()
        timer.mark('end adjust')
        
        timer.mark('start rebuild')
        self.rebuild()
        timer.mark('end rebuild')

        if debugger.allow('info'):
            debugger.print(f'adjust elapsed={timer.elapsed(start='start adjust',end='end adjust'):.3f}')
            debugger.print(f'rebuild elapsed={timer.elapsed(start='start rebuild',end='end rebuild'):.3f}')
        
        if debugger.allow('gui'):
            self.show('beautify')

    def add_blank_row(self,row_index:int,y:float,*,position:Literal['above','below']='below'):
        """指定的行分成2行，一行为原始内容，一行为空白"""
        blank_cells:list[WCell]=[]
        row = self.get_row(row_index)
        for cell in row:
            if cell.row_span!=1:
                continue
            blank_bbox = cell.bbox
            i:int 
            if position=='above':
                blank_bbox = cell.bbox.copy(y0=y)
                cell.bbox = cell.bbox.copy(y1=y)
                i=row_index
            else:
                blank_bbox = cell.bbox.copy(y1=y)
                cell.bbox = cell.bbox.copy(y0=y)
                i=row_index+1
            blank_cell = WCell(blank_bbox)
            blank_cell.col_index = cell.col_index
            blank_cell.col_span = cell.col_span
            blank_cell.row_index=i
            blank_cells.append(blank_cell)
        
        for cell in self.cells:
            if position=='above':
                if cell.row_index<=row_index<cell.row_index+cell.row_span:
                    if cell.row_span==1:
                        cell.row_index+=1
                    else:
                        cell.row_span+=1
                elif cell.row_index>=row_index:
                    cell.row_index+=1
                
            elif position=='below':
                if cell.row_index<=row_index<cell.row_index+cell.row_span:
                    if cell.row_span==1:
                        pass
                    else:
                        cell.row_span+=1
                elif cell.row_index>row_index:
                    cell.row_index+=1
                else:
                    pass
            else:
                pass
        
        
        cells:list[WCell]=[]
        cells.extend(self.cells)
        cells.extend(blank_cells)
        self._sort_cells(cells)
        self.cells = tuple(cells)

        self.row_num+=1
        self.rebuild_grid()
        
        

    def _clean_blank_rows(self):
        """"清除空白的行"""
        debugger = self._debugger.bind(page=self.pages[0].number)

        def is_blank(row:Sequence[WCell])->bool:
            for cell in row:
                if len(cell.objects)>0 and cell.row_index==i and cell.row_span==1:
                    #比较cell.row_span==1就足够了，如果是跨列的，忽略
                    #表示该单元格不为空
                    return False
            return True
        
        used_cells:list[WCell]=[]
        for i in range(1,self.row_num):
            row = self.get_row(i)
            #表示该行不为空
            if not is_blank(row):
                continue
            above_row:list[WCell]=[]
            for cell in self.get_row(i-1):
                if cell.row_index+cell.row_span==i:
                    above_row.append(cell)
            
            if not above_row:
                continue
            #先判断，全部满足的才出来
            ok_cell:list[WCell]=[]
            for cell in above_row:
                if self.can_merge(cell.row_index,i+1,cell.col_index,cell.col_index+cell.col_span) and is_blank(self[i:i+1,cell.col_index:cell.col_index+cell.col_span]):
                    #[xxxxxxx]
                    #[-blank-]
                    #或者
                    #[xxxxxxxxxxxxxxxxx]
                    #[-blank-]|[-blank-]
                    #不支持(需要先切开为n个)
                    #[xxxx]|[xxxx]
                    #[--blank----]
                    ok_cell.append(cell)
            
            #全部满足再处理
            if len(ok_cell)==len(above_row):
                for cell in ok_cell:
                    used_cells.append(cell)
                    self.merge_cells(cell.row_index,i+1,cell.col_index,cell.col_index+cell.col_span)
        
        if used_cells:
            if debugger.allow('gui'):
                self.show('before remove blank rows',bboxes=[c.bbox for c in used_cells])
            
            self.rebuild()
            
            if debugger.allow('gui'):
                self.show('after remove blank rows')


    def _clean_blank_columns(self):
        """清除空白的列"""
        pass

    def clean(self,*,rows:bool=True,columns:bool=True):
        if rows:
            self._clean_blank_rows()
        if columns:
            self._clean_blank_columns()


    def is_column_align(self,cells:Sequence[WCell])->bool:
        """判断是否单元格列对齐"""
        for i in range(1,len(cells)):
            c1 = cells[i-1]
            c2 = cells[i]
            if not (c1.col_index==c2.col_index and c1.col_span==c2.col_span):
                return False
        return True 

    
    def is_row_align(self,cells:Sequence[WCell])->bool:
        """判断是否单元格行对齐"""
        for i in range(1,len(cells)):
            c1 = cells[i-1]
            c2 = cells[i]
            if not (c1.row_index==c2.row_index and c1.row_span==c2.row_span):
                return False
        return True 
    
    def to_table(self)->Table:
        """转换为所在页的表格"""

        #避免调用错误，必须为非跨页/跨栏表格
        assert self.page is not None
        from ._base import Cell, Table
        cells:list[Cell]=[]
        for c1 in self.cells:
            objs=[obj.object for obj in c1.objects]
            c2 = Cell(self.page,c1.bbox,row_index=c1.row_index,col_index=c1.col_index,row_span=c1.row_span,col_span=c1.col_span,objects=objs)
            cells.append(c2)
        return Table(self.page,self.bbox,row_num=self.row_num,col_num=self.col_num,cells=cells,subtype=TableType.WBK)
     
    def to_xtable(self)->XTable:
        """转换为跨页/跨栏表格对象"""
        from ._xbase import XCell, XTable
        raise RuntimeError(f'未实现')


    def show(self, title:str,*,bboxes:Sequence[Any]|None=None,stack_level:int=0):
        """
        显示解析的线
        """
        #TODO 也可以直接使用cv2
        if self.page is not None:
            #表示为单页表格（没有跨栏/跨页）
            pil_image = self.page.pil_image
            scale = pil_image.width/self.page.width
        else:
            assert self.image is not None
            pil_image = self.image
            scale=1
        
        pil_image = pil_image.copy()
        m = M(1,0,0,1,0,0).scale(scale,-scale).translate((0,pil_image.height)).to_tuple()
        draw = PIL.ImageDraw.Draw(pil_image)
        for line in [*self.h_lines,*self.v_lines]:
            line = B.transform(line,m)
            draw.line(line,fill=(0,0,255),width=4)
        
        if bboxes:
            for bbox in bboxes:
                bbox = B.bbox(bbox)
                bbox = B.transform(bbox,m)
                draw.rectangle(bbox,outline=(255,255,0),width=4)
        #rgb to bgr
        cv2_img = cv2.cvtColor(np.array(pil_image),cv2.COLOR_RGB2BGR)

        #为了方便定位，在标题显示文件名等
        frame = utils.getframe(stack_level+1)
        tb = inspect.getframeinfo(frame)
        title=f'{title}[{os.path.basename(tb.filename)}:{tb.function}:{tb.lineno}]'
        cv2.imshow(title,cv2_img)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    

    def validate(self,raise_errors:bool=True,verbose:bool=False)->bool:
        """检查表格是否正确，如果错误，抛出异常"""
        row_num = self.row_num
        col_num = self.col_num
        x_axis:list[int]=[]
        y_axis:list[int]=[]
        for cell in self.cells:
            if cell.col_index not in x_axis:
                x_axis.append(cell.col_index)
            if cell.row_index not in y_axis:
                y_axis.append(cell.row_index)
            
            if cell.row_index+cell.row_span>row_num:
                raise RuntimeError(f'表格结构错误，单元格溢出了行数,row_num={row_num},cell={(cell.row_index,cell.row_span)}')
            if cell.col_index+cell.col_span>col_num:
                raise RuntimeError(f'表格结构错误，单元格溢出了列数,col_num={col_num},cell={(cell.col_index,cell.col_span)}')
            if cell.bbox.x0>=cell.bbox.x1 or cell.bbox.y0>=cell.bbox.y1:
                raise RuntimeError(f'表格结构错误，单元格的bbox不正确,row={cell.row_index},col={cell.col_index},bbox={cell.bbox},text={cell.text}')
        for i in range(self.col_num):
            row = self.get_column(i,strict=True)
            row = [c for c in row if c.col_span==1]
            if not row:
                continue
            y0 = set([c.bbox.x0 for c in row])
            y1 = set([c.bbox.x1 for c in row])
            if len(y0)!=1:
                raise RuntimeError(f'col_index={i},x0={y0}')
            if len(y1)!=1:
                print(i,[c.bbox for c in row])
                raise RuntimeError(f'col_index={i},x1={y1}')
            
            if i+1<self.col_num:
                column2 = self.get_column(i+1,strict=True)
                next_x0 = set([c.bbox.x0 for c in column2])
                if next_x0!=y1:
                    raise RuntimeError(f'next_x={next_x0}')

        for i in range(self.row_num):
            row = self.get_row(i,strict=True)
            row = [c for c in row if c.row_span==1]
            if not row:
                continue
            y0 = set([c.bbox.y0 for c in row])
            y1 = set([c.bbox.y1 for c in row])
            if len(y0)!=1:
                raise RuntimeError(f'row_index={i},y0={y0}')
            if len(y1)!=1:
                print(i,[c.bbox for c in row])
                raise RuntimeError(f'row_index={i},y1={y1}')
            
            if i+1<self.row_num:
                column2 = self.get_row(i+1,strict=True)
                next_y1 = set([c.bbox.y1 for c in column2])
                if next_y1!=y0:
                    raise RuntimeError(f'next_y={next_y1}')
                
        x_axis.sort()
        y_axis.sort()
        if verbose:
            print(f'col_num={col_num},x_axis={x_axis}')
            print(f'row_num={row_num},y_axis={y_axis}')
            
        if x_axis!=list(range(col_num)):
            if raise_errors:
                raise RuntimeError(f'表格结构计算错误，col_num={col_num},x_axis={x_axis}')
            return False
        if y_axis!=list(range(row_num)):
            if raise_errors:
                raise RuntimeError(f'表格结构计算错误，row_num={row_num},y_axis={y_axis}')
            return False
        return True

class TableSketch:
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger=XDebugger(f'{__module__}.{__qualname__}')
    def __init__(self):
        super().__init__()
    
    def draw(self,table:WTable):
        self.table=table
        debugger = self._debugger.bind(page=table.pages[0].number)
        cells = table.cells
        # 添加矩形的4条边
        bbox = table.bbox.large.ensure()
        table.h_lines.append(bbox.top.to_list())
        table.h_lines.append(bbox.bottom.to_list())
        table.v_lines.append(bbox.left.to_list())
        table.v_lines.append(bbox.right.to_list())
        #True计算量大
        use_cells=False
        if use_cells:
            self._adjust_cells(cells)
            x0, y0, x1, y1 = table.bbox.large.ensure()
            for cell in cells:
                b = cell.bbox
                table.h_lines.append([x0, b[3], x1, b[3]])
                table.h_lines.append([x0, b[1], x1, b[1]])
                table.v_lines.append([b[0], y0, b[0], y1])
                table.v_lines.append([b[2], y0, b[2], y1])
        else:
            
            self._adjust_cells(cells)
            #排序了，从上到下
            rows = self._split_rows(cells)
            if debugger.allow('info'):
                with debugger.group('rows'):
                    for row in rows:
                        debugger.console.print(row.bbox,len(row),[ (c.text,c.bbox) for c in row])
            #排序了，从右到左
            cols = self._split_cols(cells)
            x0, y0, x1, y1 = table.bbox.large.ensure()
            for row in rows:
                b = row.bbox.large
                table.h_lines.append([x0, b[3], x1, b[3]])
                table.h_lines.append([x0, b[1], x1, b[1]])
                #for cell in row:
                    #cell.row_bbox=cell.bbox.copy(y0=min(b.y0,cell.bbox.y0),y1=max(b.y1,cell.bbox.y1))
            
            for col in cols:
                b = col.bbox.large
                table.v_lines.append([b[0], y0, b[0], y1])
                table.v_lines.append([b[2], y0, b[2], y1])
                #for cell in col:
                    #cell.col_bbox = cell.bbox.copy(x0=min(b.x0,cell.bbox.x0),x1=max(b.x1,cell.bbox.x1))

        
        if debugger.allow('gui'):
            table.show('groups')

        self._erase_cell_lines(table,use_cells=use_cells)

        lists.distinct(table.v_lines)
        lists.distinct(table.h_lines)



        if debugger.allow('gui'):
            table.show('sketch')

    def _adjust_cells(self,cells:Sequence[WCell]):
        """避免cell有重叠，因为后续的计算不允许有重叠"""
        #cells = self.cells
        debugger=self._debugger.bind(page=self.table.pages[0].number)
        cells = sorted(cells,key=lambda cell:cell.bbox.y1,reverse=True)
        records:list[tuple[str,WCell,WCell,BBox,BBox]]=[]
        for i in range(len(cells)):
            c1 = cells[i]
            for j in range(i+1,len(cells)):
                c2 = cells[j]
                #不允许刚好重叠的，就设置为
                dy=c2.bbox.y1-c1.bbox.y0
                #如果dy==0，也就是c1,c2粘连在一起，在这种情况，也需要调整上下，否则无法画线？
                #[c1]
                #[c2]
                if dy<=0:
                    #不需要再继续了，也可以使用"<"
                    break

                #因为字符书写的方式是从左到右，所以，容易出现重叠的地方，c1的末尾和c2的开头，因为字符串粘连在一起，而且在空间不够，
                #可能c2会覆盖了c1的个别字符
                #[---c1--][--c2---]
                #相交区域
                xb = c1.bbox.intersect([c2.bbox])
                if xb is None or xb.area==0:
                    continue
                a1=('x',c1,xb.width/c1.bbox.width)
                a2=('y',c1,xb.height/c1.bbox.height)
                b1=('x',c2,xb.width/c2.bbox.width)
                b2=('y',c2,xb.height/c2.bbox.height)
                a = min((a1,a2),key=lambda a:a[2])
                b = min((b1,b2),key=lambda b:b[2])
                c = min((a,b),key=lambda c:c[2])

                records.append((c[0],c1,c2,c1.bbox,c2.bbox))
                if c[0]=='x':
                    #表示调整宽度
                    #[--c1--]
                    #    [---c2---]
                    if c1.bbox.x0<=c2.bbox.x0:
                        x=(c1.bbox.x1+c2.bbox.x0)//2
                        c1.bbox=c1.bbox.copy(x1=x)
                        c2.bbox=c2.bbox.copy(x0=x)
                    else:
                    #或者
                    #     [---c1---]
                    #[---c2---]
                        x=(c1.bbox.x0+c2.bbox.x1)//2
                        c1.bbox=c1.bbox.copy(x0=x)
                        c2.bbox = c2.bbox.copy(x1=x)
                else:
                    #表示调整高度，已经知道
                    #[---c1---]
                    #    [---c2---]
                    y=(c2.bbox.y1+c1.bbox.y0)//2
                    c1.bbox = c1.bbox.copy(y0=y)
                    c2.bbox = c2.bbox.copy(y1=y)

                #不能够break，可能还和其他的重叠
        
        if debugger.allow('info'):
            with debugger.group('adjusted cells'):
                for axis,c1,c2,b1,b2 in records:
                    debugger.console.print((axis,c1.text[0:20],c2.text[0:20],b1.data,c1.bbox.data,b2.data,c2.bbox.data))
        

        align_records:list[tuple[bool,WCell,BBox,float,float]]=[]
        def align(cells:Sequence[WCell],is_row:bool,d:float=1):
            """垂直或者水平对齐"""
            #[------]
            #[-------]  =调整和上面的对齐
            if is_row:
                x0,x1,y0,y1=0,2,1,3
            else:
                x0,x1,y0,y1=1,3,0,2

            cells = sorted(cells,key=lambda cell:cell.bbox[y1],reverse=True)
            while cells:
                cell = cells[0]
                group:list[WCell]=[cell]
                for cell2 in cells[1:]:
                    if cell.bbox[y1]-cell2.bbox[y1]<=d:
                        group.append(cell2)
                    else:
                        break
                
                if len(group)>1:
                    max_value=max(c.bbox[y1] for c in group)
                    min_value=min(c.bbox[y1] for c in group)
                    if max_value!=min_value:
                        #没有误差就不需要调整了，不判断也可以的，只是又构造一个bbox
                        for cell2 in group:
                            if cell2.bbox[y1]!=min_value:
                                align_records.append((is_row,cell2,cell2.bbox,max_value,min_value))
                                if is_row:
                                    cell2.bbox=cell2.bbox.copy(y1=min_value)
                                else:
                                    cell2.bbox=cell2.bbox.copy(x1=min_value)
                                
                
                #为了避免误差累积，已经调整过的就不再调整了
                lists.remove(cells,group)
        
        align(cells,True)
        align(cells,False)

        if debugger.allow('info'):
            with debugger.group('align cells'):
                for a,cell,old_bbox,max_value,min_value in align_records:
                    debugger.console.print((a,cell.text[0:20],cell.bbox.data,old_bbox.data,max_value,min_value))
        
        #self._adjust_overflow(cells,False)
        self._adjust_overflow(cells,True)
        

        
    def _adjust_overflow(self,cells:Sequence[WCell],is_row:bool,d:float=2):
        """调整一点溢出，避免出现复杂的跨行跨列"""

        #先按列思考
        #[--cell3---][--cell2--]
        #[--cell1------] =>溢出了一点

        #不调整也没有错
        #调整会好一点，但是也会把该跨行跨列的去掉了，所以，如果需要跨行跨列，就需要重叠的区域大一些
        debugger=self._debugger.bind(page=self.table.pages[0].number)
        adjusted_cells:list[WCell]=[]
        

        def find_cell2(cell1:WCell,cells:Sequence[WCell],d:float=2)->WCell|None:
            if is_row:
                x0,x1=1,3
            else:
                x0,x1=0,2
            for cell2 in cells:
                if cell1 is cell2:
                    continue
                #            [---cell2---]
                #[---cell1-----]
                if 0<cell1.bbox[x1]-cell2.bbox[x0]<=d:
                    return cell2
                if cell2.bbox[x0]>=cell1.bbox[x1]:
                    #排序过了，不需要再继续
                    return None
            return None
        
        def find_cell3(cell1:WCell,cell2:WCell,cells:Sequence[WCell],d:float=2)->WCell|None:
            if is_row:
                x0,x1=1,3
            else:
                x0,x1=0,2
            for cell3 in cells:
                if cell3 in [cell1,cell2]:
                    continue

                # [---cell3---][---cell2---]
                # [-----cell1----]
                if cell3.bbox[x1]-cell1.bbox[x0]>d:
                    return cell3

            return None


        if is_row:
            #表示调整的是行，也就是y1
            x0,x1,y0,y1=1,3,0,2
        else:
            #表示调整的是列，也就是x1
            x0,x1,y0,y1=0,2,1,3
        
        cells = sorted(cells,key=lambda cell:cell.bbox[x0])

        for cell1 in cells:
            if cell1.bbox[x1]-cell1.bbox[x0]<=5:
                #太小的就不用调整
                continue

            cell2 = find_cell2(cell1,cells,d=d)
            if cell2 is None:
                continue

            cell3 = find_cell3(cell1,cell2,cells)
            if cell3 is None:
                continue

            if is_row:
                #按行，从上到下，调整y1
                cell1.bbox=cell1.bbox.copy(y1=cell2.bbox.y0)
            else:
                #按列，从左到右，调整x1
                cell1.bbox=cell1.bbox.copy(x1=cell2.bbox.x0)
            adjusted_cells.append(cell1)
        
        if debugger.allow('gui'):
            self.table.show(f'adjust overflow cells,row={is_row}',bboxes=adjusted_cells)
        

    def _split_rows(self,cells:Sequence[WCell]):
        return self._split_groups(cells,True)
        
    def _split_cols(self,cells:Sequence[WCell]):
        return self._split_groups(cells,False)

    def _split_groups(self,objects:Sequence[WCell],by_row:bool=True)->list[Group[WCell]]:
        #从上到下，从左到右排序

        def get_group(ref_obj:WCell,objects:Sequence[WCell],by_row:bool,d:float=1)->list[WCell]:
            if by_row:
                axis='y'
            else:
                axis='x'
            group:list[WCell]=[]
            for obj in objects:
                if ref_obj is obj:
                    continue
                if ref_obj.bbox.over(axis,obj.bbox,d=d):
                    group.append(obj)
            return group
        
        def is_valid(objects:list[WCell],by_row:bool)->bool:
            """判断ref_obj是否为有效的，也就是没有跨>=2个行/列"""
            if by_row:
                x0,x1,y0,y1=0,2,1,3
            else:
                x0,x1,y0,y1=1,3,0,2
            
            objects.sort(key=lambda obj:obj.bbox[y1],reverse=True)
            for obj1 in objects:
                for obj2 in objects:
                    if obj1 is obj2:
                        continue
                    #如果按行的，obj2是否在obj1下
                    #[--obj1--]
                    #[--obj2--]
                    if obj2.bbox[y1]<=obj1.bbox[y0]:
                        return False
            return True
        
        if by_row:
            #从上到下排序
            objects = sorted(objects,key=lambda obj:B.bbox(obj)[3],reverse=True)
            axis='y'
        else:
            #从右到左排序
            objects = sorted(objects,key=lambda obj:B.bbox(obj)[2],reverse=True)
            axis='x'
        
        groups:list[Group[WCell]]=[]
        #先去掉跨行/跨列的
        invalid_objects:list[WCell]=[]
        for ref_obj in objects:
            group:list[WCell]=get_group(ref_obj,objects,by_row)
            if not is_valid(group,by_row):
                invalid_objects.append(ref_obj)
        
        #去掉无效的对象
        lists.remove(objects,invalid_objects)
        while objects:
            group = self._split_group(objects,by_row=by_row)
            groups.append(group)
        
        #TODO 这里也可以递归下去
        #True和False都可以
        use_recursive=True
        #为了避免无限循环，如所有的对象都是无效的，就不需要递归了
        if len(groups)==0:
            use_recursive=False
            
        if len(invalid_objects)>0:
            if use_recursive:
                invalid_groups = self._split_groups(invalid_objects,by_row=by_row)
                groups.extend(invalid_groups)
            else:
                for obj in invalid_objects:
                    groups.append(Group([obj]))

        if by_row:
            groups.sort(key=lambda g:g.bbox[3],reverse=True)
        else:
            groups.sort(key=lambda g:g.bbox[2],reverse=True)
        return groups
    def _split_groups2(self,objects:Iterable[WCell],by_row:bool=True)->list[Group[WCell]]:
        #从上到下，从左到右排序
        if by_row:
            #从上到下排序
            objects = sorted(objects,key=lambda obj:B.bbox(obj)[3],reverse=True)
        else:
            #从右到左排序
            objects = sorted(objects,key=lambda obj:B.bbox(obj)[2],reverse=True)
        groups:list[Group[WCell]]=[]

        while objects:
            group = self._split_group(objects,by_row=by_row)
            groups.append(group)

            
            
        def adjust_groups(groups:list[Group[WCell]]):
            if by_row:
                #[g1]
                #[g2]
                x0,y0,x1,y1=0,1,2,3
            else:
                #[g2][g1]
                x0,y0,x1,y1=1,0,3,2
            
            d=0
            overlapped_cells:list[WCell]=[]
            i=0
            while i<len(groups)-1:
                g1 = groups[i]
                g2 = groups[i+1]

                if len(g1)<=1 or g2.bbox[y1]-g1.bbox[y0]<=d:
                    i+=1
                    continue
                #g1和g2有重叠的部分
                #[g1]
                #[g2]

                #这一步不做也可以，只是做了可以提高一点点速度
                if len(g2)==1:
                    break
                elif len(g2)>1:
                    g2.sort(key=lambda cell:cell.bbox[y1],reverse=True)
                    b2 = BBox.x_union(g2[1:])
                    assert b2 is not None
                    if b2[y1]-g1.bbox[y0]<=d:
                        overlapped_cells.append(g2[0])
                        del g2[0]
                        g2.invalidate()
                        break
                
                g1.sort(key=lambda cell:cell.bbox[y0],reverse=False)
                while len(g1)>1:
                    overlapped_cells.append(g1[0])
                    del g1[0]
                    g1.invalidate()
                    if g2.bbox[y1]-g1.bbox[y0]<=d:
                        break
                
            
            for cell in overlapped_cells:
                group = Group[WCell]()
                group.append(cell)
                groups.append(group)
                

        
        #adjust_groups(groups)

        for group in groups:
            #可以调整，但是不能够和相邻的单元格重叠
            #adjust_group(group,by_row)
            pass
        if by_row:
            groups.sort(key=lambda g:g.bbox[3],reverse=True)
        else:
            groups.sort(key=lambda g:g.bbox[2],reverse=True)
        return groups
    
    def _split_group(self,objects:list[Any],by_row:bool)->Group[Any]:

        def has_above_object(group:Group[Any],obj:Any,by_row:bool)->bool:
            if by_row:
                x0,y0,x1,y1=0,1,2,3
                axis='x'
            else:
                x0,y0,x1,y1=1,0,3,2
                axis='y'

            b1 = B.bbox(obj) #obj.bbox
            for obj2 in group:
                #按行的时候
                #[xxx][b2] =>
                #[xxx][b1] => 这个不允许
                #按列的时候
                #[xxxxx]
                #[b1][b2]  => b1不允许
                b2 = B.bbox(obj2)#obj2.bbox
                #if b1.over('x',b2,d=4) and b1.y1<=b2.y0:
                if  B.over(axis,b1,b2,d=2) and b2[y0]-b1[y1]>=-4 and b1[y0]<b2[y0]:
                    return True
            return False
        
        def join(i:int,objects:list[Any],group:Group[Any],by_row:bool)->bool:
            if by_row:
                x0,y0,x1,y1=0,1,2,3
                axis='y'

                #如果是底部对齐
                #如果是顶部对齐
                #如果是居中对齐
            else:
                x0,y0,x1,y1=1,0,3,2
                axis='x'
            b1 = group.bbox
            #b2 = objects[i].bbox
            b2 = B.bbox(objects[i])
            #TODO 如果太小了？
            #d=min(b1[3]-b1[1],b2[3]-b2[1])/2
            #d=min(b1[y1]-b1[y0]-1,(b2[y1]-b2[y0])/2)
            d=min(b1[y1]-b1[y0]-2,b2[y1]-b2[y0]-2,4)
            #误差认为1即可？
            #d=1
            if b1.over(axis,b2,d=d) and not has_above_object(group,objects[i],by_row=by_row):
                #[b1][b2] => 在同一行，且没有重叠的，如：
                #[obj1][obj2]
                #[obj3]  => 没有在任何其他对象下
                group.append(objects[i])
                group.invalidate()
                return True
            else:
                return False


        
        if by_row:
            x0,y0,x1,y1=0,1,2,3
        else:
            x0,y0,x1,y1=1,0,3,2

        group:Group[Any]=Group()

        #取第一个且删除
        group.append(objects.pop(0))
        group.invalidate()

        i=0
        while i<len(objects):
            obj2 = objects[i]
            b1 = group.bbox
            b2 = B.bbox(obj2)
            if join(i,objects,group,by_row):
                del objects[i]
            elif b2[y1]<=b1[y0]:
                #这样再判断一下是多执行几次，避免“d”太大的时候，跳过了一些很小的
                #因为已经根据y1排序，如果没有重叠的，就可以跳过了
                break
            else:
                i+=1
        
        if by_row:
            group.sort(key=lambda obj:B.bbox(obj)[0])
        else:
            group.sort(key=lambda obj:B.bbox(obj)[3],reverse=True)

        return group

    
    def _erase_cell_lines(self, table:WTable,*,use_cells:bool=True):
        """
        穿过cell的线调整为2条，如：----->[cell]------>
        """
        def handle(is_h:bool):
            if is_h:
                lines = table.h_lines
                x0,y0,x1,y1=0,1,2,3
            else:
                lines = table.v_lines
                x0,y0,x1,y1=1,0,3,2
            
            #
            lines.sort(key=lambda line:line[y1],reverse=True)
            for cell in table.cells:
                bbox = cell.bbox
                i=0
                while i<len(lines):
                    line = lines[i]
                    if line[y1]>=bbox[y1]:
                        i+=1
                        continue

                    if line[y1]<=bbox[y0]:
                        break

                    if (use_cells and line[x0]<=bbox[x0] and line[x1]>=bbox[x1] ) or (not use_cells and line[x1]>=bbox[x0] and line[x0]<=bbox[x1]):
                        #如果不是cell的bbox，只需要部分就可以
                        #如果是cell的，必须穿过（实际上也不必）
                        line1=line[:]
                        line2=line[:]
                        line1[x1]=bbox[x0]
                        line2[x0]=bbox[x1]
                        del lines[i]
                        if line1[x1]-line1[x0]>0:
                            lines.insert(i,line1)
                            i+=1
                        if line2[x1]-line2[x0]>0:
                            lines.insert(i,line2)
                            i+=1
                    else:
                        i+=1

        handle(True)
        handle(False)

        if not use_cells:
            self._align_lines(table)

    def _align_lines1(self,table:WTable,is_h:bool,is_lower:bool):
        debugger = self._debugger.bind(page=table.pages[0].number)
        if is_h:
            #调整水平线
            x0,y0,x1,y1=0,1,2,3
            h_lines = table.h_lines
            v_lines = table.v_lines
        else:
            #调整垂直线
            x0,y0,x1,y1=1,0,3,2
            h_lines = table.v_lines
            v_lines = table.h_lines
        
        if is_lower:
            v_lines=sorted(v_lines,key=lambda line:line[x0])
        else:
            v_lines=sorted(v_lines,key=lambda line:line[x0],reverse=True)
        

        for line1 in h_lines:
            connected_lines:list[_Line]=[]
            cross_lines:list[_Line]=[]
            for line2 in v_lines:
                #这一步不执行也不影响，只是为了加快速度
                if is_lower:
                    if line2[x0]>=line1[x1]:
                        #---|||
                        break
                else:
                    if line2[x0]<=line1[x0]:
                        #|||---
                        break

                if line2[y0]<=line1[y0]<=line2[y1]:
                    if (is_lower and line1[x0]==line2[x0]) or (not is_lower and line1[x1]==line2[x0]):
                        #|---- or ----|
                        connected_lines.append(line2)
                    elif line1[x0]<line2[x0] and line1[x1]>line2[x0]:
                        #--|--
                        cross_lines.append(line2)
                    else:
                        pass

                    if len(connected_lines)>0 or len(cross_lines)>0:
                        break
            if len(connected_lines)==0 and len(cross_lines)>0:
                #排序过的
                line2 = cross_lines[0]
                if debugger.allow('info'):
                    debugger.print(f'align line,is_h={is_h},is_lower={is_lower},line1={line1},line2={line2}')
                if is_lower:
                    line1[x0]=line2[x0]
                else:
                    line1[x1]=line2[x0]

    def _align_lines(self,table:WTable):
        #调整水平线的x0
        self._align_lines1(table,True,True)
        #对齐水平线的x1
        self._align_lines1(table,True,False)
        #对齐垂直线的y0
        self._align_lines1(table,False,True)
        #对齐垂直线的y1
        self._align_lines1(table,False,False)

                


class LineCleaner:
    """
    合并水平线，垂直线等
    """
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger=XDebugger(f'{__module__}.{__qualname__}')
    def __init__(self):
        pass

    def clean(self, table:WTable):
        """
        整理线
        """
        debugger=self._debugger.bind(page=table.pages[0].number)
        timer = utils.Timer.start()
        old_total=len(table.v_lines)+len(table.h_lines)
        if len(table.v_lines) < len(table.h_lines):
            # 先处理少的，这样速度更快
            self._clean_lines(table,False)
            self._clean_lines(table,True)
        else:
            self._clean_lines(table,True)
            self._clean_lines(table,False)

        #TODO 暂时使用这个清除冒头的线
        if True:
            from .line import Liner
            lines = Liner().parse([*table.v_lines,*table.h_lines])
            table.h_lines,table.v_lines = self._split_lines([list(line) for line in lines])

        table.h_lines.sort(key=lambda line: line[3])
        table.v_lines.sort(key=lambda line: line[0])
    
        if debugger.allow('info'):
            new_total=len(table.v_lines)+len(table.h_lines)
            debugger.print(f'clean elapsed_times={timer.elapsed():.3f},from={old_total},to={new_total}')

    def _clean_lines(self, table:WTable, is_h:bool):
        """
        合并垂直线/水平线，只要2条线之间没有cell，就可以合并
        """
        while True:
            #从上到下
            if not self._clean_once(table, is_h,True):
                break
        while True:
            #从下到上
            if not self._clean_once(table, is_h,False):
                break

    def _clean_once(self, table:WTable, is_h:bool, lower2upper:bool): 
        """
        执行一次清除
        table: 
        is_h: True表示水平线
        lower2upper: True表示从低到高，False表示从高到低
        """

        debugger = self._debugger.bind(page=table.pages[0].number)

        timer = utils.Timer.start()
        #cells = table.cells
        h_lines = table.h_lines
        v_lines = table.v_lines
        if is_h:
            x0,x1,y0,y1=0,2,1,3
            lines = h_lines
        else:
            x0,x1,y0,y1=1,3,0,2
            lines = v_lines
        
        old_total = len(lines)

        cells:Final = sorted(table.cells,key=lambda cell:cell.bbox.y1,reverse=True)
        def is_blank(bbox:BBox)->bool:
            if bbox.width==0 or bbox.height==0:
                return True
            for cell in cells:
                if bbox.y0>cell.bbox.y1:
                    break
                if bbox.y1<cell.bbox.y0:
                    continue
                #if bbox.intersects([cell.min_bbox]):
                    #return False
                xb = bbox.intersect([cell.min_bbox])
                if xb is not None and xb.area>0:
                    return False
            return True

        def get_bbox(line1:_Line, line2:_Line)->BBox|None:
            #---------line1
            #   -----------line2
            #需要判断line1是否可以合并到line2，所以计算line1的区域
            #b = line1.bbox[:]
            if line2[x0]>=line1[x1] or line2[x1]<=line1[x0]:
                return None
            b = line1[:]
            #b[y0] = min(line1[y0], line2[y0])
            #b[y1] = max(line1[y0], line2[y0])
            if lower2upper:
                #--line2--
                #--line1--
                b[y1]=line2[y0]
            else:
                #--line1--
                #--line2--
                b[y0]=line2[y0]
            
            strict=False
            if not strict:
                return BBox(b)
            else:
                dx0=0
                dx1=0
                dy0=0
                dy1=0
                if b[2]-b[0]>2:
                    dx0=1
                    dx1=-1
                else:
                    dx1=-1
                
                if b[3]-b[1]>2:
                    dy0=1
                    dy1=-1
                else:
                    dy1=-1
                
                return BBox(b).adjust(dx0=dx0,dx1=dx1,dy0=dy0,dy1=dy1)
        
        self._remove_lines(table,lines)

        lines.sort(key=lambda line:line[y0],reverse= not lower2upper)

        
        state={
            'change_count':0,
            'loop_count':0,
            #清除的交叉数量
            'cross_count':0,
            'changed':False
        }
        
        def do_merge(i:int,lines:Sequence[_Line]):
            line1 = lines[i]
            for line2 in lines[i+1:]:
                state['loop_count']+=1
                bbox = get_bbox(line1,line2)
                #print('===>getbbox',line1,line2,bbox,is_blank(bbox) if bbox is not None else None)
                if bbox is None:
                    continue
                if is_blank(bbox):
                    result = self._merge_line(h_lines,v_lines,line1,line2,is_h)
                    if result['changed']:
                        state['change_count']+=1
                        state['changed']=True
                    #表示需要把line1删除
                    if result['removed']:
                        return True
                    #如果不需要删除，可以继续？
                    #return result['removed']
                else:
                    break
            return False

        lists.remove2(lines,do_merge)

        if debugger.allow('info'):
            debugger.print(f'clean lines,is_h={is_h},lower2upper={lower2upper},from={old_total},to={len(lines)},elapsed={timer.elapsed():.3f}')
        return state['changed']

    def _adjust_lines(self, table:WTable, is_h:bool):
        """
        有些垂直线/水平线不能够合并的，这里自动缩短连接在最近cell的线上
        """
        cells = table.cells
        h_lines = table.h_lines
        v_lines = table.v_lines
        if is_h:
            x0,x1,y0,y1=0,2,1,3
            #调整水平线
            lines = table.h_lines
            # 排序垂直线
            lines1 = sorted(v_lines, key=lambda line: line[0])
            lines2 = sorted(v_lines, key=lambda line: line[0], reverse=True)
        else:
            x0,x1,y0,y1=1,3,0,2
            #调整垂直线
            lines = table.v_lines
            # 排序水平线
            lines1 = sorted(h_lines, key=lambda line: line[1])
            lines2 = sorted(h_lines, key=lambda line: line[1], reverse=True)
      
        def find_line(b:BBox, lines:Sequence[_Line], is_upper:bool)->_Line|None:
            for line in lines:
                #当is_h=True的情景：
                #|
                #|[cell]
                #|
                #lines已经按要求排序了，所以找到第一个即可
                if b[y0] >= line[y0] and b[y1] <= line[y1] and ((is_upper and line[x0] >= b[x1]) or ((not is_upper) and line[x0] <= b[x0])):
                    return line
                else:
                    pass
            return None

        for cell in cells:
            b = cell.bbox
            for line in lines:
                if b[y0]<line[y0]< b[y1]:
                    if line[x1] == b[x0]:
                        #｜                           |
                        #--(line)[cell]  => (line)----|
                        #｜(v_line)                   |
                        # 调整line连接到v_line上
                        line2 = find_line(b, lines2,False)
                        if line2:
                            line[x1] = line2[x0]
                    elif line[x0] == b[x1]:
                        #      |            |
                        #[cell]--(line)  =》|-------(line)
                        #      |(v_line)    |(v_line)
                        line2 = find_line(b, lines1,True)
                        if line2:
                            line[x0] = line2[x0]
                    else:
                        pass


    def _merge_line(self, h_lines:list[_Line], v_lines:list[_Line], line1:_Line, line2:_Line, is_h:bool):
        """
        line1合并到line2，如果重叠删除line1
        h_lines:[],
        v_lines:[],
        line1: 需要合并的线
        line2: 需要合并的线
        
        """
        if is_h:
            x0,x1,y0,y1=0,2,1,3
        else:
            x0,x1,y0,y1=1,3,0,2
        
        changed=False
        removed=False
        if line1[y0]!=line2[y0]:
            lines = v_lines if is_h else h_lines
            self._shift_lines(line1,line2[y0],lines,is_h=is_h)
            line1[y0]=line1[y1]=line2[y0]
            changed=True

        #如果有重叠的，就合并为一条，删除第一条
        if line1[x0]<=line2[x1] and line1[x1]>=line2[x0]:
            line2[x0]=min(line1[x0],line2[x0])
            line2[x1]=max(line1[x1],line2[x1])
            changed=True
            #表示line1需要被删除
            removed=True

        else:
            #表示line1不需要被删除
            pass

        return {'changed':changed,'removed':removed}
            

    def _shift_lines(self,line:_Line,value:float,lines:list[_Line],is_h:bool):
        """
        line移动到value的位置后，与其相交的线，也需要同步移动
        line: 表示该线被移动，如果是水平线，就是上下移动，如果是垂直线的，就是左右移动
        i: 表示移动哪个值，i=0,1,2,3
        value: 如果是水平线的，value就是新的y坐标的值，如果是垂直线的，value就是新的x坐标的值
        lines: 需要调整的线
        is_h: True表示移动的是水平线（line）
        """

        def get_index(line1:_Line,line2:_Line,d:float=0):
            if is_h:
                x0,x1,y0,y1=0,2,1,3
            else:
                x0,x1,y0,y1=1,3,0,2
            
            if line1[x0]<=line2[x0]<=line1[x1]:
                #  |line2
                #-------line1
                #    |line2
                if abs(line1[y0]-line2[y0])<=d:
                    return y0
                elif abs(line1[y0]-line2[y1])<=d:
                    return y1
                else:
                    return -1
            else:
                return -1
        
        if is_h:
            #line为水平线，lines为垂直线
            x0,y0,x1,y1=0,1,2,3
            lines = sorted(lines,key=lambda line:line[0])
        else:
            #
            x0,y0,x1,y1=1,0,3,2
            lines = sorted(lines,key=lambda line:line[1])

        changed=False
        for line2 in lines:
            #if line is line2 or is_h(line)==is_h(line2):#line.is_h==line2.is_h:
                #continue
            if line2[x0]>line[x1]:
                #前面排序了，如果没有排序，这里就不能够break
                break
            i = get_index(line,line2)
            if i != -1:
                line2[i] = value
                changed=True
        
        return changed

    def _remove_lines(self,table:WTable,lines:list[_Line]):
        """
        删除无效的线
        """
        debugger=self._debugger.bind(page=table.pages[0].number)
        timer = utils.Timer.start()
        old_total=len(lines)
        #删除无效的线
        lists.remove2(lines,lambda i,lines:lines[i][2]-lines[i][0]==0 and lines[i][3]-lines[i][1]==0)
        lists.distinct(lines)
        if debugger.allow('info'):
            debugger.print(f'remove lines,from={old_total},to={len(lines)},elapsed={timer.elapsed():.3f}')

    def _split_lines(self,lines:list[_Line])->tuple[list[_Line],list[_Line]]:
        h_lines:list[_Line]=[]
        v_lines:list[_Line]=[]
        
        for line in lines:
            if line[0]==line[2]:
                #v_lines.append(list(line))
                v_lines.append(line)
            elif line[1]==line[3]:
                #h_lines.append(list(line))
                h_lines.append(line)
            else:
                pass   
        return h_lines,v_lines
def usage():
    wt = WTable([])
    #合并指定范围的单元格为一个，不改变行列数
    wt.merge_cells(0,1,0,3)
    wt.merge_cells(0,1,0,3)
    #如果需要重新计算行列，不执行也行，只是如果可能行列繁琐了
    wt.rebuild()

    #获得指定的行，等于列数，没有去重
    #row = wt[0:1,:]
    row1 = wt.get_row(0)
    row2 = wt.get_row(1)
    #假设row[0]，需要跨row[0:2]
    #    [zz]      =>调整这个跨几列
    #[xx][xx][xx]
    c1=row2[0]
    c2=row2[-1]
    i1,i2,j1,j2=c1.row_index,c1.row_index+c1.row_span,c1.col_index,c2.col_index+c2.col_span
    if wt.can_merge(i1,i2,j1,j2):
        wt.merge_cells(i1,i2,j1,j2)


    