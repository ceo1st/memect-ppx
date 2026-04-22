

from concurrent.futures import ThreadPoolExecutor
import logging
from typing import Any, Callable, Final, Mapping, Sequence, TypeGuard, cast

from memect.base import utils
from memect.base.bbox import BBox
from memect.base.debug import XDebugger
from memect.base.matrix import Matrix
from memect.pdf.base import KChar, KDocument, KFigure, KObject, KPDFFigure, KPage, KTable, KTextbox, VObject
from memect.pdf.model import ModelManager
from memect.pdf.sort import Sorter

class _Item:
    """目的是为了方便调整bbox，而且知道原对象"""
    def __init__(self,source:Any):
        super().__init__()
        
        self.bbox:BBox = source.bbox.large if hasattr(source,'bbox') else source
        self.source:Final= source
class Parser:
    _logger=logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger=XDebugger(f'{__module__}.{__qualname__}')
    def __init__(self,manager:ModelManager):
        super().__init__()
        self._table_det:Final = manager.get('table_det')
        self._cache_key:Final='cache/default/wbk/table_det'
        pass

    def parse(self,doc:KDocument,*,max_workers:int=0):
        def get_tables(page:KPage):
            tables:list[Any]=[]
            for vobj in page.vobjects:
                if vobj.is_table():
                    img = page.crop(vobj.bbox)
                    if img:
                        tables.append((img,vobj.cache))
                    else:
                        #bbox是无效的？
                        pass

            return tables
        
        self._table_det.parse(doc,self._cache_key,handler=get_tables)

        self._do(self.parse_page,doc.working_pages,max_workers=max_workers)
    
    def parse_page(self,page:KPage):
        #按无边框的方式解析
        i=0
        for vobj in page.vobjects:
            if vobj.is_table():
                self._parse_table(page,i,vobj)
                i+=1
        pass

    def _parse_table(self,page:KPage,index:int,vobj:VObject):
        debugger = self._debugger.bind(page=page.number)
        bbox = vobj.bbox
        cells = self._get_cells(page,index,vobj)
        
        pdf_chars = bbox.get(page.pdf_chars,ratio=0.7)
        pdf_figures = bbox.get(page.pdf_figures,ratio=0.7)
        ocr_chars = vobj.ocr_chars
        ocr_objects = [v for v in vobj.vobjects if v.is_figure() or v.is_formula() or v.is_chart() or v.is_code()]
        #可能还需要支持表格内有公式
        #如果同时有ocr图片和pdf图片，可能识别为同一个区域，也可能不同，也可能部分重叠
        #所以，为了一致，要么仅仅使用ocr的，要么仅仅使用pdf的

        if len(pdf_chars)>0:
            #如果有pdf的文字，任务来自pdf，按pdf的阅读顺序解析更加准确？
            #99.99%的情况下是的，除了个别pdf，因为典型的方式是先有表格结构，再根据表格结构生成pdf指令，是按顺序的
            #除了个别可能是后来修改，或者pdf制作工具存在bug
            #同时需要根据模型获得的cells，扩展或者截断？
            cells = self._get_cells_by_model(page,index,vobj)
        else:
            cells =  self._get_cells_by_model(page,index,vobj)
        

        #TODO 需要先考虑cells可用塞入所有的字符？
        raw_cells:Final = list(cells)
        #避免重叠
        cells = self._adjust_cells(cells)
        chars = pdf_chars+ocr_chars
        #如果有pdf图片，就仅仅使用pdf图片，表示来自pdf的解析
        other_objects:list[KPDFFigure|VObject]=[]
        if len(pdf_figures)>0:
            other_objects=list(pdf_figures)
        else:
            other_objects=list(ocr_objects)
        
        #有些图片有文字，可能被ocr识别为文字，去掉
        removed_chars:list[KChar]=[]
        for obj in other_objects:
            removed_chars.extend(obj.bbox.get(chars,ratio=0.7,remove=True))

        self._expand_cells(cells,chars,[0.7,0.5])
        self._expand_cells(cells,other_objects,[0.7,0.5])

        from .builder import TableCellBuilder
        table = TableCellBuilder().build(page,bbox,cells)

        objs:list[KObject|VObject] = []
        objs.extend(chars)
        objs.extend(other_objects)

        object_total = len(objs)
        #会删除用掉的对象
        self._fill_cells(table,objs)
        page.objects.append(table)
        if debugger.allow('draw'):
            page.draw(('page',None),('table',[vobj]),(f'pdf_figures={len(pdf_figures)}',pdf_figures),(f'ocr_objects={len(ocr_objects)}',ocr_objects),(f'pdf_chars={len(pdf_chars)}',pdf_chars),(f'ocr_chars={len(ocr_chars)}',ocr_chars),(f'final_chars={len(chars)}',chars),(f'raw_cells={len(raw_cells)}',raw_cells),(f'cells={len(cells)}',cells),(f'remain_objects={len(objs)}/{object_total}',objs),('lines',table.get_lines()),index=index,dir='debug/default/wbk/table',show_type=False)

    def _expand_cells(self,cells:list[BBox],objs:Sequence[Any],ratios:Sequence[float]):
        """确保cell能够塞入对象"""
        objs = list(objs)
        for ratio in ratios:
            if not objs:
                break
            for i,cell in enumerate(cells):
                cell_objs = cell.get(objs,ratio=ratio,remove=True)
                if cell_objs:
                    cells[i]=cell.union(BBox.join2(cell_objs))
                if not objs:
                    break
    
    def _fill_cells(self,table:KTable,objs:list[KObject|VObject]):
        def remove_spaces(objs:Sequence[KObject])->list[KObject]:
            new_objs:list[KObject]=[]
            for obj in objs:
                if isinstance(obj,KChar) and obj.text.isspace():
                    pass
                else:
                    new_objs.append(obj)
            return new_objs
        
        #>=3.13才能够使用TypeIs
        def is_chars(objs:Sequence[Any])->TypeGuard[Sequence[KChar]]:
            return all(isinstance(obj,KChar) for obj in objs)
        
        def is_figures(objs:Sequence[Any])->TypeGuard[Sequence[KFigure]]:
            return all(isinstance(obj,KFigure) for obj in objs)
        
        def is_vobjects(objs:Sequence[Any])->TypeGuard[Sequence[VObject]]:
            return all(isinstance(obj,VObject) for obj in objs)
        
        for cell in table.cells:
            if not objs:
                break
            assert cell.bbox is not None
            #TODO 单元格也可以存在复杂的布局，如果是这样，又需要进行一个单元格的版面分析
            #目前仅仅支持简单的格式，要么为纯文本，要么为图片
            #如果是文本和图片混合，如
            #--text1----
            #--figure1--
            #--figure2--
            #--text2----
            cell_objs = cell.bbox.get(objs,ratio=0.7,remove=True)
            if not cell_objs:
                continue

            new_cell_objs:list[KObject]=[]
            for obj in cell_objs:
                if isinstance(obj,KPDFFigure|VObject):
                    #都使用图片即可，如果是vobject的，可以再考虑公式等？
                    obj=obj.make_figure()
                new_cell_objs.append(obj)

            #空格先暂时去掉，混合在图片中，没有意义
            valid_objs=remove_spaces(new_cell_objs)
            if is_chars(new_cell_objs):
                #全部都是字符
                tb = KTextbox.from_objects(new_cell_objs)
                cell.objects.append(tb)
                cell.text = tb.text
            elif is_figures(valid_objs):
                #都是图片
                for obj in valid_objs:
                    cell.objects.append(obj)
            else:
                #图文并茂，简单的从上到下分行即可
                groups:list[tuple[str,list[KObject]]]=[]
                for line in Sorter.get_lines(new_cell_objs):
                    #先去掉前后的空格字符
                    valid_line=remove_spaces(line)
                    if is_figures(valid_line):
                        groups.append(('figure',list(valid_line)))
                    else:
                        if not groups or groups[-1][0]!='char':
                            groups.append(('char',list(line)))
                        else:
                            groups[-1][1].extend(line)
                
                for group in groups:
                    if group[0]=='figure':
                        cell.objects.extend(group[1])
                    else:
                        tb = KTextbox.from_objects(group[1])
                        cell.objects.append(tb)
                        cell.text+=tb.text
            
    def _get_cells(self,page:KPage,index:int,vobj:VObject)->list[BBox]:
        if not vobj.ocr_chars:
            pass
        pass

    def _get_cells_by_model(self,page:KPage,index:int,vobj:VObject)->list[BBox]:
        #debugger = self._debugger.bind(page=page.number)
        bbox = vobj.bbox
        result = vobj.cache.pop(self._cache_key,None)
        if not result:
            #表示无法截图？
            return cast(list[BBox],[])
        
        #获得的结果是相对截图的，还需要进行转化
        width = result['width']
        height = result['height']
        #转化为相对页面的坐标，
        sw = bbox.width/width
        sh = bbox.height/height
        tx = bbox.x0
        ty = bbox.y0
        m = Matrix().lt_to_lb((width,height)).scale(sw,sh).translate(tx,ty)
        cells:list[BBox]=[]
        for cell_bbox in result['cells']:
            cells.append(BBox.from_list(cell_bbox,matrix=m))
        
        

        #if debugger.allow('draw'):
            #page.draw(('原图',None),('table',[vobj]),('cells',cells),('adjusted',adjusted_cells),index=index,dir='debug/default/wbk/table_det')
        
        return cells

    def _get_cells_by_pdf(self,page:KPage,index:int,vobj:VObject):
        pass

    def _adjust_cells(self,cells:Sequence[Any])->list[BBox]:
        items:list[_Item]=[_Item(cell) for cell in cells]
        self._adjust_items(items)
        return [item.bbox for item in items]

    def _adjust_items(self,cells:Sequence[_Item]):
        #debugger=self._debugger.bind(page=self.table.pages[0].number)
        strict=False
        def adjust(cells:Sequence[_Item]):
            cells = sorted(cells,key=lambda cell:cell.bbox.y1,reverse=True)
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

                    #如果完全包含
                    if c1.bbox.expand(dx=3,dy=3).contains(c2.bbox) or c2.bbox.expand(dx=3,dy=3).contains(c1.bbox):
                        continue
                    area = c1.bbox.intersect(c2.bbox)
                    if area is None:
                        continue
                    
                    if area.width>=area.height:
                        #[--c1--]
                        #  [--c2--]
                        #水平重叠的多，调整y
                        if c1.bbox.height>c2.bbox.height:
                            c1.bbox=c1.bbox.adjust(y0=c2.bbox.y1+1)
                        else:
                            c2.bbox=c2.bbox.adjust(y1=c1.bbox.y0-1)
                    else:
                        #      [--c3--]
                        #[--c4--] 
                        #垂直重叠的多，调整x即可
                        if c1.bbox.x1<c2.bbox.x1:
                            c3,c4=c2,c1
                        else:
                            c3,c4=c1,c2
                        if c3.bbox.width>c4.bbox.width:
                            c3.bbox=c3.bbox.adjust(x0=c4.bbox.x1+1)
                        else:
                            c4.bbox=c4.bbox.adjust(x1=c3.bbox.x0-1)
                    
                    #不能够break，可能还和其他的重叠
                    if strict and (c1.bbox.area==0 or c2.bbox.area==0):
                        raise RuntimeError('程序写错了')
        adjust(cells)

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

