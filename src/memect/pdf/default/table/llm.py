from concurrent.futures import ThreadPoolExecutor
import re
from typing import Any, Callable, Final, Sequence, cast

import PIL
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont

from memect.base.bbox import BBox
from memect.pdf.base import KCell, KChar, KDocument, KPage, KTable, KText, VObject
from memect.pdf.commons import FileInfo
from memect.pdf.model import ModelManager


class Parser:
    def __init__(self,manager:ModelManager):
        super().__init__()
        self._table_llm = manager.get("table_llm")
        self._table_llm_key: Final = "cache/default/table_llm"
    
    def parse(self,doc:KDocument,*,max_workers:int=0):

        def get_font(size:float):
            from memect.conf import get_font_path
            path = get_font_path('GoogleSansFlex_9pt-Regular.ttf')
            return PIL.ImageFont.truetype(path,size=size)
        

        def draw_anchors(page:KPage,img:PIL.Image.Image,vobj:VObject):
            #TODO 如果表格中的单元格有图片的，可以擦除图片，然后写上序号，如：
            #<id:xxx>
            if not vobj.vobjects:
                return
            
            
            draw = PIL.ImageDraw.Draw(img)
            p = vobj.bbox.transform(page.to_lt())
            tx = -p[0]
            ty = -p[1]
            sw = page.image.width/page.width
            sh = page.image.height/page.height
            m = page.to_lt().translate(tx,ty).scale(sw,sh)
            anchors:dict[str,Any]={}
            for i,obj2 in enumerate(vobj.vobjects):
                if obj2.is_figure() or obj2.is_chart() or obj2.is_code() or obj2.is_formula():
                    #从相对页面坐标转换为相对截图坐标
                    x0,y0,x1,y1 = obj2.bbox.transform(m)
                    #不能够使用随便的字符，否则llm会解析出错误的结构
                    text = re.sub('0','a',f'[MF={i}]')
                    text = re.sub('1','b',text)
                    a=(x1-x0)/len(text)
                    b=(y1-y0)
                    fontsize=min(a,b)
                    font = get_font(fontsize)
                    draw.rectangle((x0,y0,x1,y1),fill=(255,255,255))
                    draw.text((x0,y0),text,font=font,fill=(0,0,0))

                    anchors[text]=obj2
            
            vobj.cache['anchors']=anchors
            # img.show()

        def resolve_anchors(table:KTable,anchors:dict[str,VObject]):
            names:list[str]=list(anchors.keys())
            #TODO paddle/glm存在一个奇葩的现象，就是如果一个单元格中，有多个图片，如：<1>,<2>,<3>
            #然后下一个单元格为<4>，可能会返回[<1>,<2>,<3>,<4>],[<4>]，也就是同时也生成<4>在前一个单元格中，因为很符合规律？
            #所以，这里使用倒序的方式

            #2表示支持重复出现的情况
            method=2
            for cell in reversed(table.cells):
                #解析文本，如果有图片序号的，使用图片替代
                #<figure=1>
                #vobj.vobjects[0]
                text = cell.text
                if not text:
                    continue
                j=0
                while j<len(names):
                    name = names[j]
                    i = text.find(name)
                    if i==-1:
                        j+=1
                        continue
                    #TODO 因为可能存在重复出现，这里就不删除了，因为下一次还需要使用
                    if method==1:
                        del names[j]
                    else:
                        j+=1
                    
                    text=text[0:i]+'\n'+text[i+len(name):]
                    #在这里删除
                    vobj = anchors.pop(name,None)
                    if vobj:
                        cell.objects.append(vobj.make_figure())

                
                if cell.objects and text:
                    #TODO 如果有对象了，就需要转换了,BBox不知道怎么得到，就简单的使用
                    cell.objects.append(KText(table.page,BBox.join2(cell.objects).to_quad(),text=text))
                cell.text = text

        def get_tables(page: KPage):
            items: list[Any] = []
            for vobj in page.vobjects:
                if vobj.is_table():
                    # TODO 如果表格内包含有图片，可以把图片擦除，写上figure_id值
                    # 在解析完毕后，获得cell.text==figure_id => 再匹配图片
                    img = page.crop(vobj.quad)
                    if img is not None:
                        draw_anchors(page,img,vobj)
                        item = (
                            FileInfo(file=img, params={"task": "table"}),
                            vobj.cache,
                        )
                        items.append(item)
            return items

        def parse_page(page: KPage):
            for vobj in page.vobjects:
                if not vobj.is_table():
                    continue
                result = vobj.cache.pop(self._table_llm_key, None)
                anchors = vobj.cache.pop('anchors',None)
                if not result:
                    continue
                table = KTable.from_text(page, vobj.quad, result["text"])
                table.vobject = vobj
                if table.row_num > 0 and table.col_num > 0:
                    if anchors:
                        resolve_anchors(table,anchors)
                        pass
                    page.objects.append(table)
                else:
                    # TODO 无法解析，截图？
                    pass

        self._table_llm.parse(doc, self._table_llm_key, handler=get_tables)
        self._do(parse_page, doc.working_pages, max_workers=max_workers)

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

class CellState:
    def __init__(self, cell: KCell):
        super().__init__()
        self.chars: list[KChar] = []
        self.raw_text: str = self.normalize_text(cell.text)

    @property
    def bbox(self) -> BBox:
        assert len(self.chars) > 0
        return BBox.join([c.bbox for c in self.chars])

    @classmethod
    def get(cls, cell: KCell) -> "CellState":
        return cast(CellState, cell.working_state)

    @classmethod
    def normalize_text(cls, text: str) -> str:
        # 全角转化为半角，去掉空格等
        return text


class LLMTable:
    def __init__(self):
        super().__init__()

    def build(self, table: KTable):
        """获得了表格的结构"""
        page: Final = table.page
        pdf_chars = table.bbox.get(page.pdf_chars, ratio=0.8)
        ocr_chars = table.vobject.ocr_chars if table.vobject else []
        chars: Final[list[KChar]] = []

        def get_chars(cell: KCell):
            text = re.sub(r"\s", cell.text, "", re.DOTALL)

        texts: list[str] = []
        for cell in table.cells:
            cell.working_state = CellState(cell)
            texts.append(cell.working_state.text)
        counter = Counter(texts)
        for k, c in counter.items():
            if c == 1:
                # 只出现一次的，先匹配
                pass

        x_axis: list[float] = [0] * (1 + table.col_num)
        y_axis: list[float] = [0] * (1 + table.row_num)
        x_axis[0] = table.bbox.x0
        x_axis[-1] = table.bbox.x1
        y_axis[0] = table.bbox.y1
        y_axis[-1] = table.bbox.y0
        step = table.bbox.width / table.col_num
        for i in range(1, table.col_num):
            x_axis[i] = x_axis[i - 1] + step
        step = table.bbox.height / table.row_num
        for i in range(table.row_num):
            y_axis[i] = y_axis[i - 1] - step

        for cell in table.cells:
            state = CellState.get(cell)
            if not cell.text:
                continue
            # 先获得开始的bbox
            x0 = x_axis[cell.col_index]
            x1 = x_axis[cell.col_index + cell.col_span]
            y1 = y_axis[cell.row_index]
            y0 = y_axis[cell.row_index + cell.row_span]

            # 获得这个区域可能大了，也可能小了
            bbox = BBox(x0, y0, x1, y1)
            cell_chars = bbox.get(chars, ratio=0.5)
            if cell_chars:
                tb = KTextbox.from_objects(cell_chars)
                if tb.text == cell.text:
                    pass

        grid: list[list[Group[KChar]]] = []
        for i in range(table.row_num):
            for j in range(table.col_num):
                pass
