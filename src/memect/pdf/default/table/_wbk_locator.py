
import io
import logging
import os
import re
from logging import Logger
from pathlib import Path
from typing import Any, Final, Iterable, Mapping, Self, Sequence, cast, override

import PIL
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont
from memect.base import lists, utils

type BBox=tuple[int,int,int,int]

def to_bq(s:str)->tuple[str,str]:
    """
    s:任意字符
    半角字符和全角字符，返回(半角字符，全角字符)，如果没有，就返回原始的字符
    """
    if len(s)!=1:
        raise ValueError(f'只能够为1个字符:{s}')
    c = ord(s)
    if 0x21<= c <= 0x7E:
        # 半角
        return (chr(c), chr(c+65248))
    elif 0xFF01 <= c <= 0xFF5E:
        # 全角
        return (chr(c-65248), chr(c))
    elif c == 32 or c == 12288:
        # 空格
        return (chr(32), chr(12288))
    elif s=='”' or s=='“':
        #双引号的简化
        return ('"','"')
    else:
        return (s,s)
    
def concat_bbox(objs:Iterable[Any])->BBox|None:
    bboxes:list[Sequence[int]]=[]
    for obj in objs:
        if isinstance(obj,(tuple,list)):
            bboxes.append(obj) # type: ignore
        elif hasattr(obj,'bbox'):
            b = getattr(obj,'bbox')
            if b is not None:
                bboxes.append(b)
        elif isinstance(obj,dict):
            b = obj.get('bbox') # type: ignore
            if b is not None:
                bboxes.append(b)
        else:
            raise ValueError(f'不支持的类型:{type(obj)}')
    if not bboxes:
        return None
    x0=min(b[0] for b in bboxes)
    x1=max(b[2] for b in bboxes)
    y0=min(b[1] for b in bboxes)
    y1=max(b[3] for b in bboxes)
    return (x0,y0,x1,y1)

def over_x(b1:BBox,b2:BBox,d:int=5)->bool:
    #     [--b1]
    #[----b2]
    d=min(d,b1[2]-b1[0],b2[2]-b2[0])
    if b1[2]-b2[0]>=d and b2[2]-b1[0]>=d:
        return True
    else:
        return False

def over_y(b1:BBox,b2:BBox,d:int=5)->bool:
    d=min(d,b1[3]-b1[1],b2[3]-b2[1])
    if b1[3]-b2[1]>=d and b2[3]-b1[1]>=d:
        return True
    else:
        return False
    
def to_int(b:BBox)->BBox:
    return (int(b[0]),int(b[1]),int(b[2]),int(b[3]))

def normalize(s:str)->str:
    #s = re.sub(r'[\s]','',s)
    buf:list[str]=[]
    #全部使用半角字符
    for c in s:
        if c.isspace():
            pass
        else:
            a,b = to_bq(c)
            buf.append(a)
    return ''.join(buf)


class Span:
    def __init__(self,text:str,bbox:BBox,*,fixed:bool=False):
        super().__init__()
        self._text=text
        self.text2 = normalize(text)
        self.raw_text:Final = text
        self.bbox=bbox
        self.owner:Any=None
        """表示span被谁用了，如：Cell或者Text"""
        self.fixed=fixed
        """True表示这个span被修改过"""

    @property
    def text(self)->str:
        return self._text
    
    @text.setter
    def text(self,t:str):
        self._text=t
        self.text2 = normalize(t)
        self.fixed=True


    @property
    def used(self)->bool:
        return self.owner is not None

    def join(self,*spans:Self)->Self:
        #支持：span1  span2  => span3
        if not spans:
            raise ValueError('')
        
        new_spans = [self,*spans]
        t = ''.join(s.text for s in new_spans)
        bbox = concat_bbox(new_spans)
        assert bbox is not None
        return self.__class__(t,bbox,fixed=True)
    
    
    @classmethod
    def join_all(cls,spans:Sequence[Self])->Self:
        if not spans:
            raise ValueError('')
        t = ''.join(s.text for s in spans)
        bbox = concat_bbox(spans)
        assert bbox is not None
        return cls(t,bbox,fixed=True)
    
    def cut(self,index:int,*,normalized:bool=False)->tuple[Self|None,Self|None]:
        """index是相对没有归一化之前的"""
        if normalized:
            #表示index使用的是text2，下面转换为相对text的index
            n=0
            for i,c in enumerate(self.text):
                if c.isspace():
                    n+=1
                else:
                    if i-n==index:
                        index=i
                        break
        
        if index==0:
            return None,self
        elif index==len(self.text):
            return self,None
        else:
            char_width = (self.bbox[2]-self.bbox[0])/len(self.text)
            x0,y0,x1,y1 = self.bbox

            x = x0+int(char_width*index)
            b1=(x0,y0,x,y1)
            b2=(x,y0,x1,y1)
            return self.__class__(self.text[0:index],b1,fixed=True),self.__class__(self.text[index:],b2,fixed=True)
    

class Cell:
    def __init__(self,data:Any):
        super().__init__()
        #有些返回float而不是字符串
        if not isinstance(data,(str,dict)):
            data=str(data)

        if isinstance(data,str):
            self._text = data
        else:
            self._text = cast(str,data['text'])
        
        self.raw_text:Final = self._text
        self.bbox:BBox|None = None
        #为了方便比较，去掉所有的空格，全部转换为半角
        self.text2 = normalize(self._text)
        #self.col_text:str=self.text2
        #"""表示列的文本，仅仅为表头的cell才会设置"""
        self.spans:list[Span]=[]
        self.col_index:int=0
        self.row_index:int=0
        self.data:Any=data

        self.fake:bool=False
        """True表示这个为伪造的"""
    
    @property
    def text(self)->str:
        return self._text
    
    @text.setter
    def text(self,t:str):
        self._text = t
        self.text2 = normalize(t)
    
    def match(self,span:Span)->bool:
        if self.text==span.text:
            #最匹配，完全不需要转换
            return True
        elif self.text2==span.text2:
            #归一化后匹配
            return True
        else:
            #模糊匹配？
            #如果是非数字，前面几个字？
            #如果是第一列，重复文本的可能性比较小，都是科目名字
            return False
    
    def jsonify(self)->dict[str,Any]:
        obj:dict[str,Any]
        if isinstance(self.data,str):
            obj={
                'text':self.data
            }
            obj['bbox']=self.bbox
        else:
            obj={**self.data}
            obj['bbox']=self.bbox
        
        #如果是伪造的，就使用设置的
        if self.fake:
            obj['text']=self.text
        return obj


class Match:
    def __init__(self,row_index:int,match_count:int,spans:list[Span]):
        super().__init__()
        self.row_index:int=row_index
        """对应的spans的行的index"""
        self.spans:list[Span]=spans
        """匹配的span行"""
        self.match_count:int=match_count
        """表示有多少个匹配"""

class Row:
    def __init__(self,cells:list[Cell]):
        super().__init__()
        self._index:int=0
        #去掉空白的，如果正行都是空白，也就是没有cells
        self.cells=[c for c in cells if len(c.text2)>0]
        self.raw_cells = cells
        self.matchs:list[Match]=[]

        self.used_match:Match|None=None
        """表示使用这个匹配"""
        self.span_row_index:int|None=None
        """表示对应的spans的行的index"""
    
    def is_empty(self)->bool:
        return len(self.cells)==0

    @property
    def index(self)->int:
        return self._index
    
    @index.setter
    def index(self,i:int):
        self._index = i
        for cell in self.raw_cells:
            cell.row_index=i


    def match(self,span_rows:list[list[Span]],strict:bool=True):
        """
        找到完全匹配的行
        """
        #print('===>',[c['text'] for c in self.cells])
        for i,spans in enumerate(span_rows):
            if len(self.cells)==len(spans):
                n=0
                for c1,c2 in zip(self.cells,spans):
                    if c1.text2==c2.text2:
                        #先最匹配，忽略空格和半角/全角的区别
                        #print(c1['text'],'==>',c2['text'])
                        n+=1
                    else:
                        #模糊匹配
                        pass
                
                if strict and n==len(self.cells):
                    #这里先使用严格的方式
                    self.matchs.append(Match(i,n,spans))
            else:
                #如果个数不一致，如：少了span，或者span间距过大，变成了2个，或者多识别了，或者少识别了
                #如果是太紧密了，需要切割的
                #如果是距离稍微远了点，需要合并的
                #如果是跨行了，需要合并的
                pass

    def done(self)->bool:
        """判断该行是否已经全部获得bbox了"""
        for cell in self.cells:
            if cell.bbox is None:
                return False
        return True
    
    def apply(self,match:Match):
        #self.used_match=match
        assert self.span_row_index is None
        self.span_row_index = match.row_index
        for cell,span in zip(self.cells,match.spans):
            assert cell.bbox is None
            assert len(cell.spans)==0
            assert not span.used
            cell.bbox = span.bbox
            cell.spans.append(span)
            span.owner=cell
    
    def jsonify(self):
        return [cell.jsonify() for cell in self.raw_cells]

class Column:
    def __init__(self):
        super().__init__()
        self._index:int=0

class ImageInfo:
    def __init__(self,file:str|Path|bytes,size:tuple[int,int],rotate:int=0):
        super().__init__()
        self.file=file
        """图片文件的路径或者内容"""
        self.size=size
        """图片识别使用的size，如果有旋转，为旋转后的，span的坐标相对这个size"""
        self.rotate=rotate
        """顺时针旋转多少度"""

class Text:
    def __init__(self,data:Any):
        super().__init__()
        if isinstance(data,str):
            self.text = data
        else:
            self.text = data['text']
        
        self.text2 = normalize(self.text)
        self.bbox:BBox|None=None
        self.data = data
        self.spans:list[Span]=[]
    
    def jsonify(self)->Any:
        obj:dict[str,Any]={}
        if isinstance(self.data,dict):
            obj.update(self.data) # type: ignore
        else:
            obj['text']=self.text
        
        obj['bbox']=self.bbox
        return obj
            
class Table:
    _logger:Logger = logging.getLogger(f'{__module__}.{__qualname__}')
    def __init__(self,data:dict[str,Any],*,image:ImageInfo):
        super().__init__()
        self.data = data
        """多模态返回的表格内容"""
        self.image = image
        """图片的信息，主要是给调试用"""
        self.rows:list[Row]=[]
        """去掉空白行的行"""
        self.raw_rows:list[Row]=[]
        """原始的行"""
        self.raw_spans:list[Span]=[]
        self.texts:dict[str,Text|None]={}

        self._setup()


    def _setup(self):
        #表示有哪些列
        rows:list[list[Any]] = self.data['rows']

        self.row_num = len(rows)+1
        self.col_num = len(header)

        #检查一下看看，毕竟是多态大模型返回的
        col_num = len(header)
        error_row_count=0
        #auto_fix=True

        for i,row in enumerate(rows):
            if len(row)!=col_num:
                #在使用本地部署的qwen72b，有时候会出现这个问题，官网（百炼）的没有
                #TODO 这里调整一下？
                #如何调整，多识别了列？
                self._logger.warning('多态模型返回的行的列数不一致，col_num=%s,cells=%s,row_index=%s,row=%s',col_num,len(row),i,row)
                error_row_count+=1
        
        if error_row_count==len(rows) and len(rows)>0:
            #如果所有的行列数都不匹配，可能是表头少了一个空白的列，这里尝试修复
            sizes = set(len(row) for row in rows)
            if len(sizes)==1 and len(rows[0])==col_num+1:
                #所有的列数都相同，且比表头多一列
                for i in range(len(rows[0])):
                    column:list[str]=[]
                    for row in rows:
                        cell = row[i]
                        if isinstance(cell,str) and cell=='':
                            column.append(cell)
                        else:
                            break
                    
                    if len(column)==len(rows):
                        #如果全部都是空字符串，插入一列
                        header.insert(i,'')
                        self._logger.warning('插入空白的列,col_index=%s',i)
                        break


        def build_row(objs:list[Any])->Row:
            fake=False
            if len(objs)!=col_num:
                self._logger.warning('自动修正错误的行:row=%s',objs)
                new_objs=['']*col_num
                new_objs[0]=objs[0]
                objs=new_objs
                fake=True
            cells:list[Cell]=[]
            for i,obj in enumerate(objs):
                cell = Cell(obj)
                cell.col_index=i
                cell.fake=fake
                cells.append(cell)
            return Row(cells)

        self.header = build_row(header)
        #TODO 有些跨页表格的可能没有表头（因为刚好跨页，又没有表头重复）
        #返回的表头不一定是第一行（可能就是虚构，或者乱生成的，提示词太难控制）
        #如果是这种，可以把第一行注释掉
        self.raw_rows.append(self.header)
        for row in rows:
            self.raw_rows.append(build_row(row))
        
        for row in self.raw_rows:
            if not row.is_empty():
                self.rows.append(row)
        
        for i,row in enumerate(self.rows):
            row.index=i
        

        self._adjust_header()
        



    def fill(self,spans:list[Span])->dict[str,Any]:
        """根据提供的spans进行定位，然后把用掉的span删除，如果切分了span，也添加到这个spans集合中"""

        debug=utils.Debug({'info':True,'gui':True})
        #debug.enable=False

        col_num = len(self.header.raw_cells)
        error_row_indexes:list[int]=[]
        for i,row in enumerate(self.raw_rows):
            #包括表头
            if len(row.raw_cells)!=col_num:
                #i==0表示表头，这里调整一下，针对原始的rows
                error_row_indexes.append(i-1)



        self.raw_spans = list(spans)

        if len(error_row_indexes)==0:
            self._fill(spans)
        else:
            self._logger.warning('因为存在单元格个数不一致的行，跳过不匹配bbox，个数不一致的行有:%s',error_row_indexes)

        #清除用掉的spans
        lists.remove2(spans,lambda i,spans:spans[i].used)
        


        if True:
            #检查是否写错程序了
            for row in self.rows:
                for cell in row.cells:
                    for span in cell.spans:
                        if not span.used:
                            raise RuntimeError(f'编程错误，span没有设置owner,span={span.text}')
            for k,t in self.texts.items():
                if t is not None:
                    for span in t.spans:
                        if not span.used:
                            raise RuntimeError(f'编程错误，span没有设置owner,span={span.text}')

        if debug.when(info=True):
            #就是完全没有匹配的行
            debug.print_group('start remain rows','end remain rows',[row for row in self.rows if not row.done()],lambda i,row:[c.text for c in row.cells])

        if debug.when(gui=True):
            self._draw('final',spans=spans,rows=self.rows,texts=[t for t in self.texts.values() if t is not None])

        new_data:dict[str,Any]={}
        #先复制
        new_data.update(self.data)

        for k,v in self.texts.items():
            if v is not None:
                new_data[k]=v.jsonify()
            else:
                new_data[k]=None

        #替换
        new_data['header']=self.header.jsonify()
        #去掉表头
        new_data['rows']=[row.jsonify() for row in self.raw_rows[1:]]
        

        return new_data

    def done(self)->bool:
        return all(row.done() for row in self.rows)


    def _get_column(self,col_index:int)->list[Cell]:
        return [row.raw_cells[col_index] for row in self.rows]
    
    def _fill(self,spans:list[Span]):
        pass

    def _adjust_header(self):
        #原文
        #   [aaaaa]
        #[合并][母公司]
        #合并/aaaa，而不是 aaaa/合并，因为可能前面的概率更高，更常见
        #[][][][] =>存在重复
        debug = utils.Debug({'info':True})
        #debug.enable=False
        
        i=1
        while i<len(self.header.cells):
            c1 = self.header.cells[i-1]
            c2 = self.header.cells[i]
            #正常情况是:'aa/b1','aa/b2'
            #但是有些可能生成错了，变成了：b1/aa,b2/aa
            #现在先调整正确
            a=c1.text2.split('/')
            b=c2.text2.split('/')
            if len(a)==len(b) and len(a)==2:
                
                if a[0]==b[0]:
                    #aa/b1,aa/b2
                    c1.text=a[1]
                    c2.text=b[1]
                    if debug.when(info=True):
                        debug.print(f'{a},{b} => {c1.text},{c2.text}')
                elif a[1]==b[1]:
                    #多态模型返回的结果错误了，因为表格没有使用常见的方式设计
                    # [期末余额]
                    #[合并][母公司]
                    #合并/期末余额，母公司/期末余额
                    c1.text=a[0]
                    c2.text=b[0]
                    if debug.when(info=True):
                        debug.print(f'{a},{b} => {c1.text},{c2.text}')
                else:
                    pass
            i+=1

    def _draw(self,title:str,*,bboxes:list[BBox]|None=None,spans:list[Span]|None=None,span_rows:list[list[Span]]|None=None,rows:list[Row]|None=None,method:int=1,texts:list[Text]|None=None):
        
        if isinstance(self.image.file,bytes):
            fp = io.BytesIO(self.image.file)
        else:
            fp = self.image.file
        
        #字体大小，就使用固定了，因为测试为了显示文字，就不这么精确了
        
        images:list[tuple[str,PIL.Image.Image]]=[]
        with PIL.Image.open(fp) as img:
            img = img.convert('RGB')
            if self.image.rotate!=0:
                img = img.rotate(-self.image.rotate,expand=True)

            #因为bbox是相对这个size，所以先resize
            img = img.resize(self.image.size)

            if img.width<img.height:
                fontsize=int(img.width/100)
            else:
                fontsize=int(img.width/150)
            fontsize=max(fontsize,5)
            font = PIL.ImageFont.truetype(os.path.dirname(__file__)+'/AlibabaPuHuiTi-3-35-Thin.ttf',fontsize)
            

            draw = PIL.ImageDraw.Draw(img)
            images.append((title,img))
            #在原图中
            if bboxes:
                for bbox in bboxes:
                    #使用红色
                    draw.rectangle(bbox,outline=(255,0,0),width=2)

            if spans:
                img2 = img.copy()
                draw2 = PIL.ImageDraw.Draw(img2)
                draw2.rectangle((0,0,img2.width,img2.height),fill=(255,255,255))
                for span in spans:
                    draw2.text(span.bbox[0:2],span.text,font=font,fill=(0,0,0))
                    if span.fixed:
                        #蓝色
                        color=(0,0,255)
                    else:
                        #黄色
                        color=(255,255,0)
                    draw2.rectangle(span.bbox,outline=color,width=2)
                    #原图中也圈出来
                    draw.rectangle(span.bbox,outline=(255,255,0),width=2)
                
                #img2.show('spans')
                images.append(('spans',img2))
            
            if span_rows:
                img2 = img.copy()
                draw2 = PIL.ImageDraw.Draw(img2)
                draw2.rectangle((0,0,img2.width,img2.height),fill=(255,255,255))

                for i,row in enumerate(span_rows):
                    #蓝色
                    fill = (0, 255, 221) if i%2==0 else (0, 238, 255)
                    #draw2.rectangle(concat_bbox(row),outline=(0,0,255),width=2)
                    if bbox:=concat_bbox(row):
                        draw2.rectangle(bbox,fill=fill)
                        #在原图中也圈出来
                        draw.rectangle(bbox,outline=(0,0,255),width=2)

                    for span in row:
                        draw2.text(span.bbox[0:2],span.text,font=font,fill=(0,0,0))
                        #黄色
                        draw2.rectangle(span.bbox,outline=(255,0,0),width=2)
                        #在原图中也圈出来
                        draw.rectangle(span.bbox,outline=(255,255,0),width=2)

                
                images.append(('span_rows',img2))
            
            if rows or texts:
                img2 = img.copy()
                draw2 = PIL.ImageDraw.Draw(img2)
                draw2.rectangle((0,0,img2.width,img2.height),fill=(255,255,255))
                if rows:
                    for i,row in enumerate(rows):
                        fill = (0, 255, 221) if i%2==0 else (0, 238, 255)
                        if row.done():
                            #如果完全匹配了，单数使用黄色
                            if row.index%2==1:
                                fill=(255,255,0)
                            else:
                                fill=(236,164,5)
                        if bbox:=concat_bbox(row.cells):
                            #对于倾斜的会重叠
                            #draw2.rectangle(bbox,fill=fill)
                            pass
                        else:
                            #表示一个cell都没有匹配，这一行也可以简单的显示一下，如何计算大概的bbox?
                            #draw2.rectangle()
                            pass
                        for cell in row.raw_cells:
                            #使用raw_cells，为所有的，包括fake的，cells表示非空的而已
                            if cell.bbox is not None:
                                outline=(0,0,0)
                                if cell.spans:
                                    if row.index%2==0:
                                        #使用红色
                                        outline=(255,0,0)
                                    else:
                                        #蓝色
                                        outline=(0,0,255)
                                else:
                                    #表示为推理的，没有对应的span
                                    outline=None #(0,0,0)
                                
                                draw2.rectangle(cell.bbox,outline=outline,fill=fill,width=2)
                                
                                draw2.text(cell.bbox[0:2],cell.text,font=font,fill=(0,0,0))
                            else:
                                #不需要显示了，因为正行都匹配了，就显示黄色背景
                                pass
                
                if texts:
                    for t in texts:
                        if t.bbox is not None:
                            draw2.text(t.bbox[0:2],t.text,font=font,fill=(0,0,0))
                            #黄色圈起来
                            draw2.rectangle(t.bbox,outline=(0,0,255),width=2)
                        pass

                #img3.show('cells')
                images.append(('cells',img2))
            
            #使用cv2.imshow() 更好
            #img.show(title)
            #每个图片都和原图一起
            import cv2
            import numpy as np

            if method==2:
                if len(images)==1:
                    #获得时rgb格式，需要转换为bgr
                    cv2.imshow(images[0][0],np.array(images[0][1])[:,:,::-1])
                else:
                    left = np.array(images[0][1])[:,:,::-1]
                    for t,right in images[1:]:
                        right = np.array(right)[:,:,::-1]
                        a = np.hstack([left,right])
                        cv2.imshow(t,a)
            else:
                #全部合并在一起
                final_img = np.hstack([np.array(a[1])[:,:,::-1] for a in images]) 
                cv2.imshow(title,final_img)
                #BGR => RGB
                #PIL.Image.fromarray(final_img[:,:,::-1]).show('step1')

            cv2.waitKey()
            cv2.destroyAllWindows()
        

class Table1(Table):
    _logger:Logger = logging.getLogger(f'{__module__}.{__qualname__}')

    @override
    def _fill(self,spans:list[Span]):
        self._adjust_spans(spans)
        span_rows = self._split_rows(spans)
        #处理最匹配的行
        self._step1(self.rows,span_rows)
        #处理最匹配的行之间的行
        self._step2(self.rows,span_rows)
        #处理其他的行（也就是头尾的行）
        self._step3(self.rows,span_rows)

        #再努力的匹配一下还没有任何匹配的行
        self._step4(self.rows,span_rows)

        #处理只匹配了部分cell的行（根据列对齐，有span）
        self._step5(self.rows,span_rows)

        #处理只匹配了部分cell的行（根据列对齐，没有span，计算一个大概的span，通常为很小的字符串，ocr连区域都没有识别出来）
        self._step6(self.rows,span_rows)

        #处理title和unit
        self._step7(self.rows,span_rows)

    def _adjust_spans(self,spans:list[Span]):
        #对span进行处理，如：简单的修正错别字，合并

        #原点为左上角
        #spans.sort(key=lambda span:span.bbox[1])

        debug=utils.Debug({'info':True,'gui':True})
        #debug.enable=False

        original_spans = spans[:]

        span_rows = self._split_rows(spans)



        def handle_cell(cell:Cell,start:int,span_rows:list[list[Span]])->int:
            for i in range(start,len(span_rows)-1):
                span1 = span_rows[i][0]
                span2 = span_rows[i+1][0]

                #如果有错别字的，就无法匹配了
                #或者有些分割了字符的
                s = span1.text2+span2.text2
                if s==cell.text2:#or cell.text2.startswith(span1.text2):
                    #TODO 如果存在错字，多字，少字等，就无法合并了，如何处理？
                    #因为在这里处理不需要计算字符的宽度，所以行合并也可以，先简化了
                    span3=span1.join(span2)
                    #替换，方便后续处理
                    span3.text=cell.text
                    span3.text2=cell.text2
                    lists.replace(spans,[span1,span2],[span3])
                    if debug.when(info=True):
                        debug.print('merge spans,case2',[span1.text,span2.text],'to',[span3.text])
                    return i+2
            return start
        
        for span_row in span_rows:
            if len(span_row)>=2:
                #先处理简单的相连靠的很近的字符？
                span1 = span_row[0]
                span2 = span_row[1]
                if len(span1.text2)<=2 and span2.bbox[0]-span1.bbox[2]<=3:
                    span3 = span1.join(span2)
                    lists.replace(spans,[span1,span2],[span3])
                    span_row[0]=span3
                    del span_row[1]

                    if debug.when(info=True):
                        debug.print('merge spans,case1',[span1.text,span2.text],'to',[span3.text])
                    
                pass
        start=0
        for row in self.rows:
            for cell in row.cells:
                if cell.col_index==0:
                    #第一列通常为科目的名字，可能跨行
                    start = handle_cell(cell,start,span_rows)
        

        if debug.when(gui=True):
            self._draw('adjust spans',spans=original_spans,span_rows=self._split_rows(spans))

    def _split_rows(self,spans:list[Span]):
        #先从上到下简单排序
        #这里坐标原点为左上角
        debug=utils.Debug({'info':True,'gui':True,'trace':False})
        #debug.enable=False

        def fetch_row(spans:list[Span],*,dy:int=4,strict:bool=False):
            #TODO 对于表头，可能存在跨行的，然后居中对齐的，如何处理？
            #识别为2行，然后跨行的呢？如
            #      [b]
            #[a]
            #      [c]
            #要么返回[a,b]或者[c]，要么返回[a][b,c]，或者[b][a][c]
            #但是mllm的识别为最底层的表头，也就是[a,c]
            row:list[Span]=[]
            row.append(spans.pop(0))
            i=0
            while i<len(spans):
                span1 = row[-1]
                span2 = spans[i]
                b1 = span1.bbox
                b2 = span2.bbox
                if b1[3]-b2[1]>=dy and (not strict or (len(row)<2 or row[-2] and row[-2].bbox[3]-b2[1]>=dy)):
                    #strict=True，表示需要和所有的都重叠，因为是连续的，只需要再和前前一个对比即可
                    #strict=True即可避免如下情况
                    #[a---]
                    #        [b--]
                    #   [c--] 
                    #=>[a,b,c]
                    row.append(span2)
                    del spans[i]
                else:
                    #已经排序过，不需要再继续
                    break
            row.sort(key=lambda span:span.bbox[0])
            #print([span['text'] for span in row])
            return row
   
        spans = sorted(spans,key=lambda span:span.bbox[1])
        rows:list[list[Span]]=[]
        
        if debug.when(trace=True):
            debug.print_group('start spans','end spans',spans,lambda i,span:[span.bbox,span.text])
        
        #为了解决居中对齐的情况，这里先预处理一下
        while spans:
            rows.append(fetch_row(spans,strict=True))

        if debug.when(info=True):
            debug.print_group('start rows','end rows',rows,lambda i,row:[s.text for s in row])

        return rows

    def _step1(self,rows:list[Row],span_rows:list[list[Span]]):
        #先把最匹配的行给找到，这样可以一步步减少范围，提高准确度

        debug=utils.Debug({'info':True,'gui':True})
        #debug.enable=False

        for row in rows:
            row.match(span_rows)
        
        #至少要匹配2个？
        temp = [row for row in rows if len(row.matchs)==1 and row.matchs[0].match_count==len(row.cells) and len(self.rows)>=2]
        #而且还是按顺序的
        i=1
        while i<len(temp):
            row1 = temp[i-1]
            row2 = temp[i]
            if row1.matchs[0].row_index>=row2.matchs[0].row_index:
                #出现这种情况，可能是因为识别了错别字，如：
                #原文为：[a][b][c]
                #       [a][b][d]
                #大模型识别为：
                #[a][b][c]
                #[a][b][d]
                #而ocr识别为
                #[a][b][d]    =>d应该为c，但是识别错了
                #[a][b][c]    =>c应该为d，但是识别错了
                #这个时候，row1匹配的就是span_row2，row2匹配的就是span_row1，就忽略
                #TODO 无论怎么处理都无法完美，就简单一些了，去掉
                #当然，如果考虑到当前处理的表格的特殊性，如：第一列没有重复的科目，那么，只需要第一列匹配即可？
                del temp[i-1]
                del temp[i-1]
                i=i-1
            else:
                i+=1

        for row in temp:
            row.apply(row.matchs[0])

        if debug.when(info=True):
            debug.print_group('start rows','end_rows',temp,lambda i,row:[c.text for c in row.cells])

        if debug.when(gui=True):
            self._draw('step1',span_rows=span_rows,rows=temp)

    def _step2(self,rows:list[Row],span_rows:list[list[Span]]):

        debug=utils.Debug({'info':True,'gui':True})
        #debug.enable=False

        def process_rows(start:Row,end:Row,rows:list[Row],span_rows:list[list[Span]]):
            i=start.index+1
            j=end.index
            if i>=j:
                return
            
            assert start.span_row_index is not None
            assert end.span_row_index is not None

            working_rows = rows[i:j]
            working_span_rows = span_rows[start.span_row_index+1:end.span_row_index]
            working_span_indexes:list[int] = list(range(start.span_row_index+1,end.span_row_index))
            #考虑到第一列重复的可能性很小，所以就下把第一列匹配给做了
            #[xx][xx][xx]
            #--------------
            #--------------
            #[xx][xx][xx]
            if len(working_rows)==len(working_span_rows):
                #行数匹配，就直接处理
                for row,spans,span_row_index in zip(working_rows,working_span_rows,working_span_indexes):
                    spans=spans[:]
                    if row.span_row_index is not None:
                        continue

                    for cell in row.cells:
                        if cell.bbox is not None:
                            continue
                        found=False
                        for k,span in enumerate(spans):
                            c1 = start.raw_cells[cell.col_index]
                            c2 = end.raw_cells[cell.col_index]
                            #assert c1.bbox is not None
                            #assert c2.bbox is not None
                            if c1.bbox is not None and over_x(c1.bbox,span.bbox) or c2.bbox is not None and over_x(c2.bbox,span.bbox):
                                #TODO 如果存在多个？那么就是2个bbox合并?
                                assert cell.bbox is None
                                assert len(cell.spans)==0
                                assert not span.used
                                found=True
                                cell.bbox = span.bbox
                                cell.spans.append(span)
                                span.owner = cell
                                spans = spans[k+1:]
                                break
                            else:
                                pass
                        
                        if found:
                            row.span_row_index=span_row_index
                    else:
                        #该如何处理,放宽了，找到文字相同就ok
                        pass
                    pass
                pass

        start:Row|None=None
        for row in rows:
            if row.done():
                if start is None:
                    #先处理前面的
                    start=row
                else:
                    #行和行之间的
                    process_rows(start,row,rows,span_rows)
                    start=row
            else:
                pass
        if debug.when(gui=True):
            self._draw('step2',span_rows=span_rows,rows=rows)

    def _step3(self,rows:list[Row],span_rows:list[list[Span]]):
        debug=utils.Debug({'info':True,'gui':True})
        #debug.enable=False

        def is_used(i:int):
            for row in rows:
                if row.span_row_index is not None and i>=row.span_row_index:
                    return True
            return False
        
        def get_used_row(i:int)->Row|None:
            for row in rows:
                if row.span_row_index is not None and i==row.span_row_index:
                    return row
            return None
        
        def is_match(s1:str,s2:str)->bool:
            if s1==s2:
                return True
            elif len(s1)>=4 and (s1[:-1]==s2 or s1==s2[:-1]):
                return True
            else:
                return False
        
        #先解决表头，如果没有印章，是很容易匹配的
        if self.header.span_row_index is None:
            for span_row_index,span_row in enumerate(span_rows):
                if is_used(span_row_index):
                    break
                k=0
                #开头不匹配的跳过，因为可能因为印章影响了
                matchs:list[tuple[Cell,Span]]=[]
                for cell in self.header.cells:
                    for j,span in enumerate(span_row):
                        if j>=k and cell.text2==span.text2:
                            matchs.append((cell,span))
                            k=j+1
                            break
                if len(matchs)>=2:
                    self.header.span_row_index=span_row_index
                    new_span_row = None
                    for cell,span in matchs:
                        assert not span.used
                        cell.bbox = span.bbox
                        cell.spans.append(span)
                        span.owner = cell
                        #n = span_row.index(span)
                        if new_span_row is None:
                            new_span_row = span_row[0:k]
                    
                    #把后面的span都砍掉
                    
                    #剩余的继续匹配了
                    if new_span_row:
                        for cell in self.header.cells:
                            if cell.bbox is None and cell.col_index==0:
                                #主要是印章的影响
                                while new_span_row:
                                    if cell.text2 in ('项目',) and new_span_row[0].text2 in ('项','目'):
                                        #ocr可能识别：项目，项，目，或者丢失了，或者多了几个字
                                        span = new_span_row.pop(0)
                                        assert not span.used
                                        span.owner = cell
                                        cell.spans.append(span)
                                        cell.bbox = concat_bbox(cell.spans)
                                    else:
                                        break
                                
                                #TODO 再修正一下项目的bbox，因为可能存在间距，且可能丢失了某个字

                    break



        k=0
        for row in rows:
            if row.span_row_index is not None:
                k=row.span_row_index+1
                continue
            #找到最匹配的
            for span_row_index,span_row in enumerate(span_rows):
                #判断有多少个匹配的，找到最匹配的一行
                if span_row_index<k:
                    continue

                used_row = get_used_row(span_row_index)
                if used_row is not None and row.index<=used_row.index:
                    break
                #TODO 在对比的时候，可能存在个别错别字
                if is_match(row.cells[0].text2,span_row[0].text2):
                    row.span_row_index=span_row_index
                    span = span_row[0]
                    assert not span.used
                    span.owner = row.cells[0]
                    row.cells[0].spans.append(span)
                    row.cells[0].bbox = span.bbox
                    k=span_row_index+1
                    break
        if debug.when(gui=True):
            self._draw('step3',span_rows=span_rows,rows=rows)
    
    def _step4(self,rows:list[Row],span_rows:list[list[Span]]):
        debug=utils.Debug({'info':True,'gui':True})
        #debug.enable=False

        #尽最大努力了，能够匹配的都匹配（不考虑刚好A被识别为B的情况了）

        def is_match(row:Row,span_row_index:int,span_row:list[Span])->bool:
            k=0
            pairs:list[tuple[Cell,Span]]=[]
            for cell in row.cells:
                i=k
                while i<len(span_row):
                    span = span_row[i]
                    if cell.text2==span.text2:
                        pairs.append((cell,span))
                        k=i+1
                        break
                    i+=1
            
            if len(pairs)>=2:
                row.span_row_index=span_row_index
                for cell,span in pairs:
                    assert not span.used
                    span.owner = cell
                    cell.bbox = span.bbox
                    cell.spans.append(span)
                return True
            else:
                return False
            

        def handle_rows(group:list[Row]):
            """处理连续的行"""
            i=0
            j=len(span_rows)
            if group[0].index-1>=0:
                i=rows[group[0].index-1].span_row_index
                assert i is not None
            
            if group[-1].index+1<len(rows):
                j=rows[group[-1].index+1].span_row_index
                assert j is not None
            
            #典型的应该是temp比group要多，因为存在跨行，就
            for row in group:
                #找到匹配的
                k=i
                while k<j:
                    if is_match(row,k,span_rows[k]):
                        i=k+1
                        break
                    k+=1

        #获得还没有使用到的span_rows
        pending_rows:list[Row]=[]
        for row in rows:
            if row.span_row_index is not None:
                continue
            #表示这一行还没有任何匹配，找到最多匹配的
            if not pending_rows:
                pending_rows.append(row)
            elif pending_rows[-1].index+1==row.index:
                pending_rows.append(row)
            else:
                #不连续的，先处理
                handle_rows(pending_rows)
                pending_rows = [row]
        
        if pending_rows:
            print('=========>>>')
            for row in pending_rows:
                print([c.text for c in row.cells])

            handle_rows(pending_rows)

        if debug.when(gui=True):
            self._draw('step4',span_rows=span_rows,rows=rows)

    def _step5(self,rows:list[Row],span_rows:list[list[Span]]):
        debug=utils.Debug({'info':True,'gui':True})
        #debug.enable=False


        def find_spans(cell:Cell,spans:list[Span]):
            column:list[Cell]=[]
            for row in rows:
                c2 = row.raw_cells[cell.col_index]
                if c2.bbox is not None:
                    column.append(c2)
            if not column:
                return
            bbox = concat_bbox(column)
            assert bbox is not None

            i=0
            while i<len(spans):
                span=spans[i]
                if not span.used and over_x(span.bbox,bbox,d=5):
                    #支持多个
                    span.owner = cell
                    cell.spans.append(span)
                    cell.bbox = concat_bbox(cell.spans)
                    del spans[i]
                else:
                    i+=1


        def handle_row(row:Row):
            if row.done():
                #完全匹配了
                return
            if row.span_row_index is None:
                #没有对应的行
                return
            
            #仅仅部分匹配，根据对齐找到

            #复制一个，因为会删除用掉的
            span_row = span_rows[row.span_row_index][:]
            for cell in row.cells:
                if cell.bbox is not None:
                    lists.remove(span_row,cell.spans)
                    continue
            
            for cell in row.cells:
                if cell.bbox is not None:
                    continue
                #从剩余的span中找到对齐的
                find_spans(cell,span_row)
                

        for row in rows:
            handle_row(row)
        
        if debug.when(gui=True):
            self._draw('step5',span_rows=span_rows,rows=rows)

    def _step6(self,rows:list[Row],span_rows:list[list[Span]]):
        debug=utils.Debug({'info':True,'gui':True})
        #debug.enable=False

        def get_column(cell:Cell)->list[Cell]:
            column:list[Cell]=[]
            for row in rows:
                c2 = row.raw_cells[cell.col_index]
                if c2.bbox is not None:
                    column.append(c2)       
            return column

        def handle_row(row:Row):
            if row.done():
                #完全匹配了
                return
            if row.span_row_index is None:
                #没有对应的行
                return
            
            def get_key(c1:Cell,c2:Cell)->int:
                if c1.text2==c2.text2:
                    #表示最匹配
                    return -1
                else:
                    return abs(len(c1.text2)-len(c2.text2))

            for cell in row.cells:
                if cell.bbox is not None:
                    continue
                column = get_column(cell)
                if not column:
                    continue
                column_bbox = concat_bbox(column)
                assert column_bbox is not None
                #至少有一个有bbox了
                row_bbox = concat_bbox(row.cells)
                #print('============>>>',row.span_row_index,[(c.bbox,c.text) for c in row.cells])
                assert row_bbox is not None
                #简单粗暴，直接使用该列的bbox？这样可以尽可能的包含？虽然大
                #或者使用最小的一个，因为ocr无法识别文字主要是因为模糊或者太小了
                #或者更加准确一些，找到字符个数最匹配的
                #第一个为最匹配，就是字符串相等
                column.sort(key=lambda c:get_key(cell,c))
                ref_bbox = column[0].bbox
                assert ref_bbox is not None
                bbox=(ref_bbox[0],row_bbox[1],ref_bbox[2],row_bbox[3])
                cell.bbox = bbox
                
                

        for row in rows:
            handle_row(row)

        
        if debug.when(gui=True):
            self._draw('step6',span_rows=span_rows,rows=rows)

    def _step7(self,rows:list[Row],span_rows:list[list[Span]]):
        debug=utils.Debug({'info':True,'gui':True})
        #debug.enable=False

        if self.header.span_row_index is None:
            return
        
        
        remain_span_rows = span_rows[0:self.header.span_row_index]
        if debug.when(info=True):
            debug.print('title',self.title.text if self.title else None)
            debug.print('unit',self.unit.text if self.unit else None)
            debug.print_group('span rows','span rows',remain_span_rows,lambda i,spans:[s.text for s in spans])

        texts:list[Text]=[]
        if self.title is not None:
            for i,span_row in enumerate(remain_span_rows):
                if len(span_row)==1 and span_row[0].text2==self.title.text2:
                    self.title.bbox = span_row[0].bbox
                    self.title.spans.append(span_row[0])
                    del remain_span_rows[0:i+1]
            
            texts.append(self.title)

        if self.unit is not None:
            for i,span_row in enumerate(remain_span_rows):
                span = span_row[-1]
                if span.text2==self.unit.text2 or re.search(r'金额单位|人民币|百万元|千万元',span.text2):
                    self.unit.bbox = span.bbox
                    self.unit.spans.append(span)
                    del remain_span_rows[0:i+1]
            
            texts.append(self.unit)

        if debug.when(gui=True):
            self._draw('step7',span_rows=span_rows,rows=rows,texts=texts)


class Table2(Table):
    _logger:Logger = logging.getLogger(f'{__module__}.{__qualname__}')


    @override
    def _fill(self,spans:list[Span]):
        #使用第二种方案
        #信任ocr的准确度，在图片清晰没有太多干扰的情况下，可以到达85%的准确
        self.spans = spans
        self._adjust_spans()

        #因为多态模型返回的表头可能是虚构的，所以分开处理。多态模型后续升级，希望不再出现这个问题
        self._step1()
        self._step2()
        self._step3()
        self._step4()
        self._match_texts()

    def _adjust_spans(self,*,strict:bool=True):
        debug = utils.Debug({'info':True,'gui':True})
        #debug.enable=False
        #增加删除等，都是在spans对象中
        spans:Final = self.spans
        #如果有两行的文本，先合并为一个
        #可以去掉一些太大的，如：来自印章的span
        #把一些分开的合并在一起
        #把一些合并在一起的切开
        #修正一些常见的识别错误，方便后续匹配等
        if debug.when(gui=True):
            self._draw('original spans',spans=spans) 

        #self._adjust1(strict=True)
        #self._adjust2()
        #self._adjust3()
        #self._adjust4()

        if debug.when(gui=True):
            self._draw('adjust spans',spans=spans)        

    def _adjust1(self,*,strict:bool=True):
        """合并多行的文本，前提是文本使用了换行符连接"""
        debug = utils.Debug({'info':True,'gui':True})
        #debug.enable=False
        #增加删除等，都是在spans对象中
        spans:Final = self.spans
        fixed_spans:list[Span]=[]

        def get_spans(text:str,index:int,spans:list[Span],*,strict:bool=True)->list[Span]|None:
            #多行文本，有些情况下多态模型返回多行文本会使用换行符(\n)，所以这里分离
            lines = text.splitlines()
            if len(lines)<=1:
                return None
            
            lines = [normalize(line) for line in lines]
            #先找到第一行，然后其他
            line = lines.pop(0)
            i=index
            while i<len(spans):
                span = spans[i]
                if span.text2 == line:
                    other_spans = find_other(span,lines,i+1,spans,strict=strict)
                    if other_spans:
                        return [span,*other_spans]
                    #也可以继续，因为可能存在第一行相同的，但是现在先不继续
                    break
                i+=1
        
        def find_other(span:Span,lines:list[str],index:int,spans:list[Span],*,strict:bool=True)->list[Span]:
            other_spans:list[Span]=[]
            i=index
            while i<len(spans) and len(lines)>0:
                span2 = spans[i]
                bbox = concat_bbox([span,*other_spans])
                assert bbox is not None
                if span2.bbox[1]-bbox[3]<=15 and over_x(bbox,span2.bbox,d=5) and span2.text2==lines[0]:
                    #[span]
                    #[span2]
                    other_spans.append(span2)
                    lines.pop(0)
                    i+=1
                elif strict and span2.bbox[1]-bbox[3]>20:
                    #[span]
                    #[span2]
                    #如果间距过大，就不需要继续了
                    break
                else:
                    i+=1
            return other_spans

        def fix(text:str,index:int,spans:list[Span],*,strict:bool=True)->Span|None:
            temp = get_spans(text,index,spans,strict=strict)
            if temp and len(temp)>1:
                span = Span.join_all(temp)
                lists.replace(spans,temp,[span])
                return span
            else:
                return None
        
        #先排序
        spans.sort(key=lambda span:span.bbox[1])
        texts:list[Text]=[]

        
        #考虑到主要是前面的匹配
        index=0
        for text in texts:
            span=fix(text.text,index,spans,strict=strict)
            if strict and span is not None:
                #表示有新的span，然后从span后继续，因为已经根据y排序了，这样可以减少错误
                #当然，如果倾斜严重的，y排序就没有意义了，这个时候可以去掉下面
                index = spans.index(span)+1
                fixed_spans.append(span)
        
        if not strict:
            index=0
        for row in self.rows:
            for cell in row.cells:
                span = fix(cell.text,index,spans,strict=strict)
                if strict and span is not None:
                    index = spans.index(span)+1
                    fixed_spans.append(span)
        


        if debug.when(gui=True):
            self._draw('adjust1',spans=fixed_spans,texts=texts,rows=self.rows)     

    def _adjust2(self):
        """单位和币种可能粘连在一起"""
        debug = utils.Debug({'info':True,'gui':True})
        #debug.enable=False
        #增加删除等，都是在spans对象中
        spans:Final = self.spans
        #单位：xxx 币种：xxxx
        #判断前面10个即可，不需要判断太多
        fixed_spans:list[Span]=[]

        
        def fix1():
            spans.sort(key=lambda span:span.bbox[1])
            for span in spans[0:10]:
                m=re.fullmatch(r'(?P<unit>.*单位[:：].+)(?P<currency>币种[:：].+)',span.text2)
                if m:
                    unit = m.span('unit')
                    #currency = m.span('currency')
                    s1,s2 = span.cut(unit[1],normalized=True)
                    if s1 is not None and s2 is not None:
                        lists.replace(spans,[span],[s1,s2])
                        fixed_spans.extend([s1,s2])
                    break
        
        def fix2():
            #因为印章，经常引起“项目”识别错误
            span_rows = self._split_rows(spans)
            for span_row in span_rows[0:10]:
                #有些有2个项目的，如：
                #[项目][xxx][xxx][项目]
                n=0
                i=0
                while i<len(span_row):
                    if re.fullmatch(r'项且',span_row[i].text2):
                        span_row[i].text='项目'
                        fixed_spans.append(span_row[i])
                        n+=1
                        i+=1
                    elif i+1<len(span_row) and re.fullmatch(r'项目|项且',span_row[i].text2+span_row[i+1].text2):
                        span = Span.join_all(span_row[i:i+2])
                        span.text='项目'
                        lists.replace(spans,span_row[i:i+2],[span])
                        fixed_spans.append(span)
                        n+=1
                        i+=2
                    else:
                        i+=1
                    
                    if n>=2:
                        #超过2个就不继续了，忽略也可以
                        break
                
                if n>0:
                    break
        
        def fix3():
            #因为印章引起的个别错别字
            rules:dict[str,Any]={
                '永续债':[r'水续债']
            }

            new_rules:dict[str,re.Pattern[str]]={}
            for t,patterns in rules.items():
                new_rules[t]=re.compile('|'.join(re.escape(p) for p in patterns))
            
            for span in spans:
                for k,p in new_rules.items():
                    if p.fullmatch(span.text2):
                        span.text = k
                        fixed_spans.append(span)
                        break

        def fix4():
            pattern=re.compile(r'(法定代表|主管会计工作负责人|会计机构负责人).*')
            for span in spans[:]:
                if span not in spans:
                    continue
                m = pattern.fullmatch(span.text2)
                if m is None:
                    continue

                #删除签名印章
                i=0
                while i<len(spans):
                    span2 = spans[i]
                    if span2 is span:
                        i+=1
                        continue
                    if span2.bbox[3]>span.bbox[3]:
                        del spans[i]
                        fixed_spans.append(span2)
                    else:
                        i+=1
                
                #这个也删除
                spans.remove(span)
                fixed_spans.append(span)
        fix1()
        fix2()
        fix3()
        fix4()

        if debug.when(gui=True):
            self._draw('adjust2',spans=fixed_spans)   

    def _adjust3(self):
        """
        合并常见的几种情况，特别是表头
        """
        #合并日期，如：
        # 2023年
        #12月31日
        
        debug = utils.Debug({'info':True,'gui':True})
        #debug.enable=False
        #增加删除等，都是在spans对象中
        spans:Final = self.spans
        #单位：xxx 币种：xxxx
        #判断前面10个即可，不需要判断太多
        fixed_spans:list[Span]=[]

        spans.sort(key=lambda span:span.bbox[1])

        rules:list[Any]=[
            [re.compile(r'[0-9]{4}年'),re.compile(r'[0-9]{1,2}月[0-9]{1,2}日')],
            [re.compile(r'[0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日'),re.compile(r'[(]经重述[)]')]
        ]

        def join(span:Span,spans:list[Span])->bool:
            if not spans:
                return True
            bbox = concat_bbox(spans)
            assert bbox is not None
            if span.bbox[1]-bbox[3]<=10 and over_x(span.bbox,bbox,d=10):
                return True
            else:
                return False

        def fix(rule:list[re.Pattern[str]],index:int,spans:list[Span])->Span|None:
            

            while index<len(spans):
                i=index
                match_spans:list[Span]=[]
                for p in rule:
                    found=False
                    while i<len(spans):
                        span = spans[i]
                        #print(span.text2,p.fullmatch(span.text2),p)
                        if p.fullmatch(span.text2) and join(span,match_spans):
                            match_spans.append(span)
                            i+=1
                            found=True
                            break

                        if match_spans and span.bbox[1]-match_spans[-1].bbox[3]>=15:
                            #[span1]
                            #[span2]
                            #如果间距太大，就不需要再继续
                            break
                        i+=1
                    
                    if not found:
                        break
                
                if len(match_spans)==len(rule):
                    new_span = Span.join_all(match_spans)
                    #lists.replace(spans,match_spans,[new_span])
                    #插入到第一个的位置，这样，后续循环就只需要从这个位置+1开始即可
                    lists.insert(spans,spans.index(match_spans[0]),[new_span])
                    lists.remove(spans,match_spans)
                    return new_span
                elif len(match_spans)>0:
                    index=spans.index(match_spans[0])+1
                else:
                    return None 
            return None
                
        
        for rule in rules:
            k=0
            while k<len(spans):
                span = fix(rule,k,spans)
                if span is not None:
                    #因为span是插入到spans中的，排序后可以获得其的位置
                    k=spans.index(span)+1
                    fixed_spans.append(span)
                else:
                    break

        if debug.when(gui=True):
            self._draw('adjust3',spans=fixed_spans)     

    def _adjust4(self):
        #合并第一列中跨行的
        debug=utils.Debug({'info':True,'gui':True})
        #debug.enable=False
        #
        spans:Final = self.spans
        fixed_spans:list[Span]=[]
        rows:Final=self.rows
        #先简单的分行，即使倾斜等也不影响
        #因为中fix中会支持错位的行，这里dy可以设置大一些，即使多分成几行也不影响
        span_rows = self._split_rows(spans,strict=True,dy=8)
        matchs:dict[int,int]={}
        used_span_row_indexes:list[int]=[]

        if debug.when(info=True):
            debug.print_group('start span rows','end span rows',span_rows,lambda i,row:([span.bbox for span in row],[span.text2 for span in row]))
        
        def match(s1:str,s2:str)->bool:
            if s1==s2:
                return True
            
            #净敞口套期收益(损失以“-”号填列) 18
            #净散口套期收益(损失以“"号填列) 17
            s1=re.sub(r'[一\-]','',s1)
            s2=re.sub(r'[一\-]','',s2)
            #print('1========>>>',s1,len(s1))
            #print('2========>>>',s2,len(s2))
            if len(s1)!=len(s2):
                return False
            
            total = len(s1)
            n=0
            for c1,c2 in zip(s1,s2):
                if c1!=c2:
                    n+=1
            
            #print('===>total',total,n)
            if total>=10 and n<=2:
                #允许2个错别字
                return True
            else:
                return False
        def test_spans(text:str,spans:list[Span])->int:
            #  [span1]
            #[span2]
            span1 = spans[0]
            span2 = spans[1]
            if span2.bbox[1]-span1.bbox[3]>15:
                #表示间距过大，不需要再继续
                return 0
            #严格的可以判断对齐，现在不判断了？
            if not (span1.bbox[0]-span2.bbox[2]<=10 and span1.bbox[2]-span2.bbox[0]>=5):
                #[span1]
                #   [span2]
                if span2.bbox[0]>=span1.bbox[0]:
                    #[span1]
                    #         [span2]
                    #[span3]  =>还可以测试下一行，因为如果存在倾斜等，可能导致识别为3行
                    return -1
                else:
                    #不需要继续
                    return 0
            span_text = span1.text2+span2.text2
            #print('=====>>>',text,span1.text2,span2.text2)
            if match(text,span_text):
                #先使用严格匹配
                #如果有个别错别字，可以完全匹配90%？
                return 1
            else:
                #不需要再继续
                return 0
            
        def fix(text:str,start:int,end:int)->int:
            i=start
            while i<end-1:
                j=i+1
                while j<len(span_rows):
                    span1 = span_rows[i][0]
                    span2 = span_rows[j][0]
                    k = test_spans(text,[span1,span2])
                    if k==1:
                        if debug.when(info=True):
                            debug.print(f'step2 cell={text},spans={[span.text for span in [span1,span2]]}')
                        span = Span.join_all([span1,span2])
                        span.text = text
                        lists.replace(spans,[span1,span2],[span])
                        fixed_spans.append(span)
                        return i+2
                    elif k==0:
                        #i+=1
                        break
                    else:
                        #表示需要再测试下一个也就是span1，span3，跳过span2
                        j+=1
                
                i+=1

            return start

        def step1():
            #标记完全匹配的第一列
            i=0
            j=0
            while i<len(rows):
                row = rows[i]
                cell = row.raw_cells[0]
                if not cell.text2:
                    #如果是空白的，到下一行
                    #假如ocr识别也很准确的，也应该需要移动到下一行j+=1
                    i+=1
                    continue

                k=j
                while k<len(span_rows):
                    span_row = span_rows[k]
                    if cell.text2==span_row[0].text2:
                        if debug.when(info=True):
                            debug.print(f'step1 find match,row={i},span_row={k},cell={cell.text2}')
                        matchs[i]=k
                        used_span_row_indexes.append(k)
                        j=k+1
                        break
                    k+=1
                i+=1
        
        def step2():
 
            min_length=10
            j=0
            for i in range(len(rows)):
                if i in matchs:
                    #这一行已经匹配了
                    continue
                row = rows[i]
                cell = row.raw_cells[0]
                if debug.when(info=True):
                    debug.print(f'step2 row={i},cell={cell.text2},length={len(cell.text2)},min_length={min_length}')
                if len(cell.text2)<min_length:
                    continue
                #如果有数字的也先跳过？
                #if re.search(r'[0-9]+',cell.text2):
                    #continue
                #[xxxx]
                #   [aaa]
                #或者
                #  [xxx]
                #[aaaa]
                min_index,max_index = get_span_row_index_range(i)
                j = max(min_index,j)
                if debug.when(info=True):
                    debug.print(f'step2 row={i},cell={cell.text2},span_rows=({j},{max_index})')
                j = fix(cell.text2,j,max_index)

        def get_span_row_index_range(index:int)->tuple[int,int]:
            """
            index:表示第几行，相对rows
            返回该行可以选择的span_rows的范围
            """
            i=index
            min_index=0
            #TODO 如果是有多个表格的，可能这个会很大
            max_index=len(span_rows)
            while i>=0:
                if i in matchs:
                    min_index=matchs[i]+1
                    break
                i-=1
            i=index+1
            while i<len(rows):
                if i in matchs:
                    max_index=matchs[i]
                    break

                i+=1

            return (min_index,max_index)
        
        step1()
        step2()

        if debug.when(gui=True):
            self._draw('adjust4',spans=fixed_spans)   


    def _split_rows(self,spans:list[Span],*,strict:bool=True,dy:int=4):
        #先从上到下简单排序
        #这里坐标原点为左上角
        debug=utils.Debug({'info':True,'gui':True,'trace':False})
        #debug.enable=False

        def fetch_row(spans:list[Span],*,dy:int=4,strict:bool=False):
            #TODO 对于表头，可能存在跨行的，然后居中对齐的，如何处理？
            #识别为2行，然后跨行的呢？如
            #      [b]
            #[a]
            #      [c]
            #要么返回[a,b]或者[c]，要么返回[a][b,c]，或者[b][a][c]
            #但是mllm的识别为最底层的表头，也就是[a,c]
            row:list[Span]=[]
            row.append(spans.pop(0))
            i=0
            while i<len(spans):
                span1 = row[-1]
                span2 = spans[i]
                b1 = span1.bbox
                b2 = span2.bbox
                if b1[3]-b2[1]>=dy and (not strict or (len(row)<2 or row[-2] and row[-2].bbox[3]-b2[1]>=dy)):
                    #strict=True，表示需要和所有的都重叠，因为是连续的，只需要再和前前一个对比即可
                    #strict=True即可避免如下情况
                    #[a---]
                    #        [b--]
                    #   [c--] 
                    #=>[a,b,c]
                    row.append(span2)
                    del spans[i]
                else:
                    #已经排序过，不需要再继续
                    break
            row.sort(key=lambda span:span.bbox[0])
            #print([span['text'] for span in row])
            return row
   
        spans = sorted(spans,key=lambda span:span.bbox[1])
        rows:list[list[Span]]=[]
        
        if debug.when(trace=True):
            debug.print_group('start spans','end spans',spans,lambda i,span:[span.bbox,span.text])
        
        #为了解决居中对齐的情况，这里先预处理一下
        while spans:
            rows.append(fetch_row(spans,strict=strict,dy=dy))

        if debug.when(info=True):
            debug.print_group('start rows','end rows',rows,lambda i,row:[s.text for s in row])

        return rows
    
    def _step1(self):
        """先匹配cell中唯一的"""
        debug=utils.Debug({'info':True,'gui':True})
        #debug.enable=False

        rows:Final = self.rows
        spans:Final = self.spans

        cells:dict[str,list[Cell]]={}
        for row in rows:
            for cell in row.cells:
                if cell.text:
                    group = cells.setdefault(cell.text,[])
                    group.append(cell)
        
        unique_cells:list[Cell]=[]
        for text,group in cells.items():
            if len(group)==1:
                unique_cells.append(group[0])
        
        span_map:dict[str,list[Span]]={}
        unique_spans:list[Span]=[]
        for span in spans:
            #ocr识别容易存在错误，如：“-”，——，_，等识别为同一个
            group = span_map.setdefault(span.text2,[])
            group.append(span)
        
        for _,group in span_map.items():
            if len(group)==1:
                unique_spans.append(group[0])
        
        header_cell_texts:list[str]=[]
        for cell in self.header.cells:
            header_cell_texts.append(cell.text2)
        if len(header_cell_texts)==len(set(header_cell_texts)):
            #没有相同的cell
            handle_header=True
        else:
            handle_header=False

        for cell in unique_cells:
            #TODO 如果是header的cell，可以先不匹配
            #TODO 如果该header的cell没有重复的内容，就可以匹配
            if cell in self.header.cells and not handle_header:
                continue
            for i,span in enumerate(unique_spans):
                if span.text2==cell.text2:
                    span.owner=cell
                    cell.bbox = span.bbox
                    cell.spans.append(span)
        
        #
        if handle_header:
            #如果处理了header，可能有个别还没有处理，如：和第一列的有相同的文字，现在可以再处理，找到第一行中，剩余的cell
            header_bbox = concat_bbox(self.header.cells)
            if header_bbox is not None:
                for cell in self.header.cells:
                    if cell.bbox is None:
                        group = span_map.get(cell.text2)
                        #============印章引起的错误，暂时如此了，后续可以删除
                        if not group and cell.text2 == '实收资本':
                            #ocr识别为“资本”
                            group = span_map.get('资本')
                        #========================
                        #
                        if group:
                            for span in group:
                                #如果span在header中
                                if span.used:
                                    continue
                                if span.bbox[3]<=header_bbox[3]+5 and span.bbox[1]>=header_bbox[1]-5:
                                    span.owner=cell
                                    cell.bbox = span.bbox
                                    cell.spans.append(span)
                                    break
                    
        #对于其他的，可以根据行来判断
        #TODO 这种算法，如果刚好有个别字符串错误识别了，如：
        #原文为：     [10][5][10]
        #MLLM识别为： [10][5][10]
        #OCR识别为：  [10][10][5]     => 将导致映射错误了，10->10,10->10,5->5
        for row in rows[1:]:
            row_bbox = concat_bbox(row.cells)
            if row_bbox is None:
                continue
            for cell in row.cells:
                if len(cell.text2)<5:
                    #为了避免因为识别错误进行了错误的匹配，这样要求多一些字符
                    continue
                group = span_map.get(cell.text2)
                #print('==>group',cell.text2,cell.bbox,group)
                if cell.bbox is None and group is not None and len(group)>1:
                    #len(group)==1 前面已经匹配过
                    span_rows = self._split_rows(group)
                    for span_row in span_rows:
                        span = span_row[0]
                        if len(span_row)==1 and not span.used and over_y(row_bbox,span.bbox,d=5):
                            span.owner = cell
                            cell.bbox = span.bbox
                            cell.spans.append(span)
                            #print('======>match',cell.text2,'||',span.text2)
                            break

                
        #TODO 如果为表头，可能存在虚构的，还需要判断一下


        if debug.when(info=True):
            debug.print_group("start rows","end rows",rows,lambda i,row:(row.index,concat_bbox(row.cells),[c.text for c in row.cells]))

        if debug.when(gui=True):
            self._draw('step1',spans=spans,rows=rows)
    

    def _step3(self):
        """根据已知道的行列匹配其他的，不处理表头"""
        #对于剩余的，有两种算法
        #如果表格没有倾斜的，对span分行匹配即可
        #span_rows = self._split_rows(self.spans)
        #如果表格有倾斜的，根据列来获得

        def get_row_height()->int:
            heights:list[int]=[]
            for span in self.raw_spans:
                h = span.bbox[3]-span.bbox[1]
                if h<=5 or h>=20:
                    #太小的，如："-,"等就去掉，太大的可能为签名
                    pass
                else:
                    heights.append(h)
            if not heights:
                return 15
            else:
                return int(sum(heights)/len(heights))
            
        debug = utils.Debug({'info':True,'gui':True})
        #debug.enable=True

        rows:Final = self.rows
        #获得没有用过的，该spans可以删除用过的
        spans:Final= [span for span in self.spans if not span.used]
        spans.sort(key=lambda span:span.bbox[1])

        row_height:Final = get_row_height()



        def get_row_bbox(row_index:int)->BBox|None:
            bbox = concat_bbox(rows[row_index].cells)
            if bbox is not None:
                #获得最大？
                return bbox
            #如果没有，就计算一个，如
            #----row1----
            #----row2---- 根据row1和row3来计算row2
            #----row3----

            #x0,x1不重要，随便写一个
            x0,y0,x1,y1=0,None,100,None
            if row_index-1>=0:
                b1 = concat_bbox(rows[row_index-1].cells)
                if b1 is not None:
                    y0=b1[3]+2

            if row_index+1<len(rows):
                b2 = concat_bbox(rows[row_index+1].cells)
                if b2 is not None:
                    y1=b2[1]-2
            else:
                pass

            if y0 is not None and y1 is not None:
                return (x0,y0,x1,y1)
            elif y0 is not None:
                #往下计算一个y1
                return (x0,y0,x1,y0+row_height)
            elif y1 is not None:
                #往上计算一个y0
                return (x0,min(0,y1-row_height),x1,y1)
            else:
                return None

            
        def get_column_bbox(col_index:int)->BBox|None:
            column = self._get_column(col_index)
            bbox = concat_bbox(column)
            #可以为None，目的是获得列的bbox[0],bbox[2]即可
            return bbox
        
        def get_column_bbox2(col_index:int)->BBox|None:
            bbox = get_column_bbox(col_index)
            if bbox is not None:
                return bbox
            if col_index-1>=0 and col_index+1<self.col_num:
                bbox1 = get_column_bbox(col_index-1)
                bbox2 = get_column_bbox(col_index+1)
                if bbox1 is not None and bbox2 is not None:
                    bbox = concat_bbox([bbox1,bbox2])
                    assert bbox is not None
                    x0,y0,x1,y1 = bbox
                    x0=bbox1[2]
                    x1=bbox2[0]
                    if x1-x0>=10:
                        return (x0,y0,x1,y1)
            
            #如果依然不能够获得，根据当前的table包含的spans，进行分列，然后进行匹配，这样就可以获得丢失的列
            return None
        
        def split_columns():
            #TODO 还没有完成
            cells:list[Cell]=[]
            for row in self.rows:
                cells.extend(row.cells)
            bbox = concat_bbox(cells)
            if bbox is None:
                return
            
            spans:list[Span]=[]
            for span in self.spans:
                if span.bbox[0]>=bbox[0] and span.bbox[2]<=bbox[2] and span.bbox[3]<=bbox[3] and span.bbox[1]>=bbox[1]:
                    spans.append(span)
            
            spans.sort(key=lambda span:span.bbox[0])
            return 


        def handle(cell:Cell,force:bool=False):
            assert cell.bbox is None
            row_bbox = get_row_bbox(cell.row_index)
            column_bbox = get_column_bbox(cell.col_index)
            if column_bbox is None and force:
                #如果不能够存在的，就获得自动计算的，也就是夹在两个列中间的，这样支持因为印章的影响，
                #倒置个别列没有找到匹配的表头，然后该列有数据，可能都是“-”，无法匹配，就倒置这一列都无法匹配了
                column_bbox = get_column_bbox2(cell.col_index)

            if row_bbox is None or column_bbox is None:
                #如果不能够获得，就先不处理
                return 
            i=0
            while i<len(spans):
                span = spans[i]
                assert not span.used
                if over_x(column_bbox,span.bbox,d=5) and over_y(row_bbox,span.bbox,d=5):
                    #[span1,span2,span3] =>如果有多个，可能因为间距大些被分开
                    #但是如果是分成两行的，这里还没有处理
                    if cell.bbox is not None:
                        #如果已经有一个以上了，判断是否为
                        #[span1][span2]，如果是，可以允许
                        pass
                    span.owner = cell
                    cell.spans.append(span)
                    cell.bbox=concat_bbox(cell.spans)
                    if cell.fake:
                        #直接使用span的text，或者使用归一化后的更好？span.text2
                        cell.text = span.text
                    del spans[i]
                    #先取一个，因为下一个可能是下一行的，如：
                    #[span1][span2] =>这个可以，但是暂时还是不允许，如果需要，在前面判断
                    #[span3] =>这个可能是下一行的
                    break
                else:
                    i+=1
            
        def handle2(cell:Cell):
            row_bbox = get_row_bbox(cell.row_index)
            #这里可以使用get_column_bbox2()
            column_bbox = get_column_bbox2(cell.col_index)
            if row_bbox is None or column_bbox is None:
                #如果不能够获得，就先不处理
                return 
            
            #如果还没有找到，计算一个？
            #给一个最大的区域吗？
            n=len(cell.text)
            x0=max(0,column_bbox[0]-1)
            x1=max(0,column_bbox[2]-1)
            #如果这个跨行的，也给最大了，表示为猜测的？
            y0=max(0,row_bbox[1]-1)
            y1=max(0,row_bbox[3]-1)
            cell.bbox = (x0,y0,x1,y1)
            #可以把包含的span也添加进去？
            if re.fullmatch(cell.text2,r'-'):
                #通常为右对齐
                cell.bbox=(max(x0,x1-10),y0,x1,y1)
            else:
                pass
        

        
        
        #第一行是表头，先不匹配
        #如果一行已经有至少一个cell匹配了，就可以获得该行的bbox（大概），然后再根据列，
        #计算出该行其它的cell对应的span（因为错别字，或者表格中有多个相同内容）

        def fix1():
            for i in range(1,len(rows)):
                row = rows[i]
                for cell in row.cells:
                    if cell.bbox is None:
                        handle(cell)
                #TODO 再这里再处理因为列数不一致的，实际上也可以在上一步一起处理，分开是等模型完善了，就不需要这一步了
                for cell in row.raw_cells:
                    if cell.fake and cell.bbox is None:
                        handle(cell)
        
        #处理列头没有span，且整列不匹配的
        
        def fix2():

            for col_index in range(self.col_num):
                bbox = get_column_bbox(col_index)
                if bbox is not None:
                    #已经处理过了
                    continue
                bbox = get_column_bbox2(col_index)
                if bbox is None:
                    #无法获得
                    continue

                for i in range(1,len(rows)):
                    row = rows[i]
                    #这里使用原始的了，且仅仅处理指定的col，因为其他的已经处理过了，没有必要再去判断
                    for cell in row.cells:
                        if cell.col_index==i and cell.bbox is None:
                            handle(cell,force=True)
                    for cell in row.raw_cells:
                        if cell.col_index==i and cell.bbox is None and cell.fake:
                            handle(cell,force=True)
            
            for col_index in range(self.col_num):
                #TODO 先伪造一个Span给表头
                header_cell = self.header.raw_cells[col_index]
                if header_cell.bbox is not None:
                    continue

                bbox = get_column_bbox2(col_index)
                if bbox is None:
                    #无法获得
                    continue

                #需要知道y0,y1，对于多级表头，这两个值比较难计算，简单的做法就是使用了后面1个吧
                prev_cell = self.header.raw_cells[col_index-1] if col_index-1>=0 else None
                next_cell = self.header.raw_cells[col_index+1] if col_index+1<self.col_num else None
                row_bbox = concat_bbox(self.header.raw_cells)
                if next_cell is not None and next_cell.bbox is not None:
                    header_cell.bbox = (bbox[0],next_cell.bbox[1],bbox[2],next_cell.bbox[3])
                elif prev_cell is not None and prev_cell.bbox is not None:
                    #用前一个?
                    header_cell.bbox = (bbox[0],prev_cell.bbox[1],bbox[2],prev_cell.bbox[3])
                elif row_bbox is not None:
                    #算了，获得整行的
                    header_cell.bbox = (bbox[0],row_bbox[1],bbox[2],row_bbox[3])
                else:
                    pass
            
            


        def fix3():
            #对于一些没有span的，如："-"，自动猜测一个
            #和前面一样，只是这里处理的是没有对应的span，因为可能太小，ocr无法识别，所以这里就推理一个bbox
            for i in range(1,len(rows)):
                row = rows[i]
                for cell in row.cells:
                    if cell.bbox is None:
                        #没有对应的span，就只能够猜测一个了
                        handle2(cell)
        
        fix1()
        fix2()
        fix3()
        #经过上面3步后，多数的行都已经匹配了
        
        if debug.when(gui=True):
            #显示剩余的spans
            self._draw('step3',spans=spans,rows=rows)
            

    def _step2(self):
        """匹配表头"""
        debug=utils.Debug({'info':True,'gui':True})
        #debug.enable=True

        rows:Final = self.rows
        
        #表头存在跨行，跨列，可能如下返回
        #原文：
        #   [----合并----]
        # [aaaaa][bbbbbb]
        #合并/aaaa
        #合并/bbbb

        #原文
        #   [aaaaa]
        #[合并][母公司]
        #合并/aaaa，而不是 aaaa/合并，因为可能前面的概率更高，更常见


        def do_match(cells:list[Cell],spans:list[Span],reverse:bool=True)->bool:            
            #因为前面第一个“项目”可能经常因为印章引起错误，从后面对比
            #-------------------------
            #[合并][母公司]|[合并][母公司]
            matchs:list[tuple[Cell,Span]]=[]
            for span,cell in zip(reversed(spans) if reverse else spans[:],reversed(cells) if reverse else cells[:]):
                if span.text2==cell.text2:
                    matchs.append((cell,span))
                else:
                    break
            if len(matchs)>=2:
                #如果有2个匹配，就足够了
                for cell,span in matchs:
                    span.owner=cell
                    cell.bbox = span.bbox
                    cell.spans.append(span)
                    #删除匹配了的
                    cells.remove(cell)
                return True
            else:
                return False
        


        spans=[span for span in self.spans if not span.used]
        #当存在倾斜就容易多行重叠
        span_rows = self._split_rows(spans,dy=8)

        #表示已经知道行的bbox
        bbox:BBox|None=None
        #去掉第一行，也就是表头
        for row in rows[1:]:
            bbox = concat_bbox(row.cells)
            if bbox is not None:
                break

        cells:list[Cell]=[]
        for cell in self.header.cells:
            if cell.bbox is None:
                cells.append(cell)
        
        #现在可以先根据列匹配，目前只需要匹配一次
        for reverse in [True]:
            if not cells:
                break
            n=0
            for span_row in span_rows[0:10]:
                b2 = concat_bbox(span_row)
                assert b2 is not None
                #[span_row]
                #----------------
                #[bbox]
                #如果存在倾斜且多级表头，span_row可能和bbox（也就是第一行）重叠，所以需要增加误差
                if debug.when(info=True):
                    debug.print([c.text for c in cells],[s.text for s in span_row])
                    debug.print(f'bbox={bbox},span_row_bbox={b2}')
                if bbox is not None and b2[1]>=bbox[1]:
                    #不需要再继续了
                    break

                #TODO 多级表头还需要继续匹配下一行
                if do_match(cells,span_row,reverse):
                    n+=1
                    #break
                if len(cells)<=1 or n>=1:
                    #目前匹配一次就足够了，且只有一个也不需要继续了，因为至少需要匹配2个
                    break
            
        if debug.when(gui=True):
            self._draw('step2',spans=spans,rows=rows)

    def _step4(self):
        """根据已经知道的行列匹配剩余的表头"""
        debug=utils.Debug({'info':True,'gui':True})
        #debug.enable=True

        def get_column_bbox(col_index:int)->BBox|None:
            column = self._get_column(col_index)
            bbox = concat_bbox(column)
            #可以为None，目的是获得列的bbox[0],bbox[2]即可
            return bbox
        

        def get_spans(bbox:BBox,spans:list[Span]):
            include_spans:list[Span]=[]
            for span in spans:
                dy=min(span.bbox[3]-span.bbox[1],bbox[3]-bbox[1],8)
                dx=min(span.bbox[2]-span.bbox[0],10)
                if over_y(span.bbox,bbox,d=dy) and over_x(span.bbox,bbox,d=dx):
                    include_spans.append(span)
            return include_spans
        #如果还有，就根据列对齐
        row_bbox = concat_bbox(self.header.cells)
        spans = [span for span in self.spans if not span.used]
        if row_bbox is not None:
            for cell in self.header.cells:
                if cell.bbox is not None:
                    continue
                if (column_bbox:=get_column_bbox(cell.col_index)) is not None:
                    x0=column_bbox[0]
                    x1=column_bbox[2]
                    y0=row_bbox[1]-2
                    y1=row_bbox[3]+2
                    temp = get_spans((x0,y0,x1,y1),spans)
                    if debug.when(info=True):
                        debug.print(f'cell={cell.text},spans={[s.text for s in temp]}')
                    if temp:
                        cell.spans.extend(temp)
                        cell.bbox = concat_bbox(cell.spans)
                        for span in temp:
                            span.owner = cell

        if debug.when(gui=True):
            self._draw('step4',spans=spans,rows=self.rows)

    def _match_texts(self):

        debug=utils.Debug({'info':True,'gui':True})
        #debug.enable=False

        #获得所有没有匹配的spans
        spans:Final=[span for span in self.spans if not span.used]
        spans.sort(key=lambda span:span.bbox[1])
        #TODO 可以仅仅取前面几行就可以
        texts:list[Text]=[]


        
        #处理列数不一致的行，找到对的span给填进去即可
        for row in self.rows:
            for cell in row.raw_cells:
                if cell.fake:
                    #如果是伪造的，在这里匹配对应的span
                    pass
        if debug.when(gui=True):
            self._draw('find title and unit',spans=spans,span_rows=None,rows=self.rows,texts=texts)
        


class Locator:
    def __init__(self):
        super().__init__()
    
    def process(self,file:str|Path|bytes,page:Mapping[str,Any],ocr_data:Mapping[str,Any])->dict[str,Any]:
        """
        给多模态返回的数据添加位置信息（bbox）

        file: 图片的路径或者内容
        data: 多态模型返回的内容
        ocr_data: ocr识别返回的内容
        """
        #为了调试方便，记录对象
        debug=utils.Debug({'info':True})
        #debug.enable=False

        width=ocr_data['width']
        height=ocr_data['height']
        rotate = ocr_data.get('rotate',0)%360
        #spans = ocr_data['spans']
        
        #把ocr的坐标相关的数据设置到mllm返回的结果

        #TODO 同时为了让误差计算更加准确，可以先把图片缩放在一个范围内？
        #因为主要是财务报表，A4纸张大小，600*900像素
        #现在简化一点，就设置最大为860，或者1000？
        max_size=(1000,1000)
        original_size=(width,height)
        if width>max_size[0] or height>max_size[1]:
            rate = min(max_size[0]/width,max_size[1]/height)
            width = int(rate*width)
            height = int(rate*height)
        else:
            rate=1
        #为了简化计算，使用int即可
        if debug.when(info=True):
            debug.print(f'resize image from={original_size},to={(width,height)},rate={rate}')
        
        #TODO 对于分数值太低的，可能是手写的签名，去掉？
        spans:list[Span]=[]
        for s in ocr_data['spans']:
            if s['score']>=0.8:
                spans.append(Span(s['text'],to_int(tuple(a*rate for a in s['bbox']))))
            else:
                if debug.when(info=True):
                    debug.print(f'remove span={s}')
        #spans = [Span(s['text'],to_int(tuple(a*rate for a in s['bbox']))) for s in ocr_data['spans']]
        #TODO 可以先简单快速的修正一点ocr的识别错误，提高后续的处理，如：印章经常把“项目”遮挡了
        image=ImageInfo(file,(width,height),rotate)

        tables:list[Any]=[]
        table_objs:list[Table]=[]
        for obj in page['data']['tables']:
            #选择Table1或者Table2？
            table = Table2(obj,image=image)
            tables.append(table)
        
        #TODO 如果有多个表格，需要先切开，否则容易出现cell和sapn混乱
        #TODO 可以通过对象识别，目前为了简化部署，就先不用了，直接通过表名
        def find_title(table:Table,spans:list[Span])->Span|None:
            title = table.title
            if title is None:
                return None
            #TODO 如果有多行的
            text = title.text2.splitlines()[0]
            for span in spans:
                if span.text2==text or text.startswith(span.text2):
                    if debug.when(info=True):
                        debug.print(f'find title title={text},span={span.text2}')
                    return span
            return None


        #all_spans = list(spans)
        spans.sort(key=lambda span:span.bbox[1])
        for i,table in enumerate(tables):
            #会消耗用掉的spans，也会调整spans，如：合并，分离等
            table_spans = spans
            if i+1<len(tables):
                span = find_title(tables[i+1],spans)
                if span is not None:
                    index = spans.index(span)
                    table_spans = spans[0:index]
                    spans = spans[index:]
                else:
                    #TODO 如果没有标题？该如何划分
                    pass
            else:
                pass
            table_objs.append(table.fill(table_spans))

        new_page:dict[str,Any]={}
        new_page.update(page)
        new_page['width']=width
        new_page['height']=height
        new_page['rotate']=rotate
        new_page['data']={
            'tables': table_objs
        }
        #print(new_page)
        return new_page

  
def usage():
    Locator().process('a.png',{},{})
    



