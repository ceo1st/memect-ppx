

import logging
import re
from io import BytesIO
from typing import Any, Final, Mapping, Sequence, TypedDict, cast

import PIL
import PIL.Image
import cv2
import numpy as np
from memect.base import lists
from memect.base.sdk import Api
from PIL.Image import Image

from ..core import images
from ..core.bbox import BBox
from ..core.matrix import M
from ..core.pattern import XPattern
from ..core.xdebugger import XDebugger
from ._base import Block, Char, Page, PageType, Sorter, Table, Text, VObject
from ._wtable import WCell, WObject, WTable
from ._xbase import XBlock

# 有不少跨页无边框表格
# local/嘉实bugs/pdfs/issue40.pdf

class _ModelCfg(TypedDict):
    name: str
    """模型在服务器上的名字"""
    mapping: Mapping[str, Any]
    """类型映射"""


class _DetectResult(TypedDict):
    objects: list[VObject]


def _image_to_bytes(image: Image, format: str = 'png') -> bytes:
    with BytesIO() as fp:
        image.save(fp, format=format)
        return fp.getvalue()


class _ClassifyApi:
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger = XDebugger(f'{__module__}.{__qualname__}')

    def __init__(self, settings: Mapping[str, Any]):
        super().__init__()
        self._model = cast(_ModelCfg, settings['model'])
        self._api = Api(**settings['api'])

    def classify(self, page: Page, bbox: BBox, *, image: bytes | None = None) -> Any:
        debugger = self._debugger.bind(page=page.number)
        if image is None:
            image = _image_to_bytes(page.crop(bbox))
        cfg = self._model
        params = {
            'models': [cfg['name']]
        }

        result = self._api.execute(image, params=params)
        model_result = result['models'][cfg['name']]

        new_result: dict[str, Any] = {}
        for obj in model_result['objects']:
            type_ = obj['type']
            if cfg.get('mapping'):
                type_ = cfg['mapping'].get(type_, type_)

            new_result[type_] = obj['score']

        if debugger.allow('gui'):
            #如果需要支持pypy，使用PIL或者Matplotlib
            image_array = np.frombuffer(image, dtype=np.uint8)
            image2 = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
            title = debugger.title('Image from bytes')
            cv2.imshow(title, image2)
            cv2.waitKey(0)
            cv2.destroyAllWindows()

        return new_result


class _DetectApi:
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger = XDebugger(f'{__module__}.{__qualname__}')

    def __init__(self, settings: Mapping[str, Any]):
        super().__init__()
        self._models = cast(Mapping[str, _ModelCfg], settings['models'])
        self._api = Api(**settings['api'])

    def detect(self,image:bytes|PIL.Image.Image, name: str = 'wbk') -> _DetectResult:
        """
        获得单元格信息  
        image: 
        name:  使用的模型的名字，如：wbk，ybk
        """
        if isinstance(image,PIL.Image.Image):
            #rgb
            image = _image_to_bytes(image)
        else:
            pass

        
        image_size: Final = images.get_size(image)
        #从左上角转换为左下角
        m = M(1, 0, 0, -1, 0,image_size[1]).to_tuple()

        cfg = self._models[name]
        params = {
            'models': [cfg['name']]
        }

        result = self._api.execute(image, params=params)
        model_result = result['models'][cfg['name']]

        vobjs: list[VObject] = []
        for obj in model_result['objects']:
            type_ = obj['type']
            if cfg.get('mapping'):
                type_ = cfg['mapping'].get(type_, type_)

            obj_bbox = BBox(obj['bbox']).transform(m).large.ensure((0,0,image_size[0],image_size[1]))
            vobj = VObject(obj_bbox, type=type_,score=obj['score'], kind='table', model_name=name)
            vobjs.append(vobj)

        return {
            'objects': vobjs
        }

    def batch(self,blocks:Block):
        #如果提供的是block，截图且转化为相对页面的坐标
        #如果是合并后的
        #yield from self._api.batch(files)
        pass


class _Item[T:WCell|VObject]:
    """目的是为了方便调整bbox，而且知道原对象"""
    def __init__(self,source:T):
        super().__init__()
        self.bbox:BBox = source.bbox.small
        self.source:T = source

class Parser:
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger = XDebugger(f'{__module__}.{__qualname__}')

    def __init__(self, settings: Mapping[str, Any]):
        super().__init__()
        self.settings = settings
        self._detect_api = _DetectApi(settings['detect'])

    def batch(self):
        """批量执行"""
        #可以批量进行api调用，提高性能
        pass

    def parse0(self,blocks:Sequence[Block]):
        
        #在不同页面的连续表格（跨页）
        #在同一个页面的连续表格（跨栏，甚至跨栏又跨列）

        #先处理简单的，就是跨页不跨栏
        #所在页面宽度必须一致
        def test1(blocks:Sequence[Block])->bool:
            widths = sorted([block.page.bbox.width for block in blocks])
            if widths[-1]-widths[0]<=5:
                return True
            else:
                return False
        
        #每个表格必须水平对齐
        x0_list = sorted([block.bbox.x0 for block in blocks])
        x1_list = sorted([block.bbox.x1 for block in blocks])
        x0 = x0_list[0]
        x1 = x1_list[-1]
        for block in blocks:
            #TODO 可能为了更好的，只需要气内容的高度即可？
            #避免重叠了错误的水平线/垂直线？
            b = block.bbox.copy(x0=x0,x1=x1).large
            #TODO 判断是否包含新的对象？，如果包含了新的其他对象，就不能够扩展
            block.bbox = b
        
        


    def parse(self, blocks: Sequence[Block]) -> Table | None:
        debugger = self._debugger.bind(page=blocks[0].page.number)
        #TODO 如果只是单个图片
        if len(blocks)==1:
            image = blocks[0].page.crop(blocks[0].bbox.large)
        else:
            #TODO 后台的api对图片的高度也是有限制的，不允许太大
            #需要合并几个在一起，假如都调整了对齐
            #TODO 实际上传递的blocks已经确保宽度一样的，这里再确保
            padding=10
            padding_image:PIL.Image.Image|None=None
            width = max(block.bbox.iwidth for block in blocks)
            #TODO 如果高度超过了限制，要么减少合并，要么就缩小
            height= sum(block.bbox.iheight for block in blocks)
            height+=(len(blocks)-1)*padding
            image = PIL.Image.new('RGB', (width, height))
            if padding>0:
                padding_image = PIL.Image.new('RGB',(width,padding),color=(255,255,255))
            
            y=0
            for i,block in enumerate(blocks):
                #宽度必须一致，也就是页面宽度一致，页面图片也一致
                block_image = block.page.crop(block.bbox)
                block_image = block_image.resize((width,int(width/block_image.width*block_image.height)))
                image.paste(block_image,(0,y))
                y+=block_image.height
                #TODO 需要添加一些空白吗？表示断开，因为目的只是需要获得正确的列
                if padding_image is not None and i+1<len(blocks):
                    image.paste(padding_image,(0,y))
                    y+=padding_image.height
            
            if debugger.allow('gui'):
                cv2_image = cv2.cvtColor(np.array(image),cv2.COLOR_RGB2BGR)
                cv2.imshow('zz',cv2_image)
                cv2.waitKey(0)
                cv2.destroyAllWindows()
        
        #返回的结果是相对图片，且原点已经转换为左下角
        result = self._detect_api.detect(image)
        #把result转换为相对页面
        # TODO 以后可能细分到block是来自图片还是pdf，因为pdf中，可能某个表格就是使用图片的
        vobjects = self._get_vobjects(block,result['objects'])
        if block.page.type == PageType.PDF:
            if block.is_table_figure():
                #表示该表格为一张图片，需要使用ocr的方式先获得文字+坐标
                raise ValueError(f'还没有实现')
            else:
                wcells = _PDFParser().parse(block, vobjects=vobjects)
        else:
            #TODO 表示全文都使用了ocr
            #TODO 暂时先使用针对PDF的解析了
            #wcells = _ImageParser().parse(block, vobjects=result['objects'])
            wcells = _PDFParser().parse(block, vobjects=vobjects)
        if not wcells:
            # 可能代码写错了？或者都没有匹配上等等
            self._logger.warning('无边框表格没有内容,page=%s', block.page.number)
            # raise RuntimeError(f'无边框表格没有内容')
            return None
        wtable = WTable(wcells, page=block.page, bbox=block.bbox)
        #因为使用了对象识别获得的单元格的坐标，可能会存在错位的情况，这里先修正
        #错位的
        if True:
            #如果是有边框表格使用无边框表格解析，就不需要做这些调整，因为有边框经常存在这种情况，而且有明确的线，就是要错位
            #而如果是无边框表格，因为没有明确的线，出现这种情况多数都是没有对齐所导致，可以智能调整一下，更加美观，也简化表格的结构
            self._fix0(wtable,False)
            self._fix0(wtable,True)
            
        # 然后再计算跨列表头，如果不够广泛，可以把下面两个禁用
        # 现在也可以不使用了，因为使用了对象识别
        if True:
            self._fix1(wtable)
            self._fix2(wtable)
            self._fix4(wtable)
            self._fix5(wtable)
            self._fix6(wtable)
            self._fix7(wtable)
            self._fix9(wtable)
    
        wtable.beautify()

        if True:
            #最后，补充空白行（主要是彩色表格或者原文为有边框的，空白行可以区分）
            self._fill_blank_rows(wtable,vobjects)
            pass
        return wtable.to_table()

    def parse_xblock(self, xblock: XBlock):
        """跨页无边框表格"""
        # 获得跨页的表格图片
        image = None
        pass

    def _get_vobjects(self,block:Block,vobjects:Sequence[VObject])->list[VObject]:
        # TODO 这里获得的vobjects可能稍微有些重叠，所以需要先调整一下，再进行处理更好

        debugger = self._debugger.bind(page=block.page.number)

        def clean_vobjects(vobjects:Sequence[VObject])->list[VObject]:
            """先清除被包含的"""
            #在有些情况下，可能出现这种情况
            #[--cell1--[cell2]---]  => cell1包含cell2，cell1比cell2大的多个，这个时候，是使用小对象还大对象？
            vobjects = sorted(vobjects,key=lambda vobj:vobj.bbox.area)

            i=0
            while i<len(vobjects):
                vobj = vobjects[i]
                include_vobjs = vobj.bbox.adjust(dx=3,dy=3).get(vobjects)
                include_vobjs.remove(vobj)
                if not include_vobjs:
                    i+=1
                    continue
                if vobj.score>=0.8:
                    #如果分数很高，就信任该对象，删除起包含的
                    if debugger.allow('info'):
                        debugger.print('delete small vobject',(vobj.score,vobj.bbox),[(vobj.score,vobj.bbox) for vobj in include_vobjs])
                    lists.remove(vobjects,include_vobjs)
                    i=vobjects.index(vobj)+1
                else:
                    include_vobjs.sort(key=lambda vobj:vobj.score,reverse=True)
                    if len(include_vobjs)>0 and include_vobjs[-1].score>=0.5:
                        #如果包含了多个，且最低的都可信
                        if debugger.allow('info'):
                            debugger.print('delete large vobject',(vobj.score,vobj.bbox),[(vobj.score,vobj.bbox) for vobj in include_vobjs])
                        del vobjects[i]
                    else:
                        i+=1

            return vobjects        

        
        page = block.page
        original_vobjects = vobjects
        vobjects = clean_vobjects(vobjects)        
        method=1
        if method==1:
            #先调整
            vobjs1 = self._adjust_vobjects(vobjects)
            #vobjs1 = vobjects
            #再选择
            vobjs = VObject.get_vobjects(vobjs1, 'table', types=['cell'], scores=0.5,mode='small')
            if debugger.allow('gui'):
                page.show('original vobjects', objects=original_vobjects, draw_text=False)
                page.show('adjusted vobject', objects=vobjs1, draw_text=False)
                page.show('found vobjects',objects=vobjs,draw_text=False)
        else:
            #先选择
            vobjs1 = VObject.get_vobjects(vobjects, 'table', types=['cell'], scores=0.5,mode='small')
            #再调整
            vobjs = self._adjust_vobjects(vobjs1)
            if debugger.allow('gui'):
                page.show('original vobjects', objects=original_vobjects, draw_text=False)
                page.show('found vobjects', objects=vobjs1, draw_text=False)
                page.show('adjusted vobjects',objects=vobjs,draw_text=False)
        
        Sorter.sort(vobjs)
        return vobjs

    def _adjust_vobjects(self,vobjects:Sequence[VObject])->list[VObject]:
        items:list[_Item[VObject]]=[_Item(vobj) for vobj in vobjects]
        self._adjust_items(items)
        new_vobjs:list[VObject]=[]
        for item in items:
            vobj = item.source.copy(bbox=item.bbox.adjust(dx=-1,dy=-1))
            new_vobjs.append(vobj)
        return new_vobjs

    def _adjust_items(self,cells:Sequence[_Item[VObject]]):
        #debugger=self._debugger.bind(page=self.table.pages[0].number)
        strict=False
        def adjust(cells:Sequence[_Item[VObject]]):
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
                    if c1.bbox.adjust(dx=3,dy=3).contains([c2.bbox]) or c2.bbox.adjust(dx=3,dy=3).contains([c1.bbox]):
                        continue
                    area = c1.bbox.intersect([c2.bbox])
                    if area is None:
                        continue
                    
                    if area.width>=area.height:
                        #[--c1--]
                        #  [--c2--]
                        #水平重叠的多，调整y
                        if c1.bbox.height>c2.bbox.height:
                            c1.bbox=c1.bbox.copy(y0=c2.bbox.y1+1)
                        else:
                            c2.bbox=c2.bbox.copy(y1=c1.bbox.y0-1)
                    else:
                        #      [--c3--]
                        #[--c4--] 
                        #垂直重叠的多，调整x即可
                        if c1.bbox.x1<c2.bbox.x1:
                            c3,c4=c2,c1
                        else:
                            c3,c4=c1,c2
                        if c3.bbox.width>c4.bbox.width:
                            c3.bbox=c3.bbox.copy(x0=c4.bbox.x1+1)
                        else:
                            c4.bbox=c4.bbox.copy(x1=c3.bbox.x0-1)
                    
                    #不能够break，可能还和其他的重叠
                    if strict and (c1.bbox.area==0 or c2.bbox.area==0):
                        raise RuntimeError(f'程序写错了')
        adjust(cells)

    def _fix0(self,wtable:WTable,is_row:bool):
        """修复错位的问题，或者认为无边框表格中不应该存在错位的情况"""
        
        debugger = self._debugger.bind(page=wtable.pages[0].number)

        def get_x(left_cells:Sequence[WCell],right_cells:Sequence[WCell])->float|None:
            if is_row:
                #True表示调整行
                #x0,x1=1,3
                x0,x1=3,1
            else:
                #表示调整列
                x0,x1=0,2
            #如果是行的，就是标题top_y
            
            #调整列：[left][right]
            #调整行：[left]
            #       [right]
            left_x:list[float] = []
            right_x:list[float]=[]
            for cell in left_cells:
                cb = cell.content_bbox
                if cb is not None:
                    left_x.append(cb[x1])
            for cell in right_cells:
                cb = cell.content_bbox
                if cb is not None:
                    right_x.append(cb[x0])
            
            if is_row:
                #调整行
                #left_x is top_x
                if not left_x:
                    #上面没有内容
                    x=max(c.bbox[x0] for c in right_cells)
                elif not right_x:
                    #下面没有内容
                    x=min(c.bbox[x1] for c in left_cells)
                else:
                    #上下都有内容
                    #因为行的方式，所以就仅仅一点重叠
                    max_overlapped_height=2
                    a=min(left_x)
                    b=max(right_x)
                    if debugger.allow('info'):
                        debugger.print(f'行的叠加高度为:{b-a},max={max_overlapped_height}')
                    if abs(a-b)>max_overlapped_height:
                        return None
                    x=(a+b)//2
                
                return x
            else:
                #调整列
                if not left_x:
                    #表示左边没有内容
                    x=min(c.bbox[x0] for c in right_cells)
                elif not right_x:
                    #右边没有内容
                    x=max(c.bbox[x1] for c in left_cells)
                else:
                    #左右两边都有
                    #允许调整一个字符的宽度，目前就简单的设置为10
                    #对于有些严重错位的kv表格，可以放宽这个限制？
                    #也就是不限制？
                    strict=False
                    max_overlapped_width=10
                    a=max(left_x)
                    b=min(right_x)
                    if debugger.allow('info'):
                        debugger.print(f'a={a},b={b},列的叠加长度为:{a-b},max={max_overlapped_width},strict={strict}')
                    if strict and a-b>max_overlapped_width:
                        #可以为负值，表示没有重叠
                        return None
                    x=(a+b)//2
                
                return x
                    
            

        def allow_adjust(x:float,is_row:bool,left_cells:Sequence[WCell],right_cells:Sequence[WCell],next_cells:Sequence[WCell])->bool:
            max_dx=10
            max_dy=5
            if is_row:
                #left_cells is top
                for cell in left_cells:
                    cb = cell.content_bbox
                    if cb and x-cb.y0>=min(max_dy,cb.height/2):
                        return False
                    #cell.bbox = cell.bbox.copy(y0=x)
                for cell in right_cells:
                    cb = cell.content_bbox
                    if cb and cb.y1-x>=min(max_dy,cb.height/2):
                        return False
                    #cell.bbox = cell.bbox.copy(y1=x)
                for cell in next_cells:
                    cb = cell.content_bbox
                    if cb and cb.y1-x>=min(max_dy,cb.height/2):
                        return False
                    #cell.bbox = cell.bbox.copy(y1=x)
                return True
            else:
                #调整列
                for cell in left_cells:
                    cb = cell.content_bbox
                    #if cb:
                        #print('===>left',x,cb.x1-x,cb,cell.text)
                    if cb and cb.x1-x>=min(max_dx,cb.width/2):
                        #表示内容溢出太多，不调整
                        return False
                    #cell.bbox = cell.bbox.copy(x1=x)
                for cell in right_cells:
                    cb = cell.content_bbox
                    #if cb:
                        #print('===>right',x,x-cb.x0,cb,cell.text)
                    if cb and x-cb.x0>=min(max_dx,cb.width/2):
                        return False

                    #cell.bbox = cell.bbox.copy(x0=x)
                for cell in next_cells:
                    cb = cell.content_bbox
                    #if cb:
                        #print('===>next',x,x-cb.x0,cb,cell.text)
                    if cb and x-cb.x0>=min(max_dx,cb.width/2):
                        return False
                    
                    #cell.bbox = cell.bbox.copy(x0=x)
                return True
        def fix_once()->bool:
            #也就是允许多层次错位的
            #local/cases/json2doc/report/2.pdf 第1页的，市场数据表格（因为包括了标题，去掉就没有这么严重）
            #基础数据表格

            allow_other=True
            n=wtable.row_num if is_row else wtable.col_num
            for i in range(n):
                if is_row:
                    #调整行
                    cells = wtable.get_row(i)
                else:
                    #调整列
                    cells = wtable.get_column(i)
                ok_cells:list[WCell]=[]
                left_cells:list[WCell]=[]
                right_cells:list[WCell]=[]
                other_cells:list[WCell]=[]
                for cell in cells:
                    if is_row:
                        #调整行
                        if cell.row_span==1:
                            ok_cells.append(cell)
                        elif cell.row_index+cell.row_span-1==i:
                            #|
                            #| |
                            #  |
                            #表示为top_cells
                            left_cells.append(cell)
                        elif cell.row_index==i:
                            #|
                            #||
                            # |
                            #表示为bottom_cells
                            right_cells.append(cell)
                        else:
                            # |
                            #||
                            # |
                            other_cells.append(cell)
                    else:
                        #调整列
                        if cell.col_span==1:
                            #如果为空白的，忽略？
                            if len(cell.objects)!=0:
                                ok_cells.append(cell)
                        elif cell.col_index+cell.col_span-1==i:
                            #      [--]
                            #[--|--|--]
                            left_cells.append(cell)
                        elif cell.col_index==i:
                            #[--]
                            #[--|--|--]
                            right_cells.append(cell)
                        else:
                            #   [--]
                            #[--|--|--]
                            other_cells.append(cell)
                
                if len(ok_cells)>0:
                    #不处理，要处理也可以，只是需要确定给
                    #左边或者右边的单元格分离为2个，也就是添加一个空白的单元格即可，如：
                    #[------]
                    #    [--]
                    #    [--------]
                    #给左边添加，变成了
                    #[---][--]
                    #     [--]
                    #     [-------]
                    #给右边添加，变成了
                    #[-------]
                    #     [--]
                    #     [--][-----]
                    continue

                if not allow_other and len(other_cells)>0:
                    continue
                
                if len(left_cells)==0 or len(right_cells)==0:
                    #实际上不需要判断了
                    continue
                
                if is_row:
                    next_cells = wtable.get_row(i+1,strict=True)
                else:
                    next_cells = wtable.get_column(i+1,strict=True)

                x=get_x(left_cells,right_cells)

                
                if debugger.allow('gui'):
                    wtable.show('left cells',bboxes=left_cells)
                    wtable.show('right cells',bboxes=right_cells)
                    wtable.show('next cells',bboxes=next_cells)
                    wtable.show('other cells',bboxes=other_cells)
                    if x is not None:
                        if is_row:
                            line= wtable.bbox.copy(y0=x,y1=x)
                        else:
                            line= wtable.bbox.copy(x0=x,x1=x)
                        wtable.show('adjust cell',bboxes=[*left_cells,*right_cells,line])

                if next_cells and x is not None:
                    #TODO 如果为空白的，实际上可以任意调整？
                    cb = (next_cells[0].content_bbox or next_cells[0].bbox)
                    if is_row:
                        if x<=cb.y1:
                            x=None
                    else:
                        if x>=cb.x0:
                            x=None
                    
                    if x is None and debugger.allow('info'):
                        debugger.print('需要调整的座标溢出了')

                if x is None:
                    continue

                #TODO 如果是有边框表格，可能就是有明确的线表示表示需要错位的，需要判断是否有明确的线
                #检查是否错位太大，太大就不调整了
                if not allow_adjust(x,is_row,left_cells,right_cells,next_cells):
                    if debugger.allow('info'):
                        debugger.print('需要调整的位置太多，就不调整')
                    continue



                if is_row:
                    #调整行
                    for cell in left_cells:
                        cell.row_span-=1
                    for cell in next_cells:
                        cell.row_index-=1
                        cell.row_span+=1
                        assert cell.row_index==i
                else:
                    #调整列
                    for cell in left_cells:
                        cell.col_span-=1
                    for cell in next_cells:
                        cell.col_index-=1
                        cell.col_span+=1
                        assert cell.col_index==i
                
                wtable.rebuild_grid()
                
                if is_row:
                    #left_cells is top
                    for cell in left_cells:
                        cell.bbox = cell.bbox.copy(y0=x)
                    for cell in right_cells:
                        cell.bbox = cell.bbox.copy(y1=x)
                    for cell in next_cells:
                        cell.bbox = cell.bbox.copy(y1=x)
                else:
                    for cell in left_cells:
                        cell.bbox = cell.bbox.copy(x1=x)
                    for cell in right_cells:
                        cell.bbox = cell.bbox.copy(x0=x)
                    for cell in next_cells:
                        cell.bbox = cell.bbox.copy(x0=x)
                
                if debugger.allow('gui'):
                    wtable.show('left cells',bboxes=left_cells)
                    wtable.show('right cells',bboxes=right_cells)
                    wtable.show('next cells',bboxes=next_cells)
                    #实际上没有做任何改变，不显示也可以
                    wtable.show('other cells',bboxes=other_cells)
                wtable.rebuild()
                return True
            return False
        
        fixed=False
        while True:
            if not fix_once():
                break
            fixed=True

        if fixed:
            if debugger.allow('gui'):
                wtable.show(f'after fix-cross-row={is_row}')
        
    def _fix1(self, wtable: WTable):
        #[a1][a2][a3][a4]
        #[x1][x2][x1][x2]
        #===>，a1和a2合并，a3和a4合并
        #[a1,a2] |[a3,a4]
        #[x1][x2][x1][x2]

        #如：
        #[2018][   ]|[2019][  ]
        #[收入][支出]|[收入][支出]
        #=>
        #[2018     ]|[2019     ]
        #[收入][支出]|[收入][支出]
        #特别的例子
        #[2018][2019][2020][2021]
        #[增加] [减少] [增加][减少]
        if wtable.row_num < 2:
            return
        
        debugger = self._debugger.bind(page=wtable.pages[0].number)

        row = wtable.get_row(1)

        def is_invalid(text:str)->bool:
            if not text or re.fullmatch(r'[0-9,.\-]+',text):
                #如果为数字，就是无效的
                return True
            else:
                return False
        
        def find_pairs(i:int,row:list[WCell])->list[tuple[int,int]]|None:
            if is_invalid(row[i].text2):
                return None
            j = i+2
            while j < len(row):
                c1 = row[i]
                c2 = row[j]
                #[c1][vv][c2][vv]
                if c1.text2 == c2.text2:
                    break

                j += 1
            if j >= len(row) or len(row)-j < j-i:
                return None

            # [c[i],c[i+1],c[i+2]] == [c[j],c[j+1],c[j+2]]
            ok = False
            for k in range(1, j-i):
                c1 = row[i+k]
                c2 = row[j+k]
                if c1.text2 != c2.text2 or is_invalid(c1.text2):
                    break
            else:
                ok = True

            if not ok:
                return None

            # 继续往下查找还有更多的组吗？
            n = j-i
            pairs: list[tuple[int, int]] = []
            pairs.append((i, j))
            pairs.append((j, j+n))
            k = j+n
            while k+n <= len(row):
                if [c.text2 for c in row[k:k+n]] == [c.text2 for c in row[i:j]]:
                    pairs.append((k, k+n))
                    k += n
                else:
                    break
            if debugger.allow('info'):
                with debugger.group('pairs'):
                    for p in pairs:
                        debugger.console.print((p,wtable[1,p[0]].text2))
            return pairs
        
        def do_fix(pairs:list[tuple[int,int]]) -> bool:
            start = pairs[0][0]
            end = pairs[-1][-1]
            row0:list[WCell]=[]
            for c in wtable.get_row(0):
                if c.col_index>=start and c.col_index+c.col_span<=end:
                    row0.append(c)
            
            if any(c.row_span!=1 for c in row0)  or row0[0].col_index!=start or row0[-1].col_index+row0[-1].col_span!=end:
                return False
            
            if len(pairs)!=len([c for c in row0 if not c.is_blank()]):
                return False
            
            #直接需要修复
            fixed=False
            splited_cells:list[tuple[WCell,int]]=[]
            for p in pairs:
                cells = wtable[0:1,p[0]:p[1]]
                lists.distinct(cells)
                if cells[-1].col_index+cells[-1].col_span>p[1]:
                    cb1 = cells[-1].content_bbox
                    cb2 = wtable[1,p[1]].content_bbox
                    if cb1 and cb2 and cb1.x1<=cb2.x0:
                        #TODO 严格的还需要判断内容是否溢出，没有溢出才操作
                        splited_cells.append((cells[-1],p[1]))
                        #wtable.split_cell(cells[-1],col_index=p[1])
                    else:
                        #有一个不满足
                        return False
                
            for c,k in splited_cells:
                wtable.split_cell(c,col_index=k)
                fixed=True
                
            for p in pairs:
                if wtable.can_merge(0,1,p[0],p[1]):
                    wtable.merge_cells(0,1,p[0],p[1])
                    fixed=True
            return fixed
        
        #修复一次就够了?，复杂的表格可能有多个不同的结构，如：
        #[2020]  [2021]    |[2020]  [2022]
        #[a1][a2][a1][a2]  |[b1][b2][b1][b2]
        fixed=False
        i=1
        while i<len(row):
            pairs = find_pairs(i,row)
            if not pairs:
                i+=1
            else:
                fixed = do_fix(pairs) or fixed
                i=pairs[-1][1]

        if fixed:
            # 多级表头，有空白的又可以继续，如：
            # [   ]
            # [项目]
            for i in range(wtable.col_num):
                c1 = wtable[0, i]
                c2 = wtable[1, i]
                a, b, c, d = c1.row_index, c2.row_index + \
                    c2.row_span, c1.col_index, c2.col_index+c2.col_span
                if c1.col_index == c2.col_index and c1.col_span == c2.col_span and (not c1.text2 and c2.text2 or c1.text2 and not c2.text2) and wtable.can_merge(a, b, c, d):
                    wtable.merge_cells(a, b, c, d)
        if fixed:
            wtable.rebuild()
            if debugger.allow('gui'):
                wtable.show('fix1')

    def _fix1_bak(self,wtable:WTable):
        #[a1][a2][a3][a4]
        #[x1][x2][x1][x2]
        #===>，a1和a2合并，a3和a4合并
        #[a1,a2] |[a3,a4]
        #[x1][x2][x1][x2]

        #如：
        #[2018][   ]|[2019][  ]
        #[收入][支出]|[收入][支出]
        #=>
        #[2018     ]|[2019     ]
        #[收入][支出]|[收入][支出]
        #特别的例子
        #[2018][2019][2020][2021]
        #[增加] [减少] [增加][减少]
        if wtable.row_num < 2:
            return
        
        debugger = self._debugger.bind(page=wtable.pages[0].number)

        

        def is_invalid(text:str)->bool:
            if not text or re.fullmatch(r'[0-9,.\-]+',text):
                #如果为数字，就是无效的
                return True
            else:
                return False
        
        #最简单的情况
        #[项目][2022年][2023年][2024年]
        #[    ][a1][a2][a1][a2][a1][a2]
        row = wtable.get_row(1)


        
        
    def _fix2(self, wtable: WTable):

        # local/嘉实bugs/pdfs/issue28.pdf  8页
        # 因为序号右对齐且太小，和序号分成2列
        # [序号]
        # [   ][1]
        # [   ][2]
        if wtable.col_num < 2 or wtable.row_num < 2:
            return

        debugger = self._debugger.bind(page=wtable.pages[0].number)

        def is_blank(row: Sequence[WCell]) -> bool:
            for c in row:
                if c.col_span != 1 or c.row_span != 1:
                    return False
                if c.text2:
                    return False
            return True

        def is_number(row: Sequence[WCell]) -> bool:
            for c in row:
                if c.col_span != 1 or c.row_span != 1:
                    return False
                if c.text2 and re.fullmatch(r'[0-9]+', c.text2) is None:
                    return False
            return True

        cell = wtable[0, 0]
        row1 = wtable.get_column(0)
        row2 = wtable.get_column(1)
        # TODO 不需要row_span==1
        if row1[0].col_span == 1 and row1[0].row_span == 1 and re.fullmatch(r'序号', cell.text2) and is_blank(row1[1:]) and is_number(row2) and wtable.can_merge(0, wtable.row_num, 0, 2):
            wtable.merge_cells(0, wtable.row_num, 0, 2)
            wtable.rebuild()
            if debugger.allow('gui'):
                wtable.show('fix2')
        pass

    def _fix3(self,wtable:WTable):
        """如果最后一行为：数据来源：xxxxx，可以合并为一行"""
        #[-------][---][---][---]             [------][---][---][-----]
        #[数据来源：xxxx][---][---]   =>合并为： [数据来源:xxxx           ]

        #local/cases/json2doc/report/14.pdf 第1页，图片表格，需要支持才可以执行到这里
        debugger = self._debugger.bind(page=wtable.pages[0].number)

        row = wtable.get_row(-1)
        #所有的都只能够跨一行
        #只有一个单元格有内容
        a_pattern=XPattern('fullmatch',
        patterns=[
            r'数据来源[:]?.+',
            r'资料来源[:]?.+',
            r'来源[:]?.+'
        ]
        )

        def condition1()->bool:
            for cell in row:
                if cell.row_span!=1:
                    return False
            return True
        
        def condition2()->bool:
            n=0
            for cell in row:
                if a_pattern.fullmatch(cell.text2):
                    n+=1
                elif cell.objects:
                    return False
                else:
                    pass
            return n==1

        def is_ok()->bool:          
            for fn in [condition1,condition2]:
                if not fn():
                    return False
            return True
        
        if not is_ok():
            return
        
        #最后一行
        wtable.merge_cells(wtable.row_num-1,wtable.row_num,0,wtable.col_num)
        wtable.rebuild()
        if debugger.allow('gui'):
            wtable.show('fix3')

    def _fix4(self,wtable:WTable):
        """"""
        #[---title---]
        #[c1][c2][c3]
        #[xx][xx][xx]
        #[---title---]  =>这个可以合并为一列
        #[c1][c2][c3]

        if wtable.row_num<5 or wtable.col_num<4:
            #至少5行，4列，太少就不处理了
            return
        
        def check1():
            row = wtable.get_row(0)
            if len(row)==1 and row[0].row_span==1 and row[0].col_span==wtable.col_num and row[0].text2:
                return True
            else:
                return False
        
        def check2(row:Sequence[WCell])->bool:
            if len(row)<2:
                return False
            n=0
            for cell in row:
                if cell.row_span!=1:
                    return False
                if cell.text2:
                    #必须有文本
                    n+=1
                elif cell.objects:
                    #没有问题，但是有其他对象
                    return False
                else:
                    #空白
                    pass
            if n==1:
                return True
            else:
                return False
        
        def check3(row1:Sequence[WCell],row2:Sequence[WCell])->bool:
            if len(row1)!=len(row2):
                return False
            for c1,c2 in zip(row1,row2):
                if c1.row_span!=c2.row_span or c1.col_span!=c2.col_span:
                    return False
                
                if c1.text2!=c2.text2 or len(c1.text2)==0:
                    return False
            
            return True

        if not check1():
            return 
        
        for i in range(3,wtable.row_num-1):
            row = wtable.get_row(i)
            if check2(row) and check3(wtable.get_row(1),wtable.get_row(i+1)):
                wtable.merge_cells(i,i+1,0,wtable.col_num)
                wtable.rebuild()
                #一般就一个，太多就不再继续？
                break
        pass

    def _fix5(self,wtable:WTable):
        #[  ] [-xxx-]
        #[xx]|[  ][  ]   =>如果没有[xx]，就没有空白的行，因为存在[xx]，刚好导致空白的行，因为[xx]居中显示
        #[  ]|[aa][bb]

        #最好的办法就是让[xx]变大跨3行，就不需要任何调整了
        #但是目前的识别为刚好就是居中
        if wtable.col_num<3 or wtable.row_num<3:
            return 
        
        def check0():
            #检查第一个单元格
            c1 = wtable[0,0]
            c2 = wtable[1,0]
            c3 = wtable[2,0]

            if c1.col_span==1 and c2.col_span==1 and c3.col_span==1 and c1.row_span==1 and c2.row_span==1 and c3.row_span==1 and c2.text2:
                return True
            else:
                return False
            
        def check1():
            #检查第一行
            row1 = wtable.get_row(0)
            for c in row1[1:]:
                #必须跨列，但是不能够跨行
                if c.col_span<2 or c.row_span!=1:
                    return False
            return True
        
        def check2():
            #检查第二和第三行
            row2 = wtable.get_row(1)
            row3 = wtable.get_row(2)
            if len(row2)!=len(row3):
                return False
            #去掉第一个单元格，后续的单元格都必须对应
            for c1,c2 in zip(row2[1:],row3[1:]):
                if c1.col_index!=c2.col_index or c1.col_span!=c2.col_span or c1.row_span!=c2.row_span:
                    return False
                
                if c1.row_span!=1 or c1.col_span!=1:
                    return False
                if c2.row_span!=1 or c2.col_span!=1:
                    return False
                
                #c1必须为空，c2必须有文本
                if c1.objects or not c2.text2:
                    return False
            return True 

        fns = [check0,check1,check2]
        for fn in fns:
            if not fn():
                return 

        row1 = wtable.get_row(0)
        row2 = wtable.get_row(1)
        row3 = wtable.get_row(2)
        #合并第二行
        for c in row2[1:]:
            wtable.merge_cells(1,3,c.col_index,c.col_index+c.col_span)
        #合并第一个
        wtable.merge_cells(0,3,0,1)
        wtable.rebuild()
    
    def _fix6(self,wtable:WTable):
        #[---xx---]
        #[---][---][xx]   => 因为[xx]居中对齐，倒置多了空白列
        #[-x-][-x-]
        
        if wtable.row_num<3 or wtable.col_num<3:
            return
        
        def check_cell(c1:WCell)->bool:
            """检查单元格是否跨列"""
            #必须满足
            #[----c1----]
            #[----][----]
            #[-c2-][-c3-]
            if c1.col_span<2 or c1.row_span>1 or not c1.text2:
                return False
            
            for k in range(c1.col_index,c1.col_index+c1.col_span):
                c2=wtable[1,k]
                c3=wtable[2,k]
                if not c2.objects and c3.text2 and c2.row_span==1 and c3.row_span==1 and c2.col_span==1 and c3.col_span==1:
                    pass 
                else:
                    #不满足
                    return False
            return True

        def check1()->bool:
            """检查是否存在居中的文本"""
            #[--c1--] =>空白
            #[--c2--] =>文本
            #[--c3--] =>空白
            n=0
            for c2 in wtable.get_row(1):
                c1 = wtable[0,c2.col_index]
                c3 = wtable[2,c2.col_index]
                if c2.col_span==1 and c2.row_span==1 and c2.text2 and c1.col_span==1 and c1.row_span==1 and c3.col_span==1 and c3.row_span==1 and not c1.objects and not c3.objects:
                    n+=1
                else:
                    pass
            return n>0
        
        if not check1():
            return 

        row1 = wtable.get_row(0)
        changed=False
        for c in row1:
            if check_cell(c):
                changed=True
                for k in range(c.col_index,c.col_index+c.col_span):
                    wtable.merge_cells(1,3,k,k+1)
        
        if changed:
            for i in range(wtable.col_num):
                c = wtable[0,i]
                #TODO 只能够有一个单元格有内容，才能够合并
                if c.col_span==1 and wtable.can_merge(0,3,c.col_index,c.col_index+c.col_span):
                    wtable.merge_cells(0,3,c.col_index,c.col_index+c.col_span)
            wtable.rebuild()
        

    def _fix7(self,wtable:WTable):
        """"""
        #[--][----xxx-----]
        #[xx][--x--][--x--]
        if wtable.row_num<2 or wtable.col_num<3:
            return
        
        def check_cell(c1:WCell)->bool:
            #检查是否如下
            #[----c1----]
            #[-c2-]|[-c2-]
            if c1.col_span<2 or c1.row_span>1 or not c1.text2:
                return False
            
            for k in range(c1.col_index,c1.col_index+c1.col_span):
                c2 = wtable[c1.row_index+c1.row_span,k]
                if c2.col_span==1 and c2.row_span==1 and c2.text2:
                    pass
                else:
                    return False
            return True
        
        def check1():
            n=0
            n1=0
            for c in wtable.get_row(0):
                if check_cell(c):
                    #有跨列的cell
                    n+=1
                elif c.col_span==1 and c.row_span==1:
                    if c.objects:
                        return False
                    else:
                        n1+=1
                elif c.col_span==1 and c.row_span==2 and c.objects:
                    #有跨2行有内容的，忽略
                    pass
                else:
                    return False
            
            return n>0 and n1>0

        if not check1():
            return
        
        #表示肯定有合并了
        changed=False
        for i in range(wtable.col_num):
            c = wtable[0,i]
            if c.col_span==1 and c.row_span==1 and wtable.can_merge(0,2,i,i+1):
                changed=True
                wtable.merge_cells(0,2,i,i+1)
        
        if changed:
            wtable.rebuild()
    
    def _fix8(self,wtable:WTable):
        """"""
        #[-----]
        #[-项目-] 因为居中
        #[-----]

        if wtable.row_num<2 or wtable.col_num<3:
            return
        
        def check():
            pass

        def get_first_cell():
            pass

        if not check():
            return
        
        cell = wtable[0,0]
        #wtable.merge_cells(0,n,0,1)
        #wtable.rebuild()

    def _fix9(self,wtable:WTable):
        """"""
        #[---][----xxx----]
        #[---][-xx-]|[-xx-]
        #空白的2行（或者多行）可以合并，因为合并后更加美观，且不影响理解
        #local/wbk/cases/pdfs/6.pdf 第7页
        #对于空白的区域，也可以使用识别的对象？
        if wtable.col_num<3 or wtable.row_num<2:
            return
        

        def is_col_span(cell:WCell)->bool:
            #[xxxxxxx]
            #[xx]|[xx]
            if cell.col_span==1 or cell.row_span>1:
                return False
            i=cell.row_index+cell.row_span
            cells = wtable[i:i+1,cell.col_index:cell.col_index+cell.col_span]
            if len(cells)<2:
                return False
            if any(c.col_span>1 or c.row_span>1 for c in cells):
                return False
            return True

        def handle_row(row_index:int)->bool:
            row = wtable.get_row(row_index)
            if any(cell.row_index!=row_index for cell in row):
                #如果包含了跨其它列的
                return False
            
            #跨列的单元格
            col_span_cells:list[WCell]=[]
            #空白的单元格
            blank_cells:list[WCell]=[]
            #无效的单元格
            invalid_cells:list[WCell]=[]
            for cell in row:
                if is_col_span(cell):
                    col_span_cells.append(cell)
                elif cell.is_blank() and cell.col_span==1 and cell.row_span==1:
                    #不一定需要col_span==1
                    blank_cells.append(cell)
                elif cell.row_span==2 and cell.col_span==1:
                    #已经跨2行，实际上可以跨多列，暂时就严格一点
                    pass
                else:
                    invalid_cells.append(cell)
            
            #print('=========>>>>>',i,len(blank_cells),[c.text for c in invalid_cells],[c.text for c in col_span_cells])
            if len(invalid_cells)>0 or len(blank_cells)==0 or len(col_span_cells)==0:
                return False
            
            changed=False
            for cell in blank_cells:
                next_cell = wtable[cell.row_index+cell.row_span,cell.col_index]
                if next_cell.is_blank() and next_cell.row_span==1 and wtable.is_column_align([cell,next_cell]):
                    wtable.merge_cells(cell.row_index,next_cell.row_index+next_cell.row_span,cell.col_index,cell.col_index+cell.col_span)
                    changed=True
            return changed
        
        changed=False
        i=0
        while i<wtable.row_num:
            if handle_row(i):
                changed=True
                i+=2
            else:
                i+=1
        
        if changed:
            wtable.rebuild()

    def _fill_blank_rows(self,wtable:WTable,vobjs:Sequence[VObject]):
        """补充空白的行"""

        debugger = self._debugger.bind(page=wtable.pages[0].number)
        def split_groups(row_index:int,row:list[WCell]):
            """
            把连续的没有跨行的分成一个组
            """
            group:list[WCell]=[]
            groups:list[list[WCell]]=[]
            for cell in row:
                if cell.row_index!=row_index or cell.row_span!=1:
                    group=[]
                else:
                    if not group:
                        groups.append(group)
                    group.append(cell)
            return groups
        
        def fix_row(i:int)->bool:
            #groups=split_groups(i,row)
            #TODO 现在仅仅处理最简单的情况，也就是该行没有跨行的
            if any(c.row_span!=1 for c in row ):
                return False
            #然后该行的高度需要比其他行的高，大概为2倍
            #和相邻的上下行比较即可
            fb = BBox.x_union([c.bbox for c in row if c.row_span==1])
            cb = BBox.x_union([c.content_bbox for c in row if c.row_span==1 and c.content_bbox])
            if not fb or not cb:
                return False
            #判断这个区域是否有vobjects?
            min_height=25
            if cb.y0>=fb.cy and fb.height>=min_height:
                #[---xxx--]
                #[--blank--]
                blank_vobjs:list[VObject]=[]
                for cell in row:
                    temp = cell.bbox.copy(y1=cb.y0).adjust(dx=-2,dy=-2).get(vobjs,mode='overlap')
                    #必须每一个都有吗？可能有些错误的识别，多了或者少了
                    cb.adjust(dx=-5,dy=-2).remove(temp,mode='overlap')
                    if temp:
                        blank_vobjs.extend(temp)
                if len(blank_vobjs)>=len(row):
                    if debugger.allow('gui'):
                        wtable.show('blank row (below)',bboxes=[*blank_vobjs])
                    wtable.add_blank_row(i,cb.y0-1,position='below')
                    return True
                else:
                    return False
            elif cb.y1<=fb.cy and fb.height>=min_height:
                #[--blank--]
                #[--xxx----]
                blank_vobjs:list[VObject]=[]
                for cell in row:
                    temp = cell.bbox.copy(y0=cb.y1).adjust(dx=-2,dy=-2).get(vobjs,mode='overlap')
                    cb.adjust(dx=-5,dy=-2).remove(temp,mode='overlap')
                    if temp:
                        blank_vobjs.extend(temp)
                
                if len(blank_vobjs)>=len(row):
                    if debugger.allow('gui'):
                        wtable.show('blank row(above)',bboxes=[*blank_vobjs])
                    wtable.add_blank_row(i,cb.y1+1,position='above')
                    return True
                else:
                    return False
            
            return False
            
        fixed=False
        i=0
        while i<wtable.row_num:
            row = wtable.get_row(i)
            if fix_row(i):
                fixed=True
                i+=2
            else:
                i+=1
        if fixed:
            #不清除空白行
            wtable.beautify(clean=False)
        


class _PDFParser:
    """原文来自pdf，按书写顺序切割"""
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    _debugger = XDebugger(f'{__module__}.{__qualname__}')

    _number_pattern: Final = XPattern(
        'match',
        join=False,
        patterns=[
            # 12.93% or -12.93%
            r'[-]?[0-9.]+%',
            # 放宽？会导致这样的无法识别，如：12.2020年xxxx => 会识别为"12.122020"，但是真正的为"12.20"，或者为"12."，太难区分了
            # 12,345.98，现在使用严格的方式，必须2为小数
            r'[-]?[0-9,.]+\.[0-9]{2}',
            # 整数，主要就是考虑第一列的序号
            r'[-]?[0-9]+(?![.、0-9)])',
        ]
    )

    def __init__(self):
        super().__init__()

    def parse(self, block: Block, *, vobjects: Sequence[VObject] | None = None) -> list[WCell]:
        # 有3种可能
        # 1.没有任何逻辑的书写顺序，概率很小，要么是pdf的制作工具很烂，要么是先生成了pdf模版，然后再添加数据
        # 2.通过常见工具（如：word，wps等）生成的pdf，书写顺序是按行的，也就是单元格是从左到右书写
        # 3.在设计表格的时候，是按行，但是单元格的内容是合并的，如：
        # [xx][2012年][2013年]
        # [xx][12月]  [6月]
        # 作者在设计这个表格的时候，2个单元格应该是：[2012年12月],[2013年6月]
        # 但是使用了2行设计，书写顺序就变成了：[2012年]->[2013年]->[12月]->[6月]
        # 如果只使用一行设计，书写顺序为：[2012年6月]->[2013年12月]

        # 下面这种典型的n*m表格，列粘连在一起，无法根据间距划分，需要支持这种，就需要增加更多的解析时间
        # local/嘉实bugs/pdfs/issue28.pdf 28-30页等

        debugger = self._debugger.bind(page=block.page.number)

        # TODO 暂时先忽略图片
        chars = block.chars()
        if not chars:
            #如果是有边框表格使用无边框解析，整个表格都没有内容，都是空白的单元格
            #这种情况下，也需要返回空白的单元格
            return []
        page = chars[0].page
        # 水平划分为span
        chars.sort(key=lambda char: char.index)
        spans: list[Text] = []

        while chars:
            t = self._parse_span(chars)
            if t is not None:
                # 如果不是空白字符串
                spans.append(t)

        if debugger.allow('gui'):
            # 显示一个窗口
            page.show('spans', objects=spans, draw_text=False)
            page.show('vobjs',objects=vobjects or [],draw_text=False)

        def case1(spans: list[Text]):
            # local/cases/json2doc/report/2.pdf 多数无边框表格
            # 非常奇葩的情况
            # -123 => 书写顺序为：[123,-]
            # abc-xyz =>书写顺序为:["abc","xyz","-"]
            i = 0
            while i+1 < len(spans):
                span0 = None
                span1 = spans[i]
                span2 = spans[i+1]
                #
                if i-1 > 0:
                    span0 = spans[i-1]

                if span1.bbox.align('y', span2.bbox, d=1) and abs(span2.bbox.x1-span1.bbox.x0) <= 1:
                    # [span2][span1] => [span2,span1]
                    if span0:
                        print('===========>>>zzz', (span0.text, span2.text,
                              span1.text), span0.bbox, span2.bbox, span1.bbox)
                    if span0 and span1.bbox.align('y', span0.bbox, d=1) and abs(span0.bbox.x1-span2.bbox.x0) <= 1:
                        # [span0][span2][span1] =>[span0,span2,span1]
                        span = span0+span2+span1
                        print('======>>>>', span.text,
                              (span0.text, span2.text, span1.text))
                        spans[i] = span
                        del spans[i+1]
                        del spans[i-1]
                    else:
                        span = span2+span1
                        print('======>>>>', span.text,
                              (span2.text, span1.text))
                        spans[i] = span
                        del spans[i+1]
                        i += 1
                else:
                    i += 1

        def case2(spans: list[Text]):
            """按行划分，合并间距稍微大一点的，不考虑pdf的书写顺序"""
            def get_dx(span1:Text,span2:Text)->float:
                if re.fullmatch(r'[(][a-zA-Z]+[)]',span1.text2):
                    #使用字体的高度获得宽度
                    return min(span1.bbox.height+5,20)
                else:
                    return 6
            lines:list[list[Text]] = Sorter.get_lines(spans)
            for line in lines:
                # 按书写顺序，[span1,span2,span3]，位置顺序也是：[span1,span2,span3]
                # 对于奇葩的，位置顺序可能为：[span1,span3,span2]，然后可能需要合并为：span=[span1,span3,span2]
                # 然后就需要再次尝试合并
                #print('====================>line',[span.text for span in line])
                i = 0
                while i+1 < len(line):
                    span1 = line[i]
                    span2 = line[i+1]
                    #TODO 考虑pdf的书写顺序吗？
                    #99%的情况书写顺序都是正确的，1%的可能为错误（奇葩的pdf）
                    #如果需要支持，就添加这个判断
                    #span1.chars[-1].index>=0 and span2.chars[0].index>=0 and span1.chars[-1].index+1==span2.chars[0].index
                    #dx = 6
                    # TODO 个别pdf是模板生成的，字符串的填入位置差距过大，或者有空格
                    #有些有序号的，间距可能特别大，如：
                    #(1)  xxxxx
                    #(i)  xxxxxx
                    #(i)
                    dx = get_dx(span1,span2)
                    #print('=============>>>>',(span1.text,span2.text),span1.bbox,span2.bbox,dx)
                    if -5 <= span2.bbox.x0-span1.bbox.x1 <= dx and span2.bbox.x0 >= span1.bbox.x0:
                        #如下：span=[span1,span2]，即使2个在同一行
                        #span = span1+span2
                        #=>span=[*span1.objects,*span2.objects]
                        span = Text.join([span1,span2],method=2)
                        line[i] = span
                        del line[i+1]
                        lists.replace(spans, [span1], [span])
                        lists.remove(spans, [span2])
                    else:
                        i += 1

            if debugger.allow('gui'):
                page.show('case2 spans', objects=spans, draw_text=False)
            pass
        case2(spans)
        # 然后划分数字列，这些列的去掉
        # 剩余的，再根据大一些间距合并
        # self._split1(spans)
        # 需要根据对象识别来进行把粘连的切开？
        self._split2(spans, vobjects=vobjects)

        if debugger.allow('gui'):
            # 显示一个窗口
            page.show('split spans', objects=spans, draw_text=False)

        # 再垂直合并，为了简化维护，仅仅支持最严格的方式
        # [t1]
        # ------------- t1可以为独立的单元格，也可能为[t1,t2] or [t1,t2,t3]
        # [t2]  [t4]
        # [t3]
        texts: list[Text] = []
        #100个pdf可能就只有一个是特殊的，就是不按逻辑出牌
        #为了支持例外，设置为False
        #TODO 对于2-3列简单的且单元格内容为多行文本的，使用这个准确度会提高很多
        #迟点再处理
        #local/cases/json2doc/report/5.pdf 第6页
        use_pdf=False
        if page.type==PageType.PDF:
            use_pdf=True
        if use_pdf:
            #需要先调整spans按pdf的书写顺序，因为前面的操作没有按顺序合并，可能乱了，这里再排序
            #严格的排序是+1的，现在因为前面打乱了，可能中间的不一致了，取最后一个或者最大一个
            #spans.sort(key=lambda span:span.chars[-1].index)
            spans.sort(key=lambda span:max([c.index for c in span.chars]))
            #第一步
            texts.extend(self._parse_text1(spans))
            if debugger.allow('gui'):
                page.show('pdf texts 1', objects=texts, draw_text=False)
            #第二步
            self._parse_text2(texts)
            if debugger.allow('gui'):
                page.show('pdf texts 2',objects=texts,draw_text=False)
        else:
            texts.extend(spans)

        self._fix1(texts)
        if debugger.allow('gui'):
            page.show('fix1', objects=texts, draw_text=False)

        wcells: list[WCell] = []
        for text in texts:
            #cell = WCell((text.min_bbox or text.bbox).large)
            cell = WCell(text.bbox.large)
            cell.objects.append(WObject(text.bbox, object=text, block=block))
            wcells.append(cell)
        
        #如果是独立的图片，可能为一列，或者一行，这里页需要补充
        #但是问题来了，如果是：行内图片，行内公式（图片）等如何区分
        #就仅仅处理简单的图片情况，如：
        #一列都是图片的，那么，这些图片就是单元格
        
        
        self._fix3(wcells, vobjects=vobjects,use_pdf=use_pdf)

        self._fix5(wcells)
        #补充单个图片的单元格
        if use_pdf:
            self._fill_figures(block,wcells,vobjects)

        
        #self._fix4(wcells,vobjects)

        return wcells


    def _split_rows(self, texts: Sequence[Text]) -> list[list[Text]]:
        return Sorter.get_lines(texts)

    def _parse_span(self, chars: list[Char]) -> Text | None:
        group: list[Char] = []
        group.append(chars[0])
        for i in range(1, len(chars)):
            char1 = group[-1]
            char2 = chars[i]


            if char1.page.type==PageType.PDF and char1.index+1 != char2.index:
                # 如果为严格的，必须是连续，但是考虑到可能把空格等去掉了
                pass
            # TODO 如何处理空格
            # 先小一点的，先合并靠的很近的

            dy = min(char1.bbox.height, char2.bbox.height)/2
            dy = max(4, dy)
            # 如何处理上下标？
            # 先根据间距划分，多数情况可以，有些粘连在一起的列就无法区分了
            # print('=======>>>',dx,dy,char1.bbox,char2.bbox,char1.bbox.over('y',char2.bbox,d=dy))
            # TODO 对于粘连在一起的如何切断？如：
            # [xxxxx][zzzz] =>这两个粘连在一起了，间距为0，可以通过先初步找到列
            # 现在不需要，可以先使用对象识别出单元格，如果是左对齐或者右对齐的，识别出来的单元格还是很准确的

            # 如果前面是全角字符，后面的可能会重叠比较多，如：
            # “），”，逗号可能往前移动很多
            dx1 = 5
            dx0 = max(2, char1.bbox.width)
            # d=char2.bbox.x0-char1.bbox.x1
            # print('==========>>',(char1.index,char2.index),(dy,dx0,dx1),char1.bbox,char2.bbox,char1.text,char2.text,char1.bbox.over('y',char2.bbox,d=dy) and -dx0<=char2.bbox.x0-char1.bbox.x1<=dx1)
            if char1.bbox.over('y', char2.bbox, d=dy) and -dx0 <= char2.bbox.x0-char1.bbox.x1 <= dx1:
                group.append(char2)
            else:
                break

        del chars[0:len(group)]
        return Text.create(objects=group, tl=True).strip()

    def _split1(self, spans: list[Text]):
        """把粘连在一起的数字分开，如：123.45678.89 => 123.45|678.89"""
        if not spans:
            return
        page: Final = spans[0].page
        debugger = self._debugger.bind(page=page.number)

        number_spans: list[Text] = []
        pairs: dict[Text, Sequence[Text]] = {}
        all_spans: list[Text] = []
        for i, span in enumerate(spans):
            # TODO 因为当前的span没有inline图片等，所以使用text即可一一对应
            # TODO 如果是整数的，误判率很高，如：
            # 1年内完成任务=>[1][年内完成任务] 或者 [1年内完成任务]
            m = self._number_pattern.match(span.text)
            if m is not None:
                if m.end() == len(span.text):
                    number_spans.append(span)
                    pairs[span] = [span, span]
                    all_spans.append(span)
                else:
                    t1 = span.select(m.start(), m.end())
                    t2 = span.select(m.end())
                    number_spans.append(t1)
                    pairs[t1] = [span, t1, t2]
                    all_spans.append(t1)
                    all_spans.append(t2)
            else:
                # new_spans.append(span)
                pass

        if debugger.allow('gui'):
            page.show('number spans', objects=number_spans, draw_text=False)

        # 如果获得的新的spans可以组成列，那么这些就作为独立的span，其他的仍然保留（或者再次合并？）
        def split_columns():
            # 按列排序，且右对齐
            number_spans.sort(key=lambda span: span.bbox.x1, reverse=True)
            groups: list[list[Text]] = []
            group: list[Text] = []
            for i in range(len(number_spans)):
                span = number_spans[i]
                if group and abs(group[0].bbox.x1-span.bbox.x1) <= 2:
                    # 右对齐，如果不想误差累积，使用group[0]，否则使用group[-1]
                    group.append(span)
                else:
                    group = [span]
                    groups.append(group)

            if not groups:
                return
            # 从左到右排序
            groups.sort(key=lambda g: g[0].bbox.x0)
            ok_groups: list[BBox] = []
            strict: bool = True
            for group in groups:
                group.sort(key=lambda span: span.bbox.y1, reverse=True)
                b = BBox.x_union(group)
                assert b is not None
                # 如果放宽的，再加一个条件obj.bbox.x1>b.x1
                if strict:
                    # 所有的都必须右对齐
                    objs = b.small.get(
                        all_spans, mode='overlap', filter=lambda obj: obj not in group)
                else:
                    # 因为是右对齐，允许在左边没有溢出的
                    objs = b.small.get(
                        all_spans, mode='overlap', filter=lambda obj: obj not in group and obj.bbox.x1 > b.x1)
                if len(group) >= 5 and not objs:
                    # b=BBox.x_union(group)
                    # assert b is not None
                    ok_groups.append(b)
                    # 表示该组的可以作为
                    for span in group:
                        pair = pairs[span]
                        if len(pair) > 2:
                            # pair=[span,t1,t2]
                            lists.replace(spans, pair[0:1], pair[1:])
                        else:
                            pass
                else:
                    # 删除这些
                    pass

            if debugger.allow('gui'):
                page.show('number groups', objects=ok_groups, draw_text=False)

        split_columns()

        return spans

    def _split2(self, spans: list[Text], *, vobjects: Sequence[VObject] | None = None):
        if not spans:
            return
        if not vobjects:
            return
        
        #TODO 如果识别的vcells仅仅为部分，不完全，就会出现只是部分切割
        vcells = VObject.get_vobjects(vobjects, 'table', ['cell'], 0.5,mode='small')
        if not vcells:
            return

        debugger = self._debugger.bind(page=spans[0].page.number)
        for span in spans[:]:
            span_bbox = span.min_bbox or span.bbox
            if span_bbox.width>20:
                dx=-5
            else:
                dx=-2
            overlapped_cells = (span.min_bbox or span.bbox).adjust(
                dx=dx, dy=-2).get(vcells, mode='overlap')
            if len(overlapped_cells) <1:
                #TODO 如果一个cell溢出太多了，如：
                #[--xxx--]---------   =>可能右边还有cell
                #---------[--xxx--]  => 或者左边还有
                #---[--xxx--]------- => 或者仅仅中间部分
                #需要信任cell，然后切开吗？
                #如果需要信任，只需要修改为"<1"即可
                continue

            if len(overlapped_cells)==1 and overlapped_cells[0].bbox.adjust(dx=10,dy=2).contains([span.min_bbox or span.bbox]):
                #如果溢出太多，就需要裁剪，如果仅仅溢出一点，特别是全角符号，如：（，）
                #很容易溢出一点（也就是vobj会小一些，特别是靠得很近的时候）
                continue
            # [xxxxx][xxx]
            overlapped_cells.sort(key=lambda vobj: vobj.bbox.x0)
            #print('=========>>',span.text,span.min_bbox,span.bbox,[a.bbox for a in overlapped_cells])
            # 可以切开，对于字符间距大的地方
            new_spans: list[Text] = []
            start = 0
            for i in range(len(span.chars)-1):
                c1 = span.chars[i]
                c2 = span.chars[i+1]
                # d=c2.bbox.x0-c1.bbox.x1
                vobj = overlapped_cells[0]
                #print('===>vvvv',(c1.text,c1.bbox),(c2.text,c2.bbox),vobj.bbox)
                #TODO 如果是来自ocr的，可能误差大一些
                #[c1]    [c2]
                #  [vobj]
                dx=min(2,c2.bbox.width//2)
                if c1.bbox.x0 < vobj.bbox.x1 < c2.bbox.x0+dx:
                    new_span = span.select(start, i+1)
                    new_spans.append(new_span)
                    start = i+1
                    del overlapped_cells[0]
                    if not overlapped_cells:
                        break

            if 0 < start < len(span.chars):
                new_spans.append(span.select(start))

            if len(new_spans) > 0:
                #print('=====>split span by vobj',[s.text for s in new_spans])
                lists.replace(spans, [span], new_spans)

    def _parse_text1(self, spans: Sequence[Text]) -> list[Text]:
        """
        按书写顺序垂直合并
        """

        #有些研报是使用特殊的工具生成的，如：填充某些表格
        #而不是使用word/wps等先生成docx，再转换为pdf
        #就会导致表格的书写顺序不一定是按行书写，如：
        #[tb1][tb2]
        #----------------- 可能先书写了tb1,tb2,tb3,tb4，如果是使用docx生成pdf的，顺序为tb1,tb2,tb4,tb3
        #[tb4][tb3]
        #当然，上面的情况也可以如下
        #[tb1]|[tb2]
        #-----|        =>tb2,tb3合并为一个
        #[tb4]|[tb3]
        #对于这种情况，就会导致了错误的合并
        def test1(group: list[Text], span: Text) -> bool:
            # 需要过滤这种
            # [span1][span2][span3]   => 如下会合并[span3,span4]，导致占据了[span1,span2]的空间
            # [span4--------------]
            b = span.bbox.union(group)
            objs = b.adjust(dx=-2, dy=-2).get([*spans,*texts], mode='overlap',
                                              filter=lambda obj: obj not in group and obj is not span)
            return len(objs) == 0

        def join(index: int, group: list[Text]) -> bool:
            t1 = group[-1]
            t2 = spans[index]
            # [t1]
            # [t2]
            
            #有些间距很大，如果需要支持更广泛（也更多的特殊情况？）
            leading=20
            #print('==========>>>',t1.text,'||',t2.text,t1.bbox,t2.bbox,t1.bbox.over('x',t2.bbox,d=5),-2<=t1.bbox.y0-t2.bbox.y1<=leading)
            if t1.bbox.over('x', t2.bbox, d=5) and -2 <= t1.bbox.y0-t2.bbox.y1 <= leading and test1(group, t2):
                group.append(t2)
                return True
            else:
                return False

        def parse(spans:list[Text])->Text:
            group: list[Text] = []
            group.append(spans[0])
            for i in range(1, len(spans)):
                if not join(i, group):
                    break
            del spans[0:len(group)]
            return Text.join(group)
        
        #会删除，复制一个
        spans = list(spans)
        texts:list[Text]=[]
        while spans:
            text = parse(spans)
            if text.text2:
                texts.append(text)
        return texts

    def _parse_text2(self,texts:list[Text]):
        """按pdf顺序解析的第二步"""
        #因为第一步是按简单的书写顺序合并，可能会出现如下情况
        #[span1]
        #[span2]    [span4]
        #[span3]    [span5]
        #text1=[span1,span2,span3] => span1可能为独立的
        #text2=[span4,span5]
        #或者
        #[span1]   [span3]
        #[span2]   [span4]
        #          [span5]
        #text1=[span1,span2]
        #text2=[span3,span4,span5] => span5可能为独立的

        if len(texts)==0:
            return

        def is_v_text(text:Text)->bool:
            """判断是否为：

            [x]  
            [x]
            """
            if len(text.lines)<2:
                return False
            for line in text.lines:
                if len(line.chars)>1:
                    return False
            return True
        
        def align_top(texts:list[Text],index:int,rows:list[list[Text]]):
            #[span1]
            #[span2]
            #-----------k
            #[span3]  [span5]
            #[span4]  [span6]
            row = rows[index]
            assert len(row)>1
            bbox = BBox.x_union(row[1:])
            assert bbox is not None
            text = row[0]
            lines = text.lines
            if len(lines)<2:
                return
            
            #也可以去掉这个判断
            if is_v_text(text):
                return 
            
            k=0
            for i,span in enumerate(lines):
                #if span.bbox.y1-bbox.y0<=2:
                #print('=====>>',span.text,span.bbox,bbox)
                if bbox.y1-span.bbox.y0>=2:
                    #span.bbox.y0
                    #  -------------bbox.y1
                    k=i
                    break
            if 0<k<len(lines):
                t1=text.slice(0,k)
                t2=text.slice(k)
                assert t1 is not None
                assert t2 is not None
                #t1无法确定，就使用单独的
                lists.replace(texts,[text],[*t1.lines,t2])
        
        def align_bottom(texts:list[Text],index:int,rows:list[list[Text]]):
            #[span1]     [span3]
            #[span2]     [span4]
            #---------------------k
            #            [span5]
            #            [span6]
            #[span3,span4]确定合并，span5,span6就无法确定了，所以分离

            #但是如果存在
            #[span1]   [span3]
            #[span2]   [span4]
            #          [span5]
            #[span8]   [span6]
            #[span9]   [span7]
            #text1=[span1,span2]
            #text2=[span3,span4,span5,span6,span7] 不需要分离
            row = rows[index]
            assert len(row)>1
            bbox = BBox.x_union(row[0:-1])
            assert bbox is not None
            text = row[-1]
            lines = text.lines

            #可能在align_top的时候处理了，分成了几个，就不再处理
            if text not in texts:
                return 
            if len(lines)<2:
                return
            
            #也可以去掉这个判断
            if is_v_text(text):
                return
            
            #如果不是很信任pdf的书写顺序，就设置为False
            if True:
                #因为在align_top的时候，text可能已经被替代了
                k1=texts.index(text)
                for row2 in rows[index+1:]:
                    #如果还有下一行，且下一行的span
                    k2 = texts.index(row2[0])
                    if k1+1==k2:
                        return            
            k=0
            for i,span in enumerate(lines):
                if span.bbox.y1-bbox.y0<=2:
                    #----bbox.y0
                    #    span.y1
                    k=i
                    break
            if k>0:
                t1=text.slice(0,k)
                t2=text.slice(k)
                assert t1 is not None
                assert t2 is not None
                #t2无法确定，就使用单独的
                lists.replace(texts,[text],[t1,*t2.lines])


        #print(f'====>>>',texts[0].page.number)
        rows:list[list[Text]] = Sorter.get_lines(texts)
        for i,row in enumerate(rows):
            if len(row)==1:
                #这种太难判断，就先分开为单个的？
                spans = row[0].lines
                lists.replace(texts,row,spans)
            else:
                align_top(texts,i,rows)
                #TODO 应该等align_top重新分行再处理过？
                align_bottom(texts,i,rows)

    def _fix1(self, texts: list[Text]):
        # 对于特别的还需要调整
        # [span1]   => text1=[span1,span2,span3]  => 可能需要有几种划分，如：[span1],[span2,span3] 或者 [span1,span2],[span3]，或者[span1][span2][span3]
        # [span2]
        # [span3] [text2] [text3]

        def split(text: Text, bbox: BBox):
            d = bbox.y0-text.bbox.y0
            if d >= 10:
                # 认为是居中对齐
                d -= 2
            else:
                d = -2

            t = text
            end = 1
            t = text.slice(0, 1)
            while t is not None and end < len(text.lines):
                if t.bbox.y0-bbox.y1 >= d:
                    end += 1
                    t = text.slice(0, end)
                else:
                    break

            end -= 1
            if end > 0:
                t1 = text.slice(0, end)
                t2 = text.slice(end)
                assert t1 is not None
                assert t2 is not None
                return t1, t2
            else:
                return None, None

        def accept(i: int, rows: list[list[Text]]) -> bool:
            if i-1 >= 0:
                b1 = BBox.x_union(rows[i-1])
                b2 = BBox.x_union(rows[i])
                assert b1 is not None
                assert b2 is not None
                # 对于这种需要分离的，行之间的间距应该比较大的
                # [--row1--]
                # [--row2--]
                if b1.y0-b2.y1 < 0:
                    return False
            if len(row) == 1 or len(row[0].lines) == 1:
                return False

            return True

        rows = self._split_rows(texts)
        for i, row in enumerate(rows):

            # 如果有多行
            # 根据间距划分？
            # [line1]
            # [line2]
            if not accept(i, rows):
                continue
            text = row[0]

            bbox = BBox.x_union(row[1:])
            assert bbox is not None
            # 仅仅底部对齐才考虑？
            t1, t2 = split(text, bbox)
            if t1 is not None and t2 is not None:
                lists.replace(texts, [text], [t1, t2])

        pass

    def _fix2(self, texts: list[Text]):
        # 这种也是
        # [text1][text2][span3]
        #              [span4]
        #              [span5]
        pass

    def _adjust_vobjects(self,vobjects:Sequence[VObject])->list[VObject]:
        items:list[_Item[VObject]]=[_Item(vobj) for vobj in vobjects]
        self._adjust_items(items)
        new_vobjs:list[VObject]=[]
        for item in items:
            vobj = item.source.copy(bbox=item.bbox.adjust(dx=-1,dy=-1))
            new_vobjs.append(vobj)
        return new_vobjs

    def _adjust_cells(self,cells:Sequence[WCell]):
        items:list[_Item[WCell]]=[_Item(cell) for cell in cells]
        self._adjust_items(items)
        for item in items:
            #可以直接设置
            item.source.bbox = item.bbox

        for cell in cells:
            cb = BBox.x_union(cell.objects)
            if cb is not None:
                cell.bbox = cell.bbox.union([cb.large])

    def _adjust_items[T:WCell|VObject](self,cells:Sequence[_Item[T]]):
        #debugger=self._debugger.bind(page=self.table.pages[0].number)
        strict=False
        def adjust(cells:Sequence[_Item[T]]):
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
                    if c1.bbox.adjust(dx=3,dy=3).contains([c2.bbox]) or c2.bbox.adjust(dx=3,dy=3).contains([c1.bbox]):
                        continue
                    area = c1.bbox.intersect([c2.bbox])
                    if area is None:
                        continue
                    
                    if area.width>=area.height:
                        #[--c1--]
                        #  [--c2--]
                        #水平重叠的多，调整y
                        if c1.bbox.height>c2.bbox.height:
                            c1.bbox=c1.bbox.copy(y0=c2.bbox.y1+1)
                        else:
                            c2.bbox=c2.bbox.copy(y1=c1.bbox.y0-1)
                    else:
                        #      [--c3--]
                        #[--c4--] 
                        #垂直重叠的多，调整x即可
                        if c1.bbox.x1<c2.bbox.x1:
                            c3,c4=c2,c1
                        else:
                            c3,c4=c1,c2
                        if c3.bbox.width>c4.bbox.width:
                            c3.bbox=c3.bbox.copy(x0=c4.bbox.x1+1)
                        else:
                            c4.bbox=c4.bbox.copy(x1=c3.bbox.x0-1)
                    
                    #不能够break，可能还和其他的重叠
                    if strict and (c1.bbox.area==0 or c2.bbox.area==0):
                        raise RuntimeError(f'程序写错了')
        adjust(cells)
    
    def _fix3(self, wcells: list[WCell], vobjects: Sequence[VObject] | None,use_pdf:bool=False):
        """
        use_pdf: True表示wells参考了pdf的书写顺序
        """
        if not vobjects or not wcells:
            return
        
        def get_text(cell:WCell)->Text|None:
            if len(cell.objects)!=1:
                return None
            wobj = cell.objects[0]
            if isinstance(wobj.object,Text):
                return wobj.object
            else:
                return None
            
        def is_pdf_cells(cells:Sequence[WCell])->bool:
            """判断来自pdf的cell是否为独立的，不需要再合并"""

            #如果信任pdf的书写顺序，对于大段粘连在一起的文本，对象识别可能会认为是一个大的单元格，如：
            #[---line1---]
            #[---line2---]
            #--------------- 为2个单元格的[line1,line2],[line3,line4]，但是因为line2和line3的间距很小，会被识别为[line1,line2,line3,line4]
            #[---line3---]
            #[---line4---]
            if len(cells)<2:
                return False
            
            cells = sorted(cells,key=lambda cell:cell.bbox.y1,reverse=True)
            for i in range(1,len(cells)):
                c1=cells[i-1]
                c2=cells[i]
                #必须都是多行文本，实际上一行也可，但是目前先多行
                text1 = get_text(c1)
                text2 = get_text(c2)
                if text1 is None or text2 is None:
                    return False
                if not text1.bbox.align('x',text2.bbox,d=10):
                    return False
                
                if len(text1.lines)<2 or len(text2.lines)<2:
                    return False

                if not (-2<=text1.bbox.y0-text2.bbox.y1<=8):
                    return False
            
            return True

        
        def get_bbox(vobj:VObject,cells:Sequence[WCell],wcells:Sequence[WCell],*,strict:bool=False)->BBox|None:
            """判断该vobj对应的区域是否合法，且返回一个合适的bbox"""
            cell_bbox = BBox.x_union(cells)
            assert cell_bbox is not None
            #可能vobj比new_bbox小
            #可能cell_bbox本身就和某些cell重叠，因为溢出一点
            new_bbox = vobj.bbox.union([cell_bbox])#.adjust(dx=1,dy=1)
            #自动调整一下x0,x1
            dx=2
            x0=new_bbox.x0
            x1=new_bbox.x1
            max_x0=cell_bbox.x0
            min_x1=cell_bbox.x1
            if cell_bbox.width>10:
                #可能全角字符
                min_x1=cell_bbox.x1-5
            else:
                pass
            while x0<=max_x0:
                new_bbox = new_bbox.copy(x0=x0)
                cells2 = new_bbox.get(wcells,mode='overlap')
                if len(cells2)==len(cells):
                    return cell_bbox.union([new_bbox])
                x0+=dx
            
            while x1>=min_x1:
                new_bbox = new_bbox.copy(x1=x1)
                cells2 = new_bbox.get(wcells,mode='overlap')
                if len(cells2)==len(cells):
                    return cell_bbox.union([new_bbox])
                x1-=dx
            
            return None



        # TODO 如果不是跨页的，可以使用这个页面，如果是跨页合并为一个完整的图片的，应该使用合并后的图片？
        page = wcells[0].pages[0]
        debugger = self._debugger.bind(page=page.number)

        for vobj in vobjects:
            #[---vobj-------]
            #[--w1--][--w2--]
            #需要避免下面的情况
            #      [--vobj----]
            #[--w1--][--w2----]
            #或者
            #[--vobj--]
            #[--w1--][--w2---]
            cells:list[WCell]=[]
            for cell in wcells:
                #重叠的宽度需要>1/2才算包含
                #cb = cell.bbox
                cb = cell.content_bbox or cell.bbox
                xb = cb.intersect([vobj.bbox])
                if xb is not None and xb.width>cb.width/2 and xb.height>cb.height/2:
                    cells.append(cell)
            #cells = vobj.bbox.adjust(dx=-3,dy=-2).get(wcells,mode='overlap')
            if not cells:
                #表示为空白的单元格
                #或者包含表格的单元格
                continue

            Sorter.sort(cells)
            #表示为文本单元格
            new_bbox = get_bbox(vobj,cells,wcells)

            if new_bbox is None:
                continue

            if use_pdf and is_pdf_cells(cells):
                #表示为来自pdf的书写顺序的单元格且这些单元格不需要再合并
                pass
            # TODO 可能合并多个?
            elif len(cells) == 1:
                cell = cells[0]
                #cell.bbox = cell.bbox.union([vobj.bbox.adjust(dx=-3, dy=-3)])
                cell.bbox = new_bbox
                # local/嘉实bugs/pdfs/issue28.pdf  第10页，图表9
                # 对于太小的，避免错位对齐，也调整一下，如：
                # [AA ]
                #     [1] => 刚好放在右边，没有和[AA],[BB]对齐，就导致多一列
                # [BB ]
            elif len(cells) > 1:
                # 获得的bbox可能会大一些，需要调整
                new_cell = WCell(new_bbox)
                for cell in cells:
                    new_cell.objects.extend(cell.objects)
                new_cell.invalidate()
                lists.replace(wcells, cells, [new_cell])
            else:
                #无效的
                pass
        
        if debugger.allow('gui'):
            page.show('fix text cell by vobjects', objects=wcells, draw_text=False)

        #TODO 如果是有边框表格被识别为无边框表格，可能存在正确的空白单元格，可以补充上去？
        #TODO 支持单元格内有图片？
        #TODO 如何支持图文并貌的单元格

        #前面已经调整过了，这里实际上没有必要再调整了
        if True:
            #确保wcells的
            self._adjust_cells(wcells)
            if debugger.allow('gui'):
                page.show('adjust cells',objects=wcells,draw_text=False)
        

    def _fix4(self,wcells:list[WCell],vobjects:Sequence[VObject]):
        """如果是有边框表格使用无边框表格解析，识别的空白单元格可能是正确的，需要补充"""
        min_width=10
        min_height=10
        blank_vobjs:list[VObject]=[]
        for vobj in vobjects:
            #没有包含任何的内容
            if vobj.bbox.width>=min_width and vobj.bbox.height>=min_height and not vobj.bbox.adjust(dx=2,dy=2).get(wcells,mode='overlap'):
                blank_vobjs.append(vobj)
        
        for vobj in blank_vobjs:
            wcells.append(WCell(vobj.bbox))
    
    def _fix5(self,wcells:list[WCell],is_column:bool=True):
        """简单的列对齐和行对齐，因为对象识别的区域可能大一些小一些，可能刚好倒置交叉重叠"""

        #这里需要考虑视觉上的划分，如：
        #[A   ][        B]
        #[C        ][   D]
        #如果没有颜色的和垂直线的，根据空间即可，但是如果A，B，C，D填充了背景颜色，视觉上就是形成了交叉的效果，作者的意图可能也是需要如此
        #所以，现在仅仅支持通过一点点误差来判断是否需要调整

        if is_column:
            #对齐列，也就是调整x
            x0,y0,x1,y1=0,1,2,3
            max_overlapped_length=5
        else:
            x0,y0,x1,y1=1,0,3,2
            max_overlapped_length=5

        page = wcells[0].pages[0]
        debugger = self._debugger.bind(page=page.number)
        #按x0排序，后续就可以跳过了
        wcells=sorted(wcells,key=lambda cell:cell.bbox[x0])
        fixed=False
        for i in range(len(wcells)):
            cell1 = wcells[i]
            for j in range(i+1,len(wcells)):
                cell2 = wcells[j]
                d = cell1.bbox[x1]-cell2.bbox[x0]
                if d<=0:
                    #已经排序过，不需要再继续
                    break

                #TODO 有些内容溢出的，也就是cb1<cell1.bbox，如何处理？
                cb1 = cell1.content_bbox
                cb2 = cell2.content_bbox
                
                #print('===>>zzzz',cell1.text,'||',cell2.text,d,cell1.bbox,cell2.bbox)
                if cb1 and cb2 and d<=max_overlapped_length and cell1.bbox[x0]<cell2.bbox[x0] and cb1[x1]<cb2[x0]:
                    #[cell1][cell2]
                    cx = (cell1.bbox[x1]+cell2.bbox[x0])//2
                    #TODO 内容有一点重叠都是可以的，如：
                    #xxxx）
                    #   （xxxxx
                    if cb1[x1]<=cx+1 and cb2[x0]>=cx-1:
                        if debugger.allow('info'):
                            debugger.print(f'align column x={cx}',((cell1.bbox,cell1.text),(cell2.bbox,cell2.text)))
                        cell1.bbox = cell1.bbox.set((x1,cx))
                        cell2.bbox = cell2.bbox.set((x0,cx))
                        fixed=True
        
        if fixed:
            if debugger.allow('gui'):
                page.show(f'align cells,column={is_column}',objects=wcells,draw_text=False)
        pass


    def _fill_figures(self,block:Block,wcells:list[WCell],vobjects:Sequence[VObject]|None):
        vobjects = vobjects or []
        figures = block.figures()
        if not figures:
            return
        
        for figure in figures[:]:
            #是否需要在已经存在的cell中
            for cell in wcells:
                if cell.bbox.adjust(dx=2,dy=2).contains([figure.bbox]):
                    cell.objects.append(WObject(figure.bbox,object=figure,block=block))
                    figures.remove(figure)
                    break
        
        for figure in figures:
            for vobj in vobjects:
                #print('====>>>>',figure.bbox,vobj.bbox,vobj.bbox.adjust(dx=3,dy=3).contains([figure.bbox]))
                if vobj.bbox.adjust(dx=3,dy=3).contains([figure.bbox]):
                    cell = WCell(figure.bbox)
                    cell.objects.append(WObject(figure.bbox,object=figure,block=block))
                    wcells.append(cell)
                
        #self._adjust_cells(wcells)
        debugger = self._debugger.bind(page=block.page.number)
        if debugger.allow('gui'):
            block.page.show('figure cells',objects=wcells)

class _ImageParser:
    """原文为图片的解析，文本来自ocr识别，切割方式不一样"""

    def __init__(self):
        super().__init__()

    def parse(self, block: Block, *, vobjects: Sequence[VObject] | None = None) -> list[WCell]:
        return []
