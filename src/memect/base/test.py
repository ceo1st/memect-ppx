

from pathlib import Path
from typing import Any

from .utils import Timer

from .sdk import Api


class Tester:
    def __init__(self,url:str|None=None,max_workers:int=5):
        super().__init__()
        if not url:
            url='http://127.0.0.1:9527/api/parse'
        self.url=url
        self.max_workers=max_workers
    
    def run(self,dir:str|Path,out_dir:str|Path|None=None):
        timer = Timer.start()
        dir=Path(dir)
        files:list[Path]=[]
        for file in dir.iterdir():
            if not file.is_file():
                continue
            if file.name[0]!='.' and file.suffix.lower() in ('.pdf','.png','.webp','.jpg','.jpeg','.bmp'):
                files.append(file)
        
        items:list[tuple[str,Path,Any]]=[]
        for file in files:
            item=(file.name,file,None)
            items.append(item)

        api=Api(url=self.url,worker_size=self.max_workers,use_zip=False,use_form=False,async_=False)
        total = len(items)
        n=0
        for id_,file,data in api.batch(items,{'output_files':['doc.md']}):
            print(id_,'||',file)
            n+=1
            zip_file = Path(str(file)+'.zip')
            zip_file.write_bytes(data)
            print(f'{n}/{total},{id_}')

        print(f'total={len(items)},uptime={timer.uptime()}')


def test():
    t = Tester(url='http://127.0.0.1:9527/api/parse',max_workers=5)
    t.run('local/cases/test2')

        