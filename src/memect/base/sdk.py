import base64
import io
import itertools
import json
import logging
import time
from collections.abc import Iterable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from logging import Logger
from pathlib import Path
from typing import Any, Callable, Final, NotRequired, TypedDict
from zipfile import ZipFile

import httpx

from .api import ApiError


class _Error(TypedDict):
    code:Any
    message:str

class _Result(TypedDict):
    error:NotRequired[_Error]
    data:NotRequired[Any]
    base64:NotRequired[bool]


class Api:
    _logger:Logger=logging.getLogger(f'{__module__}.{__qualname__}')
    def __init__(self,*,url:str,async_:bool=False,poll_interval:float=1,use_gzip:bool=True,use_form:bool=False,use_zip:bool=True,zip_size:int=100,timeout:float|None=None,retry_times:int=3,worker_size:int=1,headers:Mapping[str,Any]|None=None,client:Any=None,**kwargs:Any):
        super().__init__()
        self._url:Final[str]=url
        self._poll_interval:Final[float]=poll_interval
        self._use_gzip:Final[bool]=use_gzip
        self._use_form:Final[bool]=use_form
        self._timeout:Final[float|None]=timeout
        self._retry_times:Final[int]=retry_times
        self._headers:Final[Mapping[str,Any]|None]=headers
        self._async:Final[bool]=async_
        self._worker_size:Final[int]=worker_size
        self._use_zip:Final[bool]=use_zip
        self._zip_size:Final[int]=zip_size
        #可以每个api用一个，也可以全局用一个，也可以每次请求都用一个
        self._client:httpx.Client|None=None
        if isinstance(client,Mapping):
            self._client = httpx.Client(**client) # type: ignore
        elif isinstance(client,httpx.Client):
            self._client = client
        else:
            self._client = None
    
    def execute(self,file:str|Path|bytes,params:Mapping[str,Any]|None=None,*,task_id:str|None=None)->Any:
        client:Final = self._client
        custom_task_id = True if task_id else False
        def wait_result(url:str,task_id:str,headers:Mapping[str,Any]):
            error_count=0
            while error_count<self._retry_times:
                #可能出现网络错误，在网络环境好下，不需要重试，因为概率很小，失败了就再次执行即可
                #在网络环境不好，可以重试几次？
                try:
                    if client:
                        res = client.get(url,params={'task_id':task_id,'custom':custom_task_id},headers=headers)
                    else:
                        #每次一个client
                        res = httpx.get(url,params={'task_id':task_id,'custom':custom_task_id},headers=headers)
                except Exception:
                    self._logger.exception('执行请求出现异常，url=%s',url)
                    #网络错误等
                    error_count+=1
                    #如果是网络错误，可能需要等待久一点更好？
                    time.sleep(self._poll_interval)
                    continue

                if res.status_code==200:
                    #如果返回成功，就可以清除错误次数
                    error_count=0
                    if res.headers.get('x-api-result')=='binary':
                        #表示成功且返回的是二进制
                        return res.content
                    
                    result:_Result = res.json()
                    error = result.get('error')
                    if error:
                        #之前的是使用"code"，现在使用"status"
                        #兼容新老的异步
                        if error.get('status') in ('running','waiting') or error.get('code') in ('running','waiting'):
                            #这里sleep 1秒，对于快速的操作，是非常慢了
                            #所以，快速的操作不需要使用异步的方式调用
                            time.sleep(self._poll_interval)
                        else:
                            #其他错误，不需要尝试了
                            raise ApiError(ApiError.ANY,f'调用api失败，返回:{error}')
                    else:
                        return parse_result(result)
                else:
                    #如果是非200，实际上没有必要再继续，因为都是一样的错误了
                    #这里就例行公事再多尝试一下，可能部署了前置，前置错误
                    error_count+=1

        def parse_result(result:_Result)->Any:
            if result.get('base64',False):
                return base64.b64decode(result.get('data',''))
            else:
                return result.get('data')
        
        def format_size(n:int)->str:
            if n<1024:
                #20Byte
                return f'{n}Byte'
            elif n<1024*1024:
                #1.20KB
                return f'{round(n/1024,2)}KB'
            elif n<1024*1024*1024:
                #200.30MB
                return f'{round(n/1024/1024,2)}MB'
            else:
                #100.23GB
                #10234.23GB
                return f'{round(n/1024/1024/1024,2)}GB'
            
        url:Final = self._url
        
        params = params or {}
        #常用的几个参数单独指定，且为url参数
        query:dict[str,Any]={}
        if self._async:
            query['async']='true'
            #在异步的时候可以指定task_id
            if task_id:
                query['task_id']=task_id
        
        #还有更特殊的要求，支持callback，在解析完成后
        #把结果发送到指定的url

        if isinstance(file,(str,Path)):
            file = Path(file).read_bytes()
        

        #如果是返回大型json的，请求可以发送这个header，告知支持gzip，服务器会使用gzip压缩返回，增加压缩/解压的时间，但是可以减少网络的时间
        #在内网，网络快，可以不需要通过gzip，如果是外网，一个几百M的json，压缩后可能就几M，传输速度就很快
        #accept-encoding:gzip

        headers:dict[str,Any]={}
        if not self._use_gzip:
            headers['Accept-Encoding']=''
        else:
            #默认会设置gzip,deflate,br
            #后台服务器目前仅仅使用gzip压缩，因为br压缩需要安装第三方库
            pass
        #timeout=None or (connect,read)
        files:dict[str,Any]|None=None
        data:dict[str,Any]|None=None
        content:bytes|None=None
        if self._use_form:
            files={'file':file}
            #为了支持复杂的参数，使用json序列化为字符串
            data={'params':json.dumps(params,ensure_ascii=False)}
        else:
            #body为文件，通过url发送参数，参数不能够太大，超过url限制
            if params:
                query['params']=json.dumps(params,ensure_ascii=False)
            content=file
        
        t = time.monotonic()
        self._logger.info('start invoke api,url=%s ,size=%s,query=%s,params=%s',url,format_size(len(file)),query,params)
        if client:
            res = client.post(url,data=data,files=files,content=content,params=query,timeout=self._timeout,headers=headers)
        else:
            #其他语言只需要参考这一行就可以
            res = httpx.post(url,data=data,files=files,content=content,params=query,timeout=self._timeout,headers=headers)
        
        self._logger.info('end invoke api,url=%s ,status=%s,elapsed=%.3f',url,res.status_code,time.monotonic()-t)

        if res.status_code==200:
            #表示请求成功，且返回的是二进制
            #res.headers.get('x-api-status') 肯定为success
            if res.headers.get('x-api-result')=='binary':
                return res.content
            #请求成功/失败，返回json格式:{error:{},data:''}
            result:_Result = res.json()
            error = result.get('error')
            print(result,error)
            if error:
                raise ApiError(ApiError.ANY,f'api调用失败:{error}')
            if self._async:
                #表示异步执行，通过轮训等待结果
                return wait_result(url,result.get('data',{})['id'],headers)
            else:
                return parse_result(result)
        else:
            raise ApiError(ApiError.ANY,f'api调用失败:http.status={res.status_code}')

    def batch[T:(Path,bytes)](self,files:Iterable[tuple[str,T,Mapping[str,Any]|None]],common_params:Mapping[str,Any]|None=None)->Iterator[tuple[str,T,Any]]:
        """同时处理多个文件

        如果是parser中的/api/parse，不支持使用zip，或者说zip的时候（虽然也是多个图片，但是等同于多个页面，返回的结果和单个图片，单个pdf一样）

        如果是/api/detect，/api/ocr，可以通过zip一次上传多个图片，返回多个结果
        
        简单的说：如果是/api/parse，处理多个pdf/docx/不连续的图片（页面），use_zip=False
        """

        def get_items(files:Iterable[tuple[str,T,Mapping[str,Any]|None]])->Iterator[tuple[str,T,Mapping[str,Any]|None]]:
            """每个文件单独处理"""
            for a,b,c in files:
                #{'images':{}}
                params:dict[str,Any]={}
                if common_params:
                    params.update(common_params)
                if c:
                    params['image']=c
                yield a,b,params
       
        def execute(item:tuple[str,T,Mapping[str,Any]|None])->tuple[str,T,Any]:
            file = item[1]
            return item[0],file,self.execute(file,item[2])
        
        def execute_by_zip(items:Iterable[tuple[str,T,Mapping[str,Any]|None]])->Iterator[tuple[str,T,Any]]:
            buf = io.BytesIO()
            images:list[Any]=[]
            names:list[tuple[str,Any]]=[]
            with ZipFile(buf,mode='w') as zf:
                for name,f,p in items:
                    if isinstance(f,Path):
                        zf.write(f,name)
                    else:
                        #bytes
                        zf.writestr(name,f)
                    
                    if p:
                        #如果没有参数就不需要设置
                        p=dict(p)
                        p['name']=name
                        images.append(p)
                    names.append((name,f))
            #{'zip':{'images':[]}}
            params:dict[str,Any]={}
            if common_params:
                params.update(common_params)
            params['zip']={'images':images}
            result = self.execute(buf.getvalue(),params)
            for n,f in names:
                yield (n,f,result['results'][n]) # type: ignore

        if self._use_zip:
            for items in itertools.batched(files,self._zip_size):
                yield from execute_by_zip(items)
        else:
            with ThreadPoolExecutor(max_workers=self._worker_size,thread_name_prefix='api') as executor:
                yield from executor.map(execute,get_items(files))
    

    def zip(self,files:Iterable[str|Path]|str|Path,params:Mapping[str,Any]|None=None)->Any:
        """使用zip的方式打包多个文件请求"""
        buf = io.BytesIO()
        names:list[Path]=[]
        def get_files()->Iterator[str|Path]:
            if isinstance(files,str|Path):
                #为目录
                for file in Path(files).iterdir():
                    if file.is_file() and file.name[0]!='.':
                        yield file
            else:
                yield from files

        with ZipFile(buf,mode='w') as zf:
            for i,file in enumerate(get_files()):
                file = Path(file)
                zf.write(file,file.name)
                names.append(file)
        
        return self.execute(buf.getvalue(),params)
    
    
    def safe_execute[**P](self,max_times:int,fn:Callable[P,Any],*args:P.args,**kwargs:P.kwargs)->Any:
        assert max_times>0
        n=0
        while n<max_times:
            n+=1
            try:
                return fn(*args,**kwargs)
            except Exception:
                self._logger.exception('第%s/%s执行失败',n,max_times)
                if n>=max_times:
                    raise
        
        raise RuntimeError('不可能执行到这里，这行代码是因为ide认为前面的循环无法跳出')