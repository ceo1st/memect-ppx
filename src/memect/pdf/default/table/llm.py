from concurrent.futures import ThreadPoolExecutor
import re
from typing import Any, Callable, Final, Sequence

import PIL
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont

from memect.base.bbox import BBox
from memect.pdf.base import KDocument, KFigure, KPage, KTable, KText, VObject
from memect.pdf.commons import FileInfo
from memect.pdf.model import ModelManager


class Parser:
    def __init__(self, manager: ModelManager):
        super().__init__()
        self._table_llm = manager.get("table_llm")
        self._table_llm_key: Final = "cache/default/table_llm"

    def parse(self, doc: KDocument, *, max_workers: int = 0):

        def get_font(size: float):
            from memect.conf import get_font_path

            path = get_font_path("GoogleSansFlex_9pt-Regular.ttf")
            return PIL.ImageFont.truetype(path, size=size)

        def draw_anchors(page: KPage, img: PIL.Image.Image, vobj: VObject):
            # TODO 如果表格中的单元格有图片的，可以擦除图片，然后写上序号，如：
            # <id:xxx>
            if not vobj.vobjects:
                return

            draw = PIL.ImageDraw.Draw(img)
            p = vobj.bbox.transform(page.to_lt())
            tx = -p[0]
            ty = -p[1]
            sw = page.image.width / page.width
            sh = page.image.height / page.height
            m = page.to_lt().translate(tx, ty).scale(sw, sh)
            anchors: dict[str, Any] = {}
            for i, obj2 in enumerate(vobj.vobjects):
                if (
                    obj2.is_figure()
                    or obj2.is_chart()
                    or obj2.is_code()
                    or obj2.is_formula()
                ):
                    # 从相对页面坐标转换为相对截图坐标
                    x0, y0, x1, y1 = obj2.bbox.transform(m)
                    # 不能够使用随便的字符，否则llm会解析出错误的结构
                    text = re.sub("0", "a", f"[MF={i}]")
                    text = re.sub("1", "b", text)
                    a = (x1 - x0) / len(text)
                    b = y1 - y0
                    fontsize = min(a, b)
                    font = get_font(fontsize)
                    draw.rectangle((x0, y0, x1, y1), fill=(255, 255, 255))
                    draw.text((x0, y0), text, font=font, fill=(0, 0, 0))

                    anchors[text] = obj2

            vobj.cache["anchors"] = anchors
            # img.show()

        def resolve_anchors(table: KTable, anchors: dict[str, VObject]):
            names: list[str] = list(anchors.keys())
            # TODO paddle/glm存在一个奇葩的现象，就是如果一个单元格中，有多个图片，如：<1>,<2>,<3>
            # 然后下一个单元格为<4>，可能会返回[<1>,<2>,<3>,<4>],[<4>]，也就是同时也生成<4>在前一个单元格中，因为很符合规律？
            # 所以，这里使用倒序的方式

            # 2表示支持重复出现的情况
            method = 2
            objects:list[str|KFigure]=[]
            for cell in reversed(table.cells):
                # 解析文本，如果有图片序号的，使用图片替代
                # <figure=1>
                # vobj.vobjects[0]
                text = cell.text
                if not text:
                    continue
                j = 0
                while j < len(names):
                    name = names[j]
                    i = text.find(name)
                    if i == -1:
                        j += 1
                        continue
                    # TODO 因为可能存在重复出现，这里就不删除了，因为下一次还需要使用
                    if method == 1:
                        del names[j]
                    else:
                        j += 1

                    #text = text[0:i] + "\n" + text[i + len(name) :]
                    
                    if len(objects)>0 and isinstance(objects[-1],str):
                        objects[-1]+=text[0:i]
                    else:
                        objects.append(text[0:i])

                    text=text[i+len(name):]
                    # 在这里删除
                    vobj = anchors.pop(name, None)
                    if vobj:
                        objects.append(vobj.make_figure())
                
                if text and len(objects)>0:
                    if isinstance(objects[-1],str):  
                        objects[-1]+=text
                    else:
                        objects.append(text)
                
                
                if len(objects)>1:
                    #表示包含了图片
                    assert len(cell.objects)>0
                    content_bbox = BBox.join2(cell.objects)
                    cell.objects.clear()
                    
                    #---text---
                    #---figure--
                    #---text---- or ---figure---(不应该出现这个)
                    for k,obj in enumerate(objects):
                        if isinstance(obj,str):
                            #如何计算text的bbox，现在不支持行内图片，仅仅支持行间图片
                            if k==0:
                                y1=content_bbox.y1
                            else:
                                fig_obj = objects[k-1]
                                assert isinstance(fig_obj,KFigure)
                                y1=fig_obj.bbox.y0
                            
                            if k+1<len(objects):
                                fig_obj = objects[k+1]
                                assert isinstance(fig_obj,KFigure)
                                y0=fig_obj.bbox.y1
                            else:
                                y0=content_bbox.y0

                            obj = KText(table.page,content_bbox.adjust(y0=y0,y1=y1),text=obj)
                        cell.objects.append(obj)
                    pass


        def get_tables(page: KPage):
            items: list[Any] = []
            for vobj in page.vobjects:
                if vobj.is_table():
                    # TODO 如果表格内包含有图片，可以把图片擦除，写上figure_id值
                    # 在解析完毕后，获得cell.text==figure_id => 再匹配图片
                    img = page.crop(vobj.quad)
                    if img is not None:
                        draw_anchors(page, img, vobj)
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
                anchors = vobj.cache.pop("anchors", None)
                if not result:
                    continue
                table = KTable.from_text(page, vobj.quad, result["text"])
                table.vobject = vobj
                if table.row_num > 0 and table.col_num > 0:
                    if anchors:
                        resolve_anchors(table, anchors)
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
