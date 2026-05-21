
from .xbase import XObject, XText, XTree


class XGroupParser:
    def __init__(self):
        super().__init__()
    
    def parse(self,xtree:XTree):
        pass

    def _parse_quote(self,objects:list[XObject],texts:list[XText],method:int=0)->bool:
        """处理引用的内容，也就是在引用范围内的文本，不需要解析为标题
        
        method:0 不处理，1合并为一个XText，2合并为XGroup
        """
        # 对于存在：
        # "xxxx
        # xxx"
        # 这样的文本，合并为一个，不需要解析里面的内容，因为表示为引用
        # 这些文本可能很多，跨几个页面

        # 案例：
        # local/pingan-bug/pdfs/34连云港关于反馈意见的回复报告.pdf  3-4页的，包含表格等
        # local/pingan-bug/pdfs/24.pdf 第4页开始
        
        assert len(texts)>1

        if method==0:
            #不处理
            return False

        #如果是严格的
        quotes:dict[str,Sequence[str]]={
            '“':('”',),
            #'"':('"',)
        }
        
        def is_quote(texts:Sequence[XText])->bool:
            s = ''.join(t.text for t in texts)
            #半角符号难区分，如：
            #"12"34"56" => ["12","56"] 或者["34","123456"]
            #全角的就容易区分，如：
            #“12“34”56” => ["34","123456"]
            queue:list[tuple[str,Sequence[str]]]=[]
            for c in s:
                #放宽的，忽略全角半角，严格，必须匹配
                #在ocr的时候，可能需要放宽
                quote = quotes.get(c)
                if quote:
                    queue.append((c,quote))
                elif queue and c in queue[-1][1]:
                    #表示为结尾的
                    queue.pop(-1)
                else:
                    #没有quote或者不匹配
                    pass
            
            return len(queue)==0

        def make_quote(start:int,end:int,*,method:int=1,strict:bool=True)->XObject|None:
            objs=texts[start:end]
            if strict and not is_quote(objs):
                return None
            
            #每一个都标记为引用的文本？
            for obj in objs:
                for child in obj.objects:
                    child.subtype='quote'
                obj.subtype='quote'
            if method==1:
                #使用XText且lite，如果在同一页切能够合并的，合并为一个，使用起来更加简单
                return XText.join(objs).lite()
            elif method==2:
                #使用XGroup，在章节树解析中作为一个整体，最后输出维持原始的段落结构
                return XGroup(objs)
            else:
                raise ValueError(f'')
        
        quote = quotes.get(texts[0].text[0])
        if quote is None:
            return False

        for i in range(1,len(texts)):
            t1 = texts[i]
            if t1.text[-1] in quote and (xobj:=make_quote(0,i+1,method=method)) is not None:
                group = texts[0:i+1]
                del texts[0:i+1]
                lists.replace(objects,group,[xobj])
                if debugger.allow('info'):
                    with debugger.group('quote texts'):
                        for t in group:
                            debugger.console.print((t.page_numbers,t.text[0:20]))
                return True
        
        return False