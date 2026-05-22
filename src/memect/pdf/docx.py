

import io

from .base import KDocument, KFigure, KFormula, KMarkdown, KTable, KText


class DocxBuilder:
    def __init__(self):
        super().__init__()
    
    def build(self,doc:KDocument)->bytes:
        #TODO 按页解析和安章节树解析不一样
        fp = io.BytesIO()
        
        for page in doc.pages:
            if page.skipped:
                #插入空白页
                pass
            else:
                for obj in page.objects:
                    if isinstance(obj,KText):
                        #可以使用固定的字体大小，或者计算一个大概的
                        
                        pass
                    elif isinstance(obj,KMarkdown):
                        pass
                    elif isinstance(obj,KFigure):
                        pass
                    elif isinstance(obj,KFormula):
                        pass
                    elif isinstance(obj,KTable):
                        pass
                    else:
                        pass
                pass
        
        return fp.getvalue()
