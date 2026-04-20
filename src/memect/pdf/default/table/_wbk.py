

from typing import Any, Mapping, Sequence
from ._base import Block,Table
from memect.base import utils

class WBKParser:
    def __init__(self,settings:Mapping[str,Any]|None=None):
        super().__init__()
        self.settings = settings or utils.settings()['parser']['table']['wbk']

    
    def batch(self,blocks:Sequence[Block],*,mode:str='default'):
        from ._wbk_default import Parser as DefaultParser
        from ._wbk_ai import Parser as AIParser

        if mode=='default':
            #使用默认的方式
            return DefaultParser(self.settings['default']).batch(blocks)
        elif mode=='ai':
            #使用ai（多模态）来解析，如果返回的表格不完整就不使用ai的结果，然后使用默认的方式解析
            for block,table in zip(blocks,AIParser(self.settings['ai']).batch(blocks)):
                if table is None:
                    table = DefaultParser(self.settings['default']).parse(block)
                yield table
            
        else:
            raise ValueError(f'不支持的mode={mode}')
        

    def parse(self,block:Block,*,mode:str='default')->Table|None:
        """解析block包含的内容为表格
        返回None表示无法解析为表格
        """
        #mode='default' => 使用默认的方式解析，pdf可以根据书写顺序，ocr的根据ocr+简单的算法？
        #mode='ai' => 表示使用多态模型解析
        #mode='auto' => 就是先"ai" => "default"，因为当多模态返回的表格结果不完整，就按默认的方式去解析？
        
        from ._wbk_default import Parser as DefaultParser
        from ._wbk_ai import Parser as AIParser

        if mode=='default':
            #使用默认的方式
            return DefaultParser(self.settings['default']).parse(block)
        elif mode=='ai':
            #使用ai（多模态）来解析，如果返回的表格不完整就不使用ai的结果，然后使用默认的方式解析
            table=AIParser(self.settings['ai']).parse(block)
            if table is None:
                table = DefaultParser(self.settings['default']).parse(block)
            return table
        else:
            raise ValueError(f'不支持的mode={mode}')
