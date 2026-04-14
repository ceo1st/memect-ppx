
from ..base import KDocument




from functools import cached_property
import html
import importlib
import importlib.resources
import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import (Any, ClassVar, Final, Self, Sequence, TypedDict, cast,
                    override)



from ...x2x.bbox import BBox
from ...x2x.matrix import Matrix

type _BBox = Sequence[float]  # tuple[float,float,float,float,float,float]
type _Offset = Mapping[str, float]
type _Object = dict[str, Any]


class _View(TypedDict):
    left: float
    right: float
    top: float
    bottom: float
    width: float
    height: float
    scale: float
    level:int

class _Html:
    def __init__(self,html:str):
        super().__init__()
        self._html = html

    def __str__(self)->str:
        return self._html

class HtmlTag:
    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.style: dict[str, Any] = {}
        self.classes: list[str] = []
        self.attrs: dict[str, Any] = {}
        self.children: list[Any] = []
        self.data: dict[str, Any] = {}

    def escape(self, s: str, linefeed: bool = False,space:bool=False):
        if not s:
            return ''
        s = html.escape(s)
        if space:
            s = re.sub(' ','&nbsp;',s)
        if linefeed:
            s = s.replace('\n', '&#10;')
        return s
    
    def calc(self,v:float)->str:
        return f'calc(var(--scale) * {v}px)'
    
    def set_pos(self, view:_View, offset: Mapping[str, float] | None = None):
        style = self.style
        scale: float = view['scale']
        offset = offset or {'left': 0, 'top': 0}
        #TODO 可以考虑使用var
        left: float = (view['left']-offset['left']) * scale
        top: float = (view['top']-offset['top'])*scale
        style['left'] = f'{left:.2f}px'
        style['top'] = f'{top:.2f}px'

    def set_size(self, view: _View,rotate:int=0,use_height:bool=True,use_width:bool=True):
        style = self.style
        scale: float = view['scale']
        w: float = view['width'] * scale
        h: float = view['height'] * scale
        if rotate in (90,270):
            w,h=h,w
        
        if use_width:
            style['width'] = f'{w:.2f}px'
        if use_height:
            style['height'] = f'{h:.2f}px'

    def set_index(self,index:Any):
        if isinstance(index,int):
            self.style['z-index']=index
        elif isinstance(index,dict) and isinstance(index.get('index'),int):
            #添加一个统一的偏移量
            self.style['z-index']=index.get('index')+100
        else:
            #raise ValueError(f'不支持的index类型:{index}')
            pass

    def bbox2view(self, page: Mapping[str, Any], bbox: _BBox, offset: Mapping[str, float] | None = None):
        style = self.style
        scale: float = page['view']['scale']
        offset = offset or {'left': 0, 'top': 0}
        height = page['height']
        style['left'] = '{:.2f}px'.format((bbox[0]-offset['left'])*scale)
        style['top'] = '{:.2f}px'.format((height-bbox[3]-offset['top'])*scale)
        style['width'] = '{:.2f}px'.format((bbox[2]-bbox[0])*scale)
        style['height'] = '{:.2f}px'.format((bbox[3]-bbox[1])*scale)

    def set_font(self, span: 'Span',bbox:_BBox):
        style = self.style
        scale = span.view['scale']
        # size:float = span['fontsize']*scale
        # 在html显示中，使用这个更加精确
        #TODO 如果是垂直的书写的，字体的大小不是这么计算
        
        bold: bool = span.data.get('bold', False)
        italic: bool = span.data.get('italic', False)
        underline:bool=span.data.get('underline',False)
        #删除线
        strickout:bool=span.data.get('strickout',False)

        color:Any=span.data.get('color')

        size:float
        if span.data.get('vertical'):
            #如果是垂直书写的
            #size就是
            size=(bbox[3]-bbox[1])/len(span.data['text'])*scale
            style['writing-mode']='vertical-lr'
        else:
            if len(span.data['text'])==1:
                #TODO 有些字符，如：‘-’，可能为了显示很长，返回的width就很长
                #size=max(bbox[3]-bbox[1],bbox[2]-bbox[0])*scale
                size=(bbox[3]-bbox[1])*scale
            else:
                size= (bbox[3]-bbox[1])*scale

        # ocr识别返回的可能比较小，需要调整一下
        if span.page.data.get('type')=='image':
            #TODO 或者span['ocr']==True，表示该span使用ocr
            if span.data['text'] in ('-',):
                size = max(5, size)

        # 使用等宽字体
        style['font-size'] = f'{size:.2f}px'
        # style['line-height']='{:.2f}px'.format(h_size)
        if bold:
            style['font-weight'] = 'bold'
        if italic:
            style['font-style'] = 'italic'


        if color:
            if color=='ffffff' or color==[255,255,255] or color==(255,255,255):
                #如果为白色的，这里使用黄色，因为背景可能为白色，文字所在的局部背景可能是其他颜色，但是目前不一定能够还原
                #所以暂时使用蓝色？
                #color=[0,0,255]
                pass


            if color=='000000' or color==[0,0,0] or color==(0,0,0):
                #如果是黑色的，就不需要设置了，默认为黑色？
                pass
            elif isinstance(color,str):
                style['color']=f'#{color}'
            elif len(color)==3:
                #(r,g,b)
                style['color']=f'rgb({color[0]},{color[1]},{color[2]})'
            elif len(color)==4:
                #(r,g,b,alpha)
                style['color']=f'rgba({color[0]},{color[1]},{color[2]},{color[3]})'
            else:
                pass
        else:
            #如果没有设置颜色，就是(0,0,0)
            pass

        # style['transform-origin']='0% 0%'
        # style['transform']='scaleX()'.format()

        # 使用泛字体名字即可，浏览器自动选择合适的，因为指定某个字体浏览器不一定有，或者仅仅为英文字体，中文也会选择其他的
        # 严格的说，因为在span的时候，为了方便计算，可能已经按照宽度相同划分了字符串，所以仅仅需要使用等宽字体即可

        if span.font.get('family')=='Wingdings':
            #强制使用这种
            style['font-family']='Wingdings,serif'

        useFontFamily=False
        if useFontFamily:
            strict = False
            if strict:
                #font = span.data['font']
                font = span.font
                if font.get('monospace'):
                    # 等宽字体
                    style['font-family'] = 'monospace'
                elif font.get('serif'):
                    # 衬线字体
                    style['font-family'] = 'serif'
                else:
                    # 无衬线字体
                    style['font-family'] = 'sans-serif'
            else:
                #font-family:monospace;
                style['font-family'] = 'monospace'
           
    def set_data(self,obj:Mapping[str,Any],keys:Sequence[str]):
        for key in keys:
            value = obj.get(key,None)
            if value is not None:
                if isinstance(value,bool):
                    value='true' if value else 'false'
                self.data[key]=value

    def set_title(self,obj:Mapping[str,Any],keys:Sequence[str]):
        buf:list[str]=[]
        for key in keys:
            value = obj.get(key)
            if value is not None:
                #需要转换为json吗？不需要
                buf.append(f'{key}={value}')
        
        title = '\n'.join(buf)
        self.attrs['title']=title

    def set_class_by_level(self,view:_View,base:str):
        """对于有层次结构的，可以通过奇偶级别设置不同的class，交替呈现"""
        level:int = view['level']
        if level%2==0:
            self.classes.append(f'{base}-even')
        else:
            self.classes.append(f'{base}-odd')
    
    def set_color(self,name:str,color:Any):
        if isinstance(color,str):
            self.style[name]=color
        elif len(color)==3:
            self.style[name]=f'rgb({color[0]},{color[1]},{color[2]})'
        elif len(color)==4:
            self.style[name]=f'rgba({color[0]},{color[1]},{color[2]},{color[3]})'
        else:
            #不知道的颜色
            pass

    def __str__(self) -> str:
        # <a style="" class="a,b,c" ></a>
        buf: list[str] = []
        buf.append('<')
        buf.append(self.name)

        if self.style:
            buf.append(' style="')
            for k, v in self.style.items():
                buf.append(f'{k}:{v};')
            buf.append('"')

        if self.classes:
            buf.append(' class="')
            buf.append(' '.join(self.classes))
            buf.append('"')

        def quote(v: Any):
            if v is None:
                return '""'
            v = str(v)
            return f'"{self.escape(v, linefeed=True)}"'

        if self.attrs:
            for k, v in self.attrs.items():
                buf.append(' ')
                buf.append(k)
                if v!='' and v!=None:
                    buf.append('=')
                    buf.append(quote(v))

        if self.data:
            for k, v in self.data.items():
                buf.append(' ')
                buf.append(f'data-{k}')
                buf.append('=')
                buf.append(quote(v))

        buf.append('>')

        if self.children:
            for child in self.children:
                # TODO 如果是字符串的，需要先转义吗？
                if isinstance(child, str):
                    buf.append(self.escape(child, linefeed=True,space=True))
                else:
                    buf.append(str(child))

        buf.append('</')
        buf.append(self.name)
        buf.append('>')
        return ''.join(buf)


class TObject:
    type:ClassVar[str]=''
    _logger=logging.getLogger(f'{__module__}.{__qualname__}')
    def __init__(self,page:'Page',data:dict[str,Any],*,parent:'TObject|None'=None,merged_index:int|None=None):
        super().__init__()
        self.page = page
        self.data = data
        self.bbox = data['bbox']
        self.view_bbox = self.bbox
        self.parent:TObject|None=parent

        self.read_order:int=0
        """阅读顺序，从1开始，0表示没有设置"""
        self.xobject:XObject|None=None

        self._prepare()
        self.view:_View=self._make_view(self.view_bbox)
        self.offset = {'left': self.view['left'], 'top': self.view['top']}
        self.merged_index:int|None=merged_index
        """None表示没有被合并，否则表示合并的序号"""



        self.subtype:str|None=data.get('subtype',None)
        self.id = self._make_id()
        """获得一个id，目的是可以快速的导航"""
    
    def _make_id(self)->str:
        return self.page.doc.next_id(self.type)
    @property
    def level(self)->int:
        if isinstance(self.parent,self.__class__):
            #如果是同类型的，如：Form
            return self.parent.level+1
        else:
            return 0
        
    def _prepare(self):
        pass

    def render(self)->Sequence[HtmlTag]:
        return []

    def render_read_order(self,n:int)->HtmlTag:
        """显示该对象的阅读顺序序号，1表示第一个"""
        #之所以需要单独实现，目的是不想在block中又层层递归下去，按需选择即可
        offset = self.parent.offset if self.parent else None
        tag = HtmlTag('div')
        tag.set_pos(self.view,offset=offset)
        #readOrderTag.set_size()
        tag.classes.append('read-order')
        if self.merged_index is not None:
            tag.classes.append('read-order-merged')

        tag.children.append(f'{n}')
        return tag
    
    def _get_unrotated_bbox(self,obj:Any,origin:str='center') -> BBox:
        # 如果有旋转度数，就存在points
        rotate = obj['rotate']
        points = obj['quad']
        bbox: _BBox = obj['bbox']
        cx = (bbox[2]+bbox[0])/2
        cy = (bbox[3]+bbox[1])/2
        m = M(1, 0, 0, 1, 0, 0)
        m.pretranslate((cx, cy))
        m.prerotate(-rotate)
        m.pretranslate((-cx, -cy))
        points = list(m.transforms(points))
        if origin=='left top':
            bbox2 = BBox.x_bound(points).round_x(1)
            x=bbox2[0]
            y=bbox2[3]
            m2 = M(1,0,0,1,0,0)
            m2.pretranslate((cx,cy))#((bbox2.cx,bbox2.cy))
            m2.prerotate(rotate)
            m2.pretranslate((-cx,-cy))#((-bbox2.cx,-bbox2.cy))
            x0,y0 = m2.transform((x,y))
            m3=M(1,0,0,1,0,0)
            m3.pretranslate((x0-x,y0-y))
            points = list(m3.transforms(points))
        return BBox.x_bound(points)


    #@classmethod
    def _make_view(self,bbox: _BBox,level:int=0) -> _View:
        scale = self.page.scale
        width: float = self.page.width#['width']
        height: float = self.page.height # page['height']
        # bbox:_BBox = obj['bbox']
        # 原点为左下角,转换为左上角
        return _View({
            'left': bbox[0],
            'top': height-bbox[3],
            'right': width-bbox[2],
            'bottom': bbox[1],
            'width': bbox[2]-bbox[0],
            'height': bbox[3]-bbox[1],
            'scale': scale,
            'level':level
        })

    def _set_common_data(self,tag:HtmlTag):
        tag.attrs['id']=self.id
        if self.xobject:
            #对应的共同的节点的id
            tag.data['xid']=self.xobject.node.id
            if self.merged_index is not None:
                #通过这个可以构造子节点的id
                tag.data['merged-index']=self.merged_index
                tag.classes.append(f'{self.type}-merged')
                
        tag.data['type']=self.type

    def create_object(self,type:str,data:dict[str,Any],*,parent:'TObject|None'=None,merged_index:int|None=None)->'TObject':
        if type=='figure':
            return Figure(self.page,data,parent=parent,merged_index=merged_index)
        elif type=='span':
            return Span(self.page,data,parent=parent,merged_index=merged_index)
        elif type=='line':
            return Line(self.page,data,parent=parent,merged_index=merged_index)
        elif type=='rect':
            return Rect(self.page,data,parent=parent,merged_index=merged_index)
        elif type=='form':
            return Form(self.page,data,parent=parent,merged_index=merged_index)
        elif type=='formula':
            return Formula(self.page,data,parent=parent,merged_index=merged_index)
        elif type=='text':
            return Text(self.page,data,parent=parent,merged_index=merged_index)
        elif type=='table':
            return Table(self.page,data,parent=parent,merged_index=merged_index)
        elif type=='block':
            return Block(self.page,data,parent=parent,merged_index=merged_index)
        else:
            raise ValueError(f'不支持的类型:{type},page={self.page.number}')
            #self._logger.warning('不支持的类型:%s,page=%s',type,self.page.number)
            #print(data)
            #return TObject(self.page,data,parent=parent)

    def jsonify(self)->Any:
        """输出简易版的内容"""
        return {}

def render_template(template:str,values:dict[str,Any])->str:
    replaces: list[dict[str, Any]] = []
    for k, v in values.items():
        i = template.index(k)
        j = i+len(k)
        replaces.append({
            'start': i,
            'end': j,
            'value': v
        })

    html_buf: list[str] = []
    replaces.sort(key=lambda r: r['start'])
    start = 0
    for r in replaces:
        html_buf.append(template[start:r['start']])
        html_buf.append(r['value'])
        start = r['end']

    html_buf.append(template[start:])
    return ''.join(html_buf)

class Document:
    template = importlib.resources.files(
        __package__).joinpath('doc.html').read_text('utf-8')
    doc_js = importlib.resources.files(
        __package__).joinpath('doc.js').read_text('utf-8')
    def __init__(self,data:dict[str,Any],*,pagenos:Sequence[int]|None=None,scale: float | None = None):
        super().__init__()
        self.data = data
        self.scale = scale
        self.pages:list[Page]=[]
        self.fonts:dict[str,Any]={}
        self.tree:XTree|None=None
        self.id_seqs:dict[str,int]={}
        self._prepare()
    
    def _prepare(self):
        for page in self.data.get('pages',[]):
            self.pages.append(Page(self,page,scale=self.scale))
            #TODO 如果仅仅是按页解析，就可以获得body中的对象，建立按页阅读的顺序

        
        if self.data.get('tree'):
            self.tree = XTree(self,self.data.get('tree',{}))
        
        self.fonts = self.data.get('fonts') or {}
        if 'ocr' not in self.fonts:
            self.fonts['ocr']={
                'id':'ocr',
                'family':'monospace'
            }
    
    def get_font(self,id:str)->Any:
        return self.fonts[id]

    def get_page(self,no:int)->'Page':
        for page in self.pages:
            if page.number==no:
                return page
        raise ValueError(f'不存在的页面:{no}')
    
    def render(self) -> str:
        doc = self.data
        #pdf/info/title
        pdf_info:dict[str,Any] = (doc.get('pdf') or {}).get('info') or {}
        title:str = pdf_info.get('title','') or 'doc'
        title = html.escape(title)

        # 这一步cpython比pypy快1倍多
        doc_html = self.render_pages()

        doc_data:dict[str,Any] = {
            'currentPageNumber': doc['pages'][0]['number'] if doc['pages'] else 0,
            'pages': [{'number': page['number']} for page in doc['pages']],
            'pdf_info': pdf_info,

            #跨页合并的表格，后续可能连xtext也输出？对于没有合并的，这里不输出
            'xobjects':{},
            #输出表格
            'objects':{}
        }

        #为了支持跨页表格复制，这里可以输出简化版的跨页表格数据
        #doc_data['tables']={'id1':{},'id2':{},'id3':{}}
        #如果是没有解析章节树的，从pages中获得
        #如果是章节树的，从tree中获得
        #col_span,row_span如果没有，默认为1
        #然后返回最简单的信息，就是cells=[{row_index:0,col_index:0,text:'',figures:[]}]
        #就可以支持最好的复制了
        

        
        doc_data['xobjects']={}
        if self.tree:
            #目前仅仅需要跨页的，没有跨页的不需要重复输出
            doc_data['xobjects']=self.tree.jsonify_tables()
        #输出页面的表格
        objects:dict[str,Any]={}
        for page in self.pages:
            objects.update(page.jsonify_objects())
        doc_data['objects']=objects

        outline_data = OutlineBuilder().build(self)

        # 为了安全，使用严格的替换
        values:dict[str,str] = {
            '{{title}}': title,
            "'{{outline.data}}'": json.dumps(outline_data, ensure_ascii=False),
            "'{{doc}}'": json.dumps(doc_data, ensure_ascii=False),
            '{{doc_html}}': doc_html,
            "'{{doc_js}}'":self.doc_js,
            # '{{page_numbers}}':''.join(page_numbers)
        }
        return render_template(self.template,values)

    def render_pages(self):
        #for page in doc['pages']:
            #page['html_scale']=scale
        tag = HtmlTag('div')
        tag.classes.append('doc')
        tag.attrs['id']='doc'
        tag.attrs['v-pre']=''
        for page in self.pages:
            tag.children.extend(page.render())
        return str(tag)

    def next_id(self,prefix:str)->str:
        seq = self.id_seqs.setdefault(prefix,0)
        seq+=1
        self.id_seqs[prefix]=seq
        return f'{prefix}_{seq}'

class Page(TObject):
    type:ClassVar[str]='page'
    def __init__(self,doc:Document,data:dict[str,Any],scale:float|None=None):
        
        data['bbox']=(0,0,data['width'],data['height'])

        self.doc = doc
        self.width = data['width']
        self.height = data['height']

        self.max_width=0
        self.scale= self._get_scale(scale,data['width'],max_width=self.max_width)
        """从页面转换为html的缩放比例"""
        self.number = cast(int,data.get('number'))

        super().__init__(self,data)

        #按页或者章节树解析
        self.header:Header|None = None
        self.footer:Footer|None = None
        self.body:Body|None = None
        self.footnotes:list[Footnote]=[]
        self.other:Other|None=None

        #pdf2json的结果
        self.lines:list[Line]=[]
        self.rects:list[Rect]=[]
        self.spans:list[Span]=[]
        self.figures:list[Figure]=[]
        self.tags:list[Any]=[]
        self.forms:list[Form]=[]
        
        if data.get('lines'):
            for obj in data.get('lines',[]):
                self.lines.append(Line(self,obj))
        if data.get('rects'):
            for obj in data.get('rects',[]):
                self.rects.append(Rect(self,obj))
        if data.get('spans'):
            for obj in data.get('spans',[]):
                self.spans.append(Span(self,obj))
        
        if data.get('figures'):
            for obj in data.get('figures',[]):
                self.figures.append(Figure(self,obj))
        
        if data.get('forms'):
            for obj in data.get('forms',[]):
                self.forms.append(Form(self,obj))

        if data.get('tags'):
            #for tag in data.get('tags'):
            self.tags.extend(data.get('tags',[]))
        

        
        #
        if data.get('header') is not None:
            self.header = Header(self,data.get('header',{}))
        if data.get('footer') is not None:
            self.footer = Footer(self,data.get('footer',{}))
        
        if data.get('footnotes'):
            for obj in data.get('footnotes',[]):
                self.footnotes.append(Footnote(self,obj))
        
        if data.get('body'):
            #doc.json
            self.body = Body(self,data.get('body',{}))
        
        if data.get('other'):
            self.other = Other(self,data.get('other',{}))
        
        #if data.get('background'):
            #self.background = Block(self.data.get('background'))

    def _get_scale(self,scale: float | None,width:int,max_width: int) -> float:
        if scale == 0 or scale is None:
            if max_width==0:
                return 1
            
            # 表示自动计算
            if width > max_width:
                # 如果超过1000，就需要缩放一下，避免过大
                # 而且为了方便计算，四舍五入到，更加合理的是1.59 => 1.5而不是1.6
                scale = round(max_width/1-0.05, 1)
                # scale = round(max_width/width,1)
            else:
                scale = 1
        return scale
        
    def _make_id(self)->str:
        """使用页码，这样更加容易查看"""
        return f'page_{self.number}'

    def render(self)->Sequence[HtmlTag]:
        page:dict[str,Any]=self.data
        scale:Final = self.scale
        
        def create_page_tag():
            tag = HtmlTag('div')
            tag.set_size(self.view)
            # tag.style['']
            tag.classes.append('page')
            #page-pdf or page-image
            tag.classes.append(f'page-{page.get("type", "pdf")}')
            tag.set_data(page,['number','type','width','height'])
            # 表示html页面的单位比例，1表示1个逻辑单位=1px，2表示1个逻辑单位=2px
            tag.data['html-scale'] = scale
            # 表示页面最大为多少px，避免太大浏览器看起来不方便，需要滚动，0表示不限制
            tag.data['html-max-width'] = self.max_width

            
            #pdf2json.json
            if self.body is None:
                for obj in [*self.figures,*self.rects,*self.lines,*self.spans]:
                    tag.children.extend(obj.render())
            else:
                for obj in [self.other,self.body,self.header,*self.footnotes,self.footer]:
                    if obj is not None:
                        #目前仅仅在body显示
                        render_read_orders = obj in [self.body]
                        tag.children.extend(obj.render(render_read_orders=render_read_orders))


            page_helper_tag=HtmlTag('div')
            page_helper_tag.classes.append('page-helper')
            page_helper_tag.classes.append('page-helper-hide')
            page_helper_tag.children.append(_Html('<span>👻</span>'))
            page_helper_tag.set_title(page,['number','bbox','rotate','type'])
            tag.children.append(page_helper_tag)

            #目的是当不显示内容的时候，也显示占用的空间
            placeholder_tag=HtmlTag('div')
            placeholder_tag.classes.append('page-placeholder')
            placeholder_tag.set_size(self.view)
            placeholder_tag.children.append(tag)

            #显示阅读顺序

            
            return placeholder_tag

        def create_image_tag():
            tag = HtmlTag('div')
            tag.set_size(self.view)
            
            tag.set_data(page,['number','type','width','height'])
            tag.classes.append('image')

            tag.style['position']='relative'
            # 表示html页面的单位比例，1表示1个逻辑单位=1px，2表示1个逻辑单位=2px
            tag.data['html-scale'] = scale
            # 表示页面最大为多少px，避免太大浏览器看起来不方便，需要滚动，0表示不限制
            tag.data['html-max-width'] = self.max_width
            #ocr_tag.style['display']='none'
            #TODO 如果存在旋转，使用background-image的方式就只能够使用先旋转好的图片
            use_prerotated=False
            if use_prerotated:
                #如果display:none，不会载入
                tag.style['background-image']=f'url(pages/{page["number"]}.webp)'
                tag.style['background-size']='contain'
                tag.style['background-position']='center'
            else:
                #使用image
                rotate = page.get('rotate',0)
                img_tag = HtmlTag('img')
                if rotate!=0:
                    #判断是否存在了已经先旋转好的图片，如：
                    #pages/1-rotate-90.webp  ，如果没有，就需要在做旋转的处理
                    #ocrs/1.webp
                    
                    #TODO 
                    b = page['bbox']
                    quad=[(b[0],b[1]),(b[2],b[1]),(b[2],b[3]),(b[0],b[3])]
                    bbox = self._get_unrotated_bbox({'rotate':rotate,'quad':quad,'bbox':b},'center').large.data
                    
                    img_tag.style['transform'] = f'rotate({rotate}deg)'
                    img_tag.style['transform-origin']='center'
                    img_view = self._make_view(bbox)
                    img_tag.set_pos(img_view)
                    img_tag.set_size(img_view)
                else:
                    img_tag.style['width']='100%'
                    img_tag.style['height']='100%'
                img_tag.style['position']='absolute'
                #顺时针
                

                img_tag.set_data(page,['number','rotate'])
                img_tag.attrs['src']=f'pages/{page['number']}.png'
                #img_tag.attrs['src']=f'pages/1-rotate-90.png'
                #设置这个好像没有用，还是会先载入，即使ocr_tag display:none
                #img_tag.style['display']='none'
                img_tag.attrs['loading']='lazy'

                img_tag.set_title(page,['rotate'])
                tag.children.append(img_tag)

            placeholder_tag=HtmlTag('div')
            placeholder_tag.classes.append('image-placeholder')
            placeholder_tag.set_size(self.view)
            placeholder_tag.children.append(tag)
            
            return placeholder_tag
        
        # 在最后显示可以支持事件，但是span就不能够选择了
        # render_annots()
        wrapper = HtmlTag('div')
        wrapper.classes.append('page-wrapper')
        wrapper.attrs['id']=self.id

        #设置一个页码
        wrapper.set_data(page,['number'])

        wrapper.children.append(create_page_tag())
        wrapper.children.append(create_image_tag())

        #现在不需要这个了，因为先使用了placeholder，目的是即使不显示内容，也先显示占用的空间
        lazy_load = False
        if lazy_load:
            # 开始的时候先不显示，避免>=300百页的文档渲染需要4-5秒
            wrapper.style['display'] = 'none'
        return [wrapper]

    def jsonify_objects(self)->dict[str,Any]:
        objects:dict[str,Any]={}
        if self.body is not None:
            for obj in self.body.objects:
                if isinstance(obj,Table):
                    #输出一个简单的
                    objects[obj.id]=obj.jsonify()
                elif isinstance(obj,Formula):
                    objects[obj.id]=obj.jsonify()
        return objects
    
  
class Span(TObject):
    type:ClassVar[str]='span'
    @property
    def font(self)->Any:
        return self.page.doc.get_font(self.data.get('font','ocr'))

    @override
    def render(self)->Sequence[HtmlTag]:
        #<span>xxx</span>
        data: Mapping[str, Any] = self.data

        offset = self.parent.offset if self.parent else None

        tag = HtmlTag('span')
        
        #使用BBox就可以准确还原了，虽然因为字体不一样有微小的差异，但是可以忽略了
        tag.set_pos(self.view, offset=offset)
        tag.set_index(data)
        #rotate','fontsize','vertical',
        tag.set_data(data,['tag','form','rotate'])
        #需要根据这个值设置transform:scaleX()，确保span的宽度一致
        #所以，span的字体使用什么样的都不是很重要了
        tag.set_title(data,['tag','form','bbox'])

        #tag.data['scale']=span['view']['scale']
        tag.classes.append('span')

        if data.get('rotate', 0) != 0:
            # 注意：有些字符看起来是旋转的，但是没有旋转度数，原因：嵌入pdf里面字体的字符的字形就是旋转后的结果，所以pdf渲染字符后看起来为旋转后的
            # 而获得的字符（unicode）对应的是标准的字符

            #这里获得是transform-origin='center'的中心点
            #如果需要获得transform-origin='left bottom' or 'left top'
            #必须使用transform-origin='0% 0%' 而不能够使用transform-origin='center'
            bbox = self._get_unrotated_bbox(data,'left top').large.data
            tag.style['transform'] = f'rotate({data.get("rotate")}deg)'
            tag.style['transform-origin']='0% 0%'
            
            view = self._make_view(bbox)
            tag.set_pos(view, offset=offset)

            tag.data['width']=bbox[2]-bbox[0]
            tag.data['height']=bbox[3]-bbox[1]
            tag.set_font(self,bbox)
            #print('=======>>>>',span['text'],span['bbox'],bbox,span['rotate'],tag.style)
        else:
            tag.data['width']=data['bbox'][2]-data['bbox'][0]
            tag.data['height']=data['bbox'][3]-data['bbox'][1]
            tag.set_font(self,data['bbox'])
            

        
        #在subscripts/supscripts中的span render的时候，会补充这两个属性
        if data.get('subscript'):
            tag.classes.append('subscript')

        if data.get('supscript'):
            tag.classes.append('supscript')
        
        #如果同时需要显示<span><span></span></span>
        underline:bool=data.get('underline',False)
        #删除线
        strickout:bool=data.get('strickout',False)
        if underline and strickout:
            #为了支持显示不同的颜色
            span1 = HtmlTag('span')
            span2 = HtmlTag('span')
            span1.classes.append('span-underline')
            span2.classes.append('span-line-through')
            span2.children.append(data['text'])
            span1.children.append(span2)
            tag.children.append(span1)
        else:
            if underline:
                tag.classes.append('span-underline')
            elif strickout:
                tag.classes.append('span-line-through')
            else:
                pass

            #即使在同一个span中，字符串的宽度也可能不一致，在pdf中显示的时候，有些可能还特别长
            tag.children.append(data['text'])

        return [tag]
    
    @property
    def text(self)->str:
        return self.data['text']

    
class Line(TObject):
    type: ClassVar[str] = 'line'

    @override
    def _prepare(self):
        width = self.data['width']
        x0,y0,x1,y1 = self.bbox
        if abs(x0-x1)<=1:
            x0-=width/2
            x1+=width/2
        else:
            y0-=width/2
            y1+=width/2
        self.view_bbox = (x0,y0,x1,y1)
        return super()._prepare()

    @override
    def render(self) -> Sequence[HtmlTag]:
        offset = self.parent.offset if self.parent else None
        data = self.data
        tag=HtmlTag('div')
        tag.classes.append('line')
        tag.set_pos(self.view,offset=offset)
        tag.set_size(self.view)
        tag.set_index(data)
        tag.set_data(data,['tag','form','bbox'])
        #TODO 颜色
        tag.set_color('background-color',data.get('color',(0,0,0)))
        return [tag]

class Rect(TObject):
    type: ClassVar[str] = 'rect'
    @override
    def render(self) -> Sequence[HtmlTag]:
        offset = self.parent.offset if self.parent else None
        data= self.data
        tag =HtmlTag('div')
        tag.classes.append('rect')
        tag.set_pos(self.view,offset=offset)
        tag.set_size(self.view)
        tag.set_index(data)
        #TODO 颜色，border（outline）忽略
        
        if True:#rect.get('fill'):
            if self.view['width']<=4 or self.view['height']<=4:
                #如果太细的矩形，总是使用蓝色而不是自带的颜色
                #tag.style['background-color']='blue'
                tag.set_color('background-color','blue')
            else:
                #默认为白色
                fill_color = data.get('color',(255,255,255))
                #tag.style['background-color']=f'rgb({fill_color[0]},{fill_color[1]},{fill_color[2]})'
                tag.set_color('background-color',fill_color)
        else:
            #如果没有填充，背景颜色就是透明，不能够设置div为透明，因为可能还有border
            tag.set_color('background-color','rgba(255,255,255,0)')
        
        #现在没有这个了
        if data.get('stroke'):
            stroke = data.get('stroke',{})
            tag.style['border-style']='solid'
            tag.style['border-width']=f'{stroke['width']}px'
            tag.set_color('border-color',stroke['color'])
            
        tag.set_title(data,['tag','form','bbox'])
        tag.set_data(data,['tag','form'])

        return [tag]

class Figure(TObject):
    type: ClassVar[str] = 'figure'

    @override
    def render(self) -> Sequence[HtmlTag]:
        offset = self.parent.offset if self.parent else None
        data = self.data
        tag = HtmlTag('img')
        tag.set_pos(self.view, offset=offset)
        tag.set_size(self.view)
        tag.set_index(data)

        tag.classes.append('figure')

        tag.attrs['src'] = f'images/{data["filename"]}'

        self._set_common_data(tag)

        #'objid','flip','rotate','features',
        tag.set_data(data,['tag','form'])
        tag.set_title(data,['tag','form','bbox','filename','flip','rotate','features'])

        flip = data.get('flip')
        rotate = data.get('rotate', 0)

        if flip == 'h':
            # or scaleX(-1)
            tag.style['transform'] = 'rotateY(180deg)'
        elif flip == 'v':
            # or scaleY(-1)
            tag.style['transform'] = 'rotateX(180deg)'
        elif rotate != 0:
            # 需要计算出未旋转前的坐标，然后再旋转即可
            view = self._make_view(self._get_unrotated_bbox(data).large.idata)
            tag.set_pos(view, offset)
            tag.set_size(view)
            tag.style['transform'] = f'rotate({rotate}deg)'
        else:
            pass

        return [tag]

    @override
    def jsonify(self)->Any:
        return {
            'type':'figure',
            'filename':self.data['filename']
        }
class Tag(TObject):
    type: ClassVar[str] = 'tag'
    @override
    def _render_one(self, obj: Any, *, offset: Mapping[str, float] | None = None, **kwargs: Any) -> Sequence[Any]:
        xtag = obj

        
        def make_tag(view:_View,multi:bool)->HtmlTag:
            tag:HtmlTag=HtmlTag('div')
            tag.classes.append('tag')
            if multi:
                tag.classes.append('tag-multi')
            tag.set_class_by_level(view,'tag')
            tag.set_pos(view,offset=offset)
            tag.set_size(view)
            tag.set_title(xtag,['name','mcid','props'])
            tag.data['tag-id']=xtag.get('id','')
            return tag

        #bboxes = xtag.get('bboxes')
        views = xtag.get('views')
        tags:list[HtmlTag]=[]
        if len(views)>1 and not xtag.get('tags'):
            #如果有多个bboxes且没有子tags，每个bboxes单独显示
            for view in views:
                #view = Renderer.make_view(xtag['page'],b,xtag['view']['scale'])
                tags.append(make_tag(view,True))
        else:
            tags.append(make_tag(xtag['view'],False))
            if xtag.get('tags'):
                #如果又有子tag，该如何呈现？
                tags[0].children.extend(Renderer.render_objects('tag',xtag.get('tags'),offset=xtag['view']))
        return tags

class Form(TObject):
    type: ClassVar[str] = 'form'

    @override
    def _prepare(self):
        self.forms:list[Form]=[]
        for obj in self.data.get('forms',[]):
            self.forms.append(Form(self.page,obj,parent=self))
        return super()._prepare()
    


    @override
    def render(self) -> Sequence[Any]:
        data = self.data
        offset = self.parent.offset if self.parent else None

        from_:HtmlTag=HtmlTag('div')
        from_.classes.append('form')
        from_.set_class_by_level(self.view,'form')
        
        from_.set_pos(self.view,offset=offset)
        from_.set_size(self.view)
        from_.set_title(data,['name','number','objid'])
        from_.set_data(data,['path'])

        if False:
            if data.get('tags'):
                #如果又有tag
                from_.children.extend(Renderer.render_objects('tag',data.get('tags'),offset=data['view']))
                pass
        
        for f in self.forms:
            from_.children.extend(f.render())

        return [from_]


class Block(TObject):
    type:ClassVar[str]='block'

    @override
    def _prepare(self):
        self.objects:list[TObject]=[]
        for i,obj in enumerate(self.data.get('objects',[])):
            self.objects.append(self.create_object(obj['type'],obj,parent=self))
            #一开始存在的对象都会按原来的顺序建立阅读顺序，从1开始
            self.objects[-1].read_order=i+1
        return super()._prepare()
    
    @override
    def render(self,*,render_read_orders:bool=False)->Sequence[HtmlTag]:
        data = self.data
        offset = self.parent.offset if self.parent else None
        tag=HtmlTag('div')
        tag.set_pos(self.view,offset=offset)
        tag.set_size(self.view)
        tag.classes.append(self.type)
        for obj in self.objects:
            #如果是block，不再递归显示阅读顺序
            tag.children.extend(obj.render())
            #TODO 也可以在这里
            #tag.children.append(obj.render_read_order(i+1))
        
        #或者在最后面
        if render_read_orders:
            for i,obj in enumerate(self.objects):
                tag.children.append(obj.render_read_order(obj.read_order))
        return [tag]
    
    def render_read_orders(self)->Sequence[HtmlTag]:
        """显示所有对象的阅读顺序"""
        tags:list[HtmlTag]=[]
        for i,obj in enumerate(self.objects):
            tags.append(obj.render_read_order(i+1))
        return tags
    
    def add(self,obj:TObject):
        assert obj.parent is None
        self.objects.append(obj)
        obj.parent=self
    
    def jsonify(self)->Any:
        return {
            'type':'block',
            #不再输出line，rect等，仅仅输出
            'objects':[obj.jsonify() for obj in self.objects if isinstance(obj,(Table,Text,Figure,Formula))]
        }
    
class Header(Block):
    type:ClassVar[str]='header'
    
class Footer(Block):
    type:ClassVar[str]='footer'

class Footnote(Block):
    type:ClassVar[str]='footnote'

class Body(Block):
    type:ClassVar[str]='body'
    
class Other(Block):
    type:ClassVar[str]='other'

class Formula(TObject):
    type:ClassVar[str]='formula'

    @override
    def render(self) -> Sequence[HtmlTag]:
        offset = self.parent.offset if self.parent else None
        data = self.data
        #使用一个div，里面显示一个图片
        use_img=False
        if use_img:
            tag = HtmlTag('img')
            #如果使用的是图片
            tag.attrs['src'] = f'images/{data["filename"]}'
        else:
            tag = HtmlTag('div')
            #图片作为背景就可以？
            img_tag = HtmlTag('img')
            img_tag.attrs['src']=f'images/{data["filename"]}'
            img_tag.style['width']='100%'
            img_tag.style['height']='100%'
            tag.children.append(img_tag)
        tag.set_pos(self.view, offset=offset)
        tag.set_size(self.view)
        tag.set_index(data)

        tag.classes.append('formula')

        #如果使用的是latex，可以渲染？
        #暂时记录为公式，然后如果需要查看渲染的结果？在hover的时候？
        self._set_common_data(tag)

        #'objid','flip','rotate','features',
        tag.set_data(data,['tag','form'])
        tag.set_title(data,['tag','form','bbox','filename'])

        return [tag]
    
    def jsonify(self)->Any:
        obj={'type':'formula','filename':self.data['filename'],'latex':self.data.get('latex')}
        return obj
    
class Text(TObject):
    type:ClassVar[str]='text'
    
    @override
    def _prepare(self):
        self.textlines:list[Textline]=[]
        for tl in self.data.get('lines',[]):
            tl = Textline(self.page,tl,parent=self)
            self.textlines.append(tl)
        return super()._prepare()
    
    @override
    def render(self)->Sequence[HtmlTag]:
        data=self.data
        offset=self.parent.offset if self.parent else None
        tag = HtmlTag('div')
        tag.set_pos(self.view,offset=offset)
        tag.set_size(self.view)
        #需要区分为text还是title
        tag.classes.append('text')
        if self.subtype=='title':
            #这个是在XTitle中设置
            tag.classes.append('title')
        elif self.subtype=='quote':
            tag.classes.append('quote')

        self._set_common_data(tag)
        
        for tl in self.textlines:
            tag.children.extend(tl.render())
        return [tag]
    
    @cached_property
    def text(self)->str:
        buf:list[str]=[]
        for tl in self.textlines:
            buf.append(tl.text)
        #不添加换行符号了
        return ''.join(buf)
    
    @override
    def jsonify(self)->Any:
        return {
            'type':'text',
            #忽略行内图片/公式等了
            'text':self.text
        }

class Textline(TObject):
    type:ClassVar[str]='textline'

    @override
    def _prepare(self):
        self.objects:list[TObject]=[]
        for obj in self.data.get('objects',[]):
            self.objects.append(self.create_object(obj['type'],obj,parent=self))
        return super()._prepare()
    
    @override
    def render(self)->Sequence[HtmlTag]:
        data=self.data
        offset=self.parent.offset if self.parent else None
        tag = HtmlTag('div')
        tag.set_pos(self.view,offset=offset)
        tag.set_size(self.view)
        tag.classes.append('textline')
        for obj in self.objects:
            tag.children.extend(obj.render())
        return [tag]
    
    @cached_property
    def text(self)->str:
        buf:list[str]=[]
        for obj in self.objects:
            if isinstance(obj,Span):
                buf.append(obj.text)
        return ''.join(buf)


class Table(TObject):
    type:ClassVar[str]='table'

    @override
    def _prepare(self):
        self.row_num = self.data['row_num']
        self.col_num = self.data['col_num']
        self.cells:list[Cell]=[]
        for obj in self.data.get('cells',[]):
            self.cells.append(Cell(self.page,obj,parent=self))
    
    @override
    def render(self)->Sequence[HtmlTag]:
        data=self.data
        subtype=data.get('subtype','ybk')
        offset = self.parent.offset if self.parent else None
        table = HtmlTag('table')
        table.set_pos(self.view, offset=offset)
        table.set_size(self.view)
        table.classes.append('table')
        table.classes.append(f'table-{subtype}')
        if self.merged_index is not None:
            table.classes.append('table-merged')

        self._set_common_data(table)

        # <table classe="table" style=""></table>
    
        # 设置列的默认width，这样当存在错位的列，也能够100%还原

        debug=True

        def get_x_axis()->list[float]:
            axis = [None]*self.col_num
            for cell in self.cells:
                i = cell.col_index
                # j = cell.col_index+cell.col_span
                if axis[i] is None:
                    axis[i] = cell.bbox[0]
            axis.append(self.bbox[2])
            if debug:
                #存在表格解析的错误，问题是因为存在误差很小的线
                #导致col_span计算错误，只要修正了线，就不会出现出现了
                if any(x is None for x in axis):
                    print(self.page.number,(self.row_num,self.col_num),axis)
                    for c in self.cells:
                        print((c.row_index,c.col_index,c.row_span,c.col_span))
            return axis

        x_axis = get_x_axis()

        colgroup = HtmlTag('colgroup')
        for i in range(self.col_num):
            width = (x_axis[i+1]-x_axis[i])*self.view['scale']
            #buf.append(f'<col span="1" style="width:{width}px;"></col>')
            col = HtmlTag('col')
            col.style['width']=f'{width:.2f}px'
            colgroup.children.append(col)
        
        table.children.append(colgroup)
        # =========
        tbody=HtmlTag('tbody')
        trs:list[HtmlTag] = []
        for cell in self.cells:
            if cell.row_index >= len(trs):
                tr = HtmlTag('tr')
                trs.append(tr)
            tr = trs[-1]#rows[cell.row_index]
            tr.children.extend(cell.render())
        
        tbody.children.extend(trs)
        table.children.append(tbody)

        return [table]

    def jsonify(self)->Any:
        """简易版的"""
        cells:list[Any]=[]
        for cell in self.cells:
            cells.append(cell.jsonify())
        obj={
            'row_num':self.row_num,
            'col_num':self.col_num,
            'cells':cells
        }
        return obj
class Cell(TObject):
    type:ClassVar[str]='cell'
    def _prepare(self):
        self.row_index:int = self.data['row_index']
        self.col_index:int = self.data['col_index']
        self.row_span:int = self.data.get('row_span',1)
        self.col_span:int = self.data.get('col_span',1)

        self.objects:list[TObject]=[]
        for obj in self.data['body']['objects']:
            #对象使用的parent为table，而不是cell，因为offset是相对table
            ##obj相对table，而不是相对self.body这个block
            self.objects.append(self.create_object(obj['type'],obj,parent=self.parent))
        return super()._prepare()
    
    @override
    def render(self)->Sequence[HtmlTag]:
        cell = self.data
        # <td ></td>

        td_tag = HtmlTag('td')
        
        # 现在使用了colgroup，只需要设置height就可以，但是现在还是连宽度都设置
        # self.set_size(style,cell['view'],scale=scale)
        td_tag.attrs['colspan']=self.col_span
        td_tag.attrs['rowspan']=self.row_span
        td_tag.classes.append('cell')
        #td_tag.set_pos(self.view,offset=offset)
        #td_tag.set_size(self.view)
        #td_tag.style['height'] = '{:.2f}px'.format(self.view['height']*self.view['scale'])
        td_tag.set_size(self.view,use_width=False)
        merged=self.data.get('merged')
        if merged is True:
            #表示跨页表格相邻的单元格合并了
            td_tag.classes.append('cell-merged')
        elif merged is False:
            #表示跨页表格相邻的单元格没有合并
            td_tag.classes.append('cell-no-merged')
        elif self.data.get('subtype')=='header':
            #跨页表格中的重复的表头
            td_tag.classes.append('cell-header')
        else:
            pass
        for obj in self.objects:
            td_tag.children.extend(obj.render())
        
        return [td_tag]
    

    def jsonify(self)->Any:
        obj:Any={
            'row_index':self.row_index,
            'col_index':self.col_index,
            'row_span':self.row_span,
            'col_span':self.col_span,
            #仅仅输出这几个
            'objects':[obj.jsonify() for obj in self.objects if isinstance(obj,(Text,Figure,Formula))]
        }
        return obj
class XTree:
    def __init__(self,doc:Document,data:dict[str,Any]):
        super().__init__()
        self.doc = doc
        self.root = XNode(doc,data['root'])

        #因为目前的结构为
        #<root>
        # <chapter>   => 使用这个为单位，从1开始计算阅读顺序，而不是按页
        #   <title>
        # <chapter>   => 到了下一个章节，又从1开始
        #   <title>

        #1表示每个章节独立排序
        #2表示全局排序
        method=1
        if method==1:
            for child in self.root.children:
                self._setup_read_orders(child,1)
        else:
            self._setup_read_orders(self.root,0)
        
        self._setup_id(self.root,0)

    
    def _setup_id(self,node:'XNode',id:int)->int:
        #TODO 或者添加一个类型
        #node.id=str(id)
        node.id=f'{node.type}_{id}'
        id+=1
        for child in node.children:
            id=self._setup_id(child,id)
        return id
    def _setup_read_orders(self,node:'XNode',n:int):
        """设置阅读顺序，返回下一个的阅读顺序"""
        xobject = node.object
        if isinstance(xobject,(XTitle,XText,XFigure,XFormula)):
            if len(xobject.objects)>0:
                #如果为逻辑标题，没有对象，所以也不影响n
                for obj in xobject.objects:
                    obj.read_order=n
                n+=1
        elif isinstance(xobject,XTable):
            if len(xobject.tables)>0:
                for obj in xobject.tables:
                    obj.read_order=n
                n+=1
        else:
            raise ValueError(f'不支持的xobject:{xobject}')
        

        for child in node.children:
            n=self._setup_read_orders(child,n)
        return n
    
    def jsonify_tables(self)->dict[str,Any]:
        #如果是解析了章节树，存在合并，这里就输出合并的对象
        #为了避免文件过大，如果不是合并的对象，这里就不输出了

        def fill_xobjects(node:XNode,xobjects:dict[str,Any]):
            if isinstance(node.object,XTable):
                #再获得一个简化版本的json
                if True:#len(node.object.tables)>1:
                    xobjects[node.id]=node.object.jsonify()
                else:
                    #单个的没有合并的，就不需要输出了
                    pass
            elif isinstance(node.object,XText):
                pass
            elif isinstance(node.object,XTitle):
                pass
            for child in node.children:
                fill_xobjects(child,xobjects)
        
        xobjects:dict[str,Any]={}
        fill_xobjects(self.root,xobjects)
        return xobjects

class XNode:
    def __init__(self,doc:Document,data:dict[str,Any]):
        super().__init__()
        self.doc:Final = doc
        self.parent:Self|None=None
        self.children:list[Self]=[]
        self.type = data['type']
        self.data=data

        self.id:str=''
        """"""

        if data.get('data'):
            self.object = self._create_xobject(data['data'])

        for child in data.get('children') or []:
            self.children.append(self.__class__(doc,child))
        
    
    def _create_xobject(self,data:dict[str,Any])->Any:
        type_ = data['type']
        if type_=='xtitle':
            return XTitle(self,data)
        elif type_=='xtext':
            return XText(self,data)
        elif type_=='xfigure':
            return XFigure(self,data)
        elif type_=='xformula':
            return XFormula(self,data)
        elif type_=='xtable':
            return XTable(self,data)
        else:
            raise ValueError(f'不支持的类型:{type_},data={data}')



    def add(self,child:Self):
        self.children.append(child)
        child.parent = self
    

    def to_outline(self)->Any:
        obj:dict[str,Any]={
            'id':self.id,
            'type':'xnode'
        }
        if self.type=='xtitle':
            #如果太长了，截断一些？
            xtitle=cast(XTitle,self.object)
            obj['title']=xtitle.text
            if xtitle.objects:
                #表示来自原文
                obj['el_id']=xtitle.objects[0].id
                obj['page_id']=xtitle.objects[0].page.id
            else:
                page = self.doc.get_page(xtitle.data['location']['page_number'])
                obj['page_id']= page.id
                #也可以设置这个，然后支持滚动到指定位置
                #obj['bbox']=xtitle.data['location']['bbox']
            obj['children']=[child.to_outline() for child in self.children]
        elif self.type=='xtext':
            xtext = cast(XText,self.object)
            #xtext[1,2,3] => 表示从跨了1-2页，3个文本合并
            obj['el_id']=xtext.objects[0].id
            obj['title']=f'xtext[{xtext.objects[0].page.number},{xtext.objects[-1].page.number},{len(xtext.objects)}]'
            if len(xtext.objects)>1:
                obj['children']=[{
                    'title': f'{i+1}',
                    'type':'xnode',
                    'el_id':t.id,
                    'id': f'{self.id}_{i}'
                } for i,t in enumerate(xtext.objects)]
                pass
        elif self.type=='xtable':
            #显示子表格
            xtable=cast(XTable,self.object)
            obj['el_id']=xtable.tables[0].id
            obj['title']=f'xtable[{xtable.tables[0].page.number},{xtable.tables[-1].page.number},{len(xtable.tables)}]'
            if len(xtable.tables)>1:
                obj['children']=[{
                    'title':f'{i+1}',
                    'type':'xnode',
                    'el_id':t.id,
                    'id':f'{self.id}_{i}'
                } for i,t in enumerate(xtable.tables)]
            pass
        elif self.type=='xfigure':
            xfigure = cast(XFigure,self.object)
            obj['el_id']=xfigure.objects[0].id
            obj['title']=f'xfigure[{xfigure.objects[0].page.number},{xfigure.objects[-1].page.number},{len(xfigure.objects)}]'
            if len(xfigure.objects)>1:
                obj['children']=[{
                    'title':f'{i+1}',
                    'type':'xnode',
                    'el_id':t.id,
                    'id':f'{self.id}_{i}'
                } for i,t in enumerate(xfigure.objects)]

        elif self.type=='xformula':
            xformula = cast(XFormula,self.object)
            obj['el_id']=xformula.objects[0].id
            obj['title']=f'xformula[{xformula.objects[0].page.number},{xformula.objects[-1].page.number},{len(xformula.objects)}]'
            
            if len(xformula.objects)>1:
                obj['children']=[{
                    'title':f'{i+1}',
                    'type':'xnode',
                    'el_id':t.id,
                    'id':f'{self.id}_{i}'
                } for i,t in enumerate(xformula.objects)]
        else:
            raise ValueError(f'不支持的类型:{self.type}')
        
        return obj


class XObject:
    type:ClassVar[str]='xobject'
    def __init__(self,node:XNode,data:dict[str,Any]):
        super().__init__()
        self.doc:Final = node.doc
        self.node:Final = node
        self.data:Final = data
        self._prepare()
    
    def _prepare(self):
        pass

class XTable(XObject):
    type:ClassVar[str]='xtable'
    @override
    def _prepare(self):
        data = self.data
        self.tables:list[Table]=[]
        merged = len(data['tables'])>1
        for i,obj in enumerate(data.get('tables',[])):
            page = self.doc.get_page(obj['page_number'])
            merged_index=i if merged else None
            table = page.create_object('table',obj,merged_index=merged_index)
            table.xobject=self
            assert page.body is not None
            page.body.add(table)
            self.tables.append(table)
    
    def jsonify(self)->Any:
        def build_text(text:Any):
            buf:list[str]=[]
            for line in text['lines']:
                for obj in line['objects']:
                    if obj['type']=='span':
                        buf.append(obj['text'])
                    else:
                        #行内图片和公式，忽略？
                        pass
            return ''.join(buf)

        def build_body(cell:Any):
            body = cell['body']
            buf:list[str]=[]
            objects:list[Any]=[]
            for obj in body['objects']:
                if obj['type']=='text':
                    buf.append(build_text(obj))
                else:
                    text=''.join(buf)
                    buf.clear()
                    if text:
                        objects.append({'type':'text','text':text})
                    
                    if obj['type']=='figure':
                        objects.append({
                            'type':'figure',
                            'filename':obj['filename']
                        })
                    elif obj['type']=='formula':
                        objects.append({
                            'type':'formula',
                            'filename':obj['filename'],
                            'latex':obj.get('latex')
                        })
                    else:
                        #忽略
                        pass
            
            text=''.join(buf)
            if text:
                objects.append({'type':'text','text':text})
            
            return objects
        
        new_cells:list[Any]=[]
        for cell in self.data['cells']:
            new_cell = {
                'row_index':cell['row_index'],
                'col_index':cell['col_index'],
                'row_span': cell.get('row_span',1),
                'col_span':cell.get('col_span',1),
                'objects':build_body(cell)
            }
            new_cells.append(new_cell)
        return {
            'row_num':self.data['row_num'],
            'col_num':self.data['col_num'],
            'cells':new_cells,
        }

class XTitle(XObject):
    type:ClassVar[str]='xtitle'
    @override
    def _prepare(self):
        data = self.data
        self.text = data['text']
        self.objects:list[TObject]=[]
        if data.get('body'):
            #表示来自原文
            merged = len(data['body']['objects'])>1
            for i,obj_data in enumerate(data['body']['objects']):
                page = self.doc.get_page(obj_data['page_number'])
                assert page.body is not None
                merged_index=i if merged else None
                obj = page.create_object(obj_data['type'],obj_data,merged_index=merged_index)
                obj.xobject=self
                if obj.type=='text':
                    #添加一个子类型为title
                    obj.subtype='title'
                page.body.add(obj)
                self.objects.append(obj)
            
            self.location={
                'page_number':self.objects[0].page.number,
                'bbox':self.objects[0].bbox
            }
        else:
            #表示为逻辑标题，只有位置
            #
            self.location = data.get('location')
    

class XText(XObject):
    type:ClassVar[str]='xtext'
    @override
    def _prepare(self):
        self.objects:list[TObject]=[]
        merged = len(self.data['objects'])>1
        for i,obj_data in enumerate(self.data['objects']):
            page = self.doc.get_page(obj_data['page_number'])
            assert page.body is not None
            merged_index=i if merged else None
            obj = page.create_object(obj_data['type'],obj_data,merged_index=merged_index)
            obj.xobject=self
            page.body.add(obj)
            self.objects.append(obj)

class XFigure(XObject):
    type:ClassVar[str]='xfigure'
    @override
    def _prepare(self):
        self.objects:list[TObject]=[]
        merged = len(self.data['objects'])>1
        for i,obj_data in enumerate(self.data['objects']):
            page = self.doc.get_page(obj_data['page_number'])
            assert page.body is not None
            merged_index=i if merged else None
            obj = page.create_object(obj_data['type'],obj_data,merged_index=merged_index)
            obj.xobject=self
            page.body.add(obj)
            self.objects.append(obj)

class XFormula(XObject):
    type:ClassVar[str]='xformula'

    @override
    def _prepare(self):
        self.objects:list[TObject]=[]
        merged = len(self.data['objects'])>1
        for i,obj_data in enumerate(self.data['objects']):
            page = self.doc.get_page(obj_data['page_number'])
            assert page.body is not None
            merged_index=i if merged else None
            obj = page.create_object(obj_data['type'],obj_data,merged_index=merged_index)
            obj.xobject=self
            page.body.add(obj)
            self.objects.append(obj)


class OutlineBuilder:
    def __init__(self):
        super().__init__()

    def build(self, doc:Document):

        nodes:list[Mapping[str,Any]] = []
        if False:
            if doc.get('pdf'):
                #表示解析的是pdf文件，才有outline
                nodes.append(self._build_pages_node(doc))
                nodes.append(self._build_pdf_node(doc))
        
        if False:
            #如果是解析了章节树
            nodes.append({
                'title':'页面',
                'type':'pages',
                'children':[{
                    'title':'1',
                    'type':'page',
                    'children':[{
                        'title':'页眉',
                        'type':'header'
                    },{
                        'title':'页脚',
                        'type':'footer'
                    },{
                        'type':'脚注',
                        'type':'footnotes',
                        'children':[]
                    },{
                        'type':'内容',
                        'type':'body'
                    }]
                }]
            })
        
        if doc.tree:
            if True:
                nodes.append(doc.tree.root.to_outline())
            else:
                nodes.append({
                'title':'章节树',
                'type':'xdoc',
                'children':[
                    doc.tree.root.to_outline()
                ]
            })
            pass

        return nodes
    
    def _build_pages_node(self,doc:Mapping[str,Any])->Mapping[str,Any]:

        def build_table_nodes(doc):
            nodes = []

            for page in doc['pages']:
                if 'body' in page:
                    for block_index, block in enumerate(page['body']['blocks']):
                        for table in block['tables']:
                            nodes.append({
                                'id': table['id'],
                                'type': 'table',
                                'title': f'[{page["number"]}-{block_index}]'
                            })

            return nodes
        
        def build_tags(tags:Sequence[Mapping[str,Any]])->list[Mapping[str,Any]]:
            nodes:list[Mapping[str,Any]]=[]
            for tag in tags:
                title:str=f'{tag['name']}:{tag['path']}'
                if tag.get('props'):
                    title=f'{title}:{json.dumps(tag.get('props'),ensure_ascii=False)}'

                node={
                    #'id':f'',
                    'type':'page.tag',
                    'title':title,
                    'tag':tag['path'],
                    'children':build_tags(tag.get('tags') or [])
                }
                nodes.append(node)
            return nodes


        def build_block(block:Mapping[str,Any],type:str)->Mapping[str,Any]:
            if type=='page':
                title=f'{block['number']}'
            else:
                title=f'{block['objid']}_{block['number']}({block['name']})'

            node:dict[str,Any]={
                #'id':f'page-{page['number']}',
                'title':title,
                'type': type,
                #页面的没有
                'path': block.get('path'),
                'children':[
                    {
                        'title':'forms',
                        'type':'forms',
                        'children':[build_block(form,'form') for form in block.get('forms',[])]
                    },
                    {
                        'title':'tags',
                        'type':'tags',
                        'children':build_tags(block.get('tags',[]))
                    }
                ]
            }
            return node
        
        node:dict[str,Any]={
            #'id':'pages',
            'title':'页面',
            'type':'pages',
            'children':[build_block(p,'page') for p in doc['pages']]
        }
        return node
    def _build_pdf_node(self,doc:Mapping[str,Any])->Mapping[str,Any]:

        def get_tags(node:Mapping[str,Any],tags:list[str])->list[str]:
            if node.get('type')=='item':
                tags.append(node.get('tag'))
            else:
                for child in node.get('children') or []:
                    get_tags(child,tags)
            return tags
        
        def build_element_title(node:Mapping[str,Any])->str:
            name:str=node['name']
            path:str|None=node.get('path')
            #开始页码
            number:int|None=node.get('page_number')
            if path and number:
                #表示没有输出这两个，可能在生产的时候
                #P(1,/a/b)
                return f'{name}({number},{path})'
            else:
                return name

        def build_struct_tree_node(node:Mapping[str,Any],seq:int=0,is_root:bool=False)->dict[str,Any]:
            obj:dict[str,Any]={
                'id':f'pdf-node-{seq}',
                'type':'st.element',
                #path不一定输出
                'title':build_element_title(node),
                'children':[],
                #为了减少json的大小，并不会输出tags，需要动态计算
                'tags': None # if is_root else get_tags(node,[])
            }
            if is_root:
                obj['root']=is_root

            seq+=1
            children:list[Mapping[str,Any]]=[]
            tags:list[str]=[]
            for n in node['children']:
                if n.get('type','element')=='element':
                    children.append(build_struct_tree_node(n,seq))
                    seq+=1
                elif n.get('type')=='item' and n.get('tag'):
                    #item就不输出了，仅仅计算其的tag
                    tags.append(n.get('tag'))
                else:
                    pass
            
            #仅仅输出item的tags即可，如果需要包括子的，js中动态计算
            obj['tags']=tags
            obj['children']=children
            return obj
        


        node:dict[str,Any]={
            #'id':'pdf',
            'title':'PDF',
            'type':'pdf',
            'children':[
                {
                    #'id':'pdf-struct-tree',
                    'title':'struct tree',
                    'type':'st.tree',
                    'children':[]
                }
            ]
        }
        st = doc['pdf'].get('struct_tree')
        if st and st.get('root'):
            node['children']=[build_struct_tree_node(st.get('root'),is_root=True)]
        return node


class HtmlRenderer:
    """支持渲染pdf2json.json,image2json.json,doc.json的结果"""
    def render(self,data:dict[str,Any],result_file:Path|str,scale:float=1):
        result_file=Path(result_file)
        result_file.parent.mkdir(parents=True,exist_ok=True)
        
        doc = Document(data,scale=scale)
        html = doc.render()
        result_file.write_text(html,encoding='utf-8')
    
    def render_tow(self,name:str,dir1:Path,dir2:Path,scale:float=1):
        #TODO 暂时不需要这个方法了
        data1=json.loads(dir1.joinpath('doc.json').read_text('utf-8'))
        data2=json.loads(dir2.joinpath('doc.json').read_text('utf-8'))
        result_file=dir2.joinpath(f'{name}.html')
        result_file.parent.mkdir(parents=True,exist_ok=True)

        #需要获得相对与dir2的目录，否则构造pages/,images/的路径的时候，就不正确了
        v1_doc = Document(data1,scale=scale)
        v1_html = v1_doc.render_pages()
        
        #因为输出文件是在dir2，所以不需要设置目录，使用默认的相对目录即可
        v2_doc = Document(data2,scale=scale)
        v2_html = v2_doc.render_pages()

        values={
            '{{v1_html}}':v1_html,
            '{{v2_html}}':v2_html
        }
        template=Path('').read_text('utf-8')
        html = render_template(template,values)
        result_file.write_text(html,encoding='utf-8')

        


