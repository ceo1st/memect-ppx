
from typing import Any, Final, Mapping, NotRequired, Sequence, TypedDict

from pydantic import BaseModel


class ErrorModel(BaseModel):
    code:int|None=0
    message:str|None=None
    extras:Mapping[str,Any]|None=None

class ApiError(Exception):

    #在多数情况下，调用者都不需要关心错误码，除非是需要自动化程序，如：现在的Agent等，可以根据错误码进行合适的处理
    ANY=10000
    """表示任何不需要区分的错误"""
    SYSTEM=10001
    """系统错误"""
    PARAMETER=10002
    """参数错误"""

    def __init__(self,code:int,message:str,**extras:Any):
        super().__init__(code,message,extras)
        self.code:Final=code
        self.message:Final=message
        self.extras:Final=dict(extras)
    
    def jsonify(self)->dict[str,Any]:
        data={
            'code':self.code,
            'message':self.message
        }
        
        # 为了兼容老的api接口，code='running'，按现在应该是返回数值+status='running'
        if self.extras.get('status') in ('running','waiting'):
            data['code']='running'
        data.update(self.extras)
        return data
    
    @classmethod
    def from_dict(cls,error:Mapping[str,Any]):
        return cls(**error)


class FileType(TypedDict):
    name: str
    """类型的名字"""
    exts: Sequence[str]
    """文件的扩展名"""
    max_length: int
    """文件的字节数"""
    max_size: NotRequired[tuple[int, int] | None]
    """图片的width/height"""
    max_page_count: NotRequired[int | None]
    """pdf文件允许的页数"""
    max_file_count: NotRequired[int | None]
    """zip文件允许的文件数"""
    
class ApiInfo(TypedDict):
    name: str
    url: str

    allow_async: bool
    allow_timeout: bool
    allow_form: bool
    allow_task_id: bool

    file: Mapping[str, Any]
    schema: Mapping[str, Any]
    defaults: Mapping[str, Any] | None

def usage():
    ApiError(ApiError.SYSTEM,'')
    ApiError(ApiError.ANY,'xxx',status='running')
    