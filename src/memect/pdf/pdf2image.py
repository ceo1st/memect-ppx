import abc
import contextlib
import importlib
import logging
import multiprocessing as mp
import threading
from concurrent.futures import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
)
from enum import StrEnum, auto
from logging import Logger
from pathlib import Path
from typing import (
    Annotated,
    Any,
    ClassVar,
    Final,
    Mapping,
    Self,
    Sequence,
    cast,
    override,
)

from pydantic import Field
from rich.progress import Progress

from memect.base import utils
from memect.base.config import MPInit
from memect.base.utils import MyBaseModel

from .base import KDocument

class Provider(StrEnum):
    pymupdf = auto()
    """gil下线程安全，不支持free-threaded"""
    pdfium = auto()
    """git下线程不安全！！！，不支持free-threaded"""
    unknown = auto()

    def is_available(self)->bool:
        providers={
            Provider.pymupdf:'pymupdf',
            Provider.pdfium:'pypdfium2'
        }
        if self not in providers:
            return False
        else:
            try:
                importlib.import_module(providers[self])
                return True
            except ModuleNotFoundError:
                return False
    
    @classmethod
    def get_available_provider(cls,provider:Self|None)->'Provider':
        if not provider:
            for p in [Provider.pymupdf,Provider.pdfium]:
                if p.is_available():
                    return p
            return Provider.unknown
        elif provider.is_available():
            return provider
        else:
            return Provider.unknown

class DrawerArgs(MyBaseModel):
    file: Path
    out_dir: Path
    max_scale: float = 2
    max_size: tuple[int, int] | None = (2000, 2000)
    #pagenos: Sequence[int] | None = None
    #chunk_size: Annotated[int, Field(description="表示多少页为一批")] = 10
    #skip_exists: bool = False

class DrawerFactory:
    def __init__(self,provider:Provider,args:DrawerArgs):
        super().__init__()
        self._provider:Final=provider
        self._args:Final=args
    
    def __call__(self):
        if self._provider==Provider.pymupdf:
            return _Drawer1(self._args)
        elif self._provider==Provider.pdfium:
            return _Drawer2(self._args)
        else:
            raise ValueError(f'provider={self._provider}')


class Drawer(abc.ABC):
    def __init__(self,args:DrawerArgs):
        super().__init__()
        self._file = args.file
        self._out_dir = args.out_dir
        self._max_scale = args.max_scale
        self._max_size = args.max_size
        self._out_dir.mkdir(parents=True,exist_ok=True)
        self._doc:Any=None
        

    @abc.abstractmethod
    def draw(self,pagenos:Sequence[int]):
        pass

    def _get_scale(
        self,
        size: tuple[int, int],
        max_scale: float,
        max_size: tuple[int, int] | None = None,
    ) -> float:
        if max_size is None:
            return max_scale
        return min(max_scale, max_size[0] / size[0], max_size[1] / size[1])


class _Drawer1(Drawer):
    """轻量级对象，支持进程序列化"""
    _logger: Logger = logging.getLogger(f"{__module__}.{__qualname__}")

    @override
    def draw(self,pagenos:Sequence[int]):
        if self._doc is None:
            #不支持多线程
            import pymupdf
            self._doc = pymupdf.Document(self._file, filetype="pdf")
        for pageno in pagenos:
            self._draw(pageno)

    def _draw(
        self,
        pageno: int
        
    ):
        import pymupdf
        self._logger.debug("start draw page=%s", pageno)
        page: pymupdf.Page = self._doc[pageno - 1]
        alpha: Final = False
        matrix = pymupdf.Identity

        scale = self._get_scale(
            (int(page.rect.width), int(page.rect.height)), self._max_scale, max_size=self._max_size
        )
        if scale == 1:
            matrix = pymupdf.Identity
        else:
            matrix = pymupdf.Matrix(scale, scale)

        pix: pymupdf.Pixmap = page.get_pixmap(  # type: ignore
            matrix=matrix, alpha=alpha, annots=False
        )
        # pix:pymupdf.Pixmap = doc.get_page_pixmap(pageno - 1, matrix=matrix, alpha=alpha, dpi=dpi)
        # 这个如果指定其他格式，总是为png
        # https://pymupdf.readthedocs.io/en/latest/pixmap.html#pixmapoutput
        pix.save(self._out_dir.joinpath(f'{pageno}.png'), "png")  # type: ignore

class _Drawer2(Drawer):
    """轻量级对象，支持进程序列化"""
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")


    @override
    def draw(self,pagenos:Sequence[int]):
        if self._doc is None:
            #不支持多线程
            import pypdfium2 as pdfium
            self._doc = pdfium.PdfDocument(self._file)
        for pageno in pagenos:
            self._draw(pageno)

    def _draw(
        self,
        pageno: int
    ):
        self._logger.debug("start draw page=%s", pageno)
        # pypdfium2不是线程安全的，在多线程下（即使gil开启），一页出现问题，典型的就是doc[1]这样的时候失败
        page = self._doc[pageno - 1]
        width, height = page.get_width(), page.get_height()
        # 计算 scale，确保图片不超过 2000x2000
        scale = self._get_scale((width, height), self._max_scale, max_size=self._max_size)
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil()
        image.save(self._out_dir.joinpath(f'{pageno}.png'))




class Pdf2ImageArgs(MyBaseModel):
    provider: Annotated[Provider | None, Field(description="")] = None
    max_workers:int=4
    max_size:tuple[int,int]=(2000,2000)
    max_scale:float=2
    mode:str='process'

class Pdf2Image:
    """支持多线程使用"""

    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self, args: Pdf2ImageArgs|Mapping[str,Any] | None = None):
        super().__init__()
        args = Pdf2ImageArgs.create(args)
        self._args: Final = args

        if args.provider and not args.provider.is_available() and args.mode not in ('process','thread'):
            raise RuntimeError(f'当前环境不支持,provider={args.provider},mode={args.mode}')

        self._provider: Final = Provider.get_available_provider(args.provider)
        if self._provider==Provider.pymupdf and args.mode=='thread':
            raise ValueError('pymupdf2不支持多线程')

        self._max_workers = args.max_workers
        """表示最大使用多少个worker执行"""

        #如果将来支持了free-threaded，就可以使用多线程的方式执行而不需要使用多进程
        self._mode:Final=args.mode
        self._max_size = args.max_size
        self._max_scale = args.max_scale
        self._drawers:Final = threading.local()
        
    def _get_page_count(self, file: str | Path) -> int:
        if self._provider==Provider.pymupdf:
            import pymupdf
            with pymupdf.Document(filename=file, filetype="pdf") as doc:
                return cast(int, doc.page_count)  # type: ignore
        elif self._provider==Provider.pdfium:
            # 如果仅仅是获得页码，pypdfium2更快
            import pypdfium2 as pdfium
            with pdfium.PdfDocument(file) as doc:
                return len(doc)
        elif self._provider==Provider.unknown:
            import pypdf
            return pypdf.PdfReader(Path(file)).get_num_pages()
        else:
            #使用pdf_oxide?获得，也不支持free-threaded
            #使用pdfminer获得，太慢
            raise ValueError(f'不支持的provider={self._provider}')

    def _get_pagenos(
        self,
        out_dir: str | Path,
        page_count: int,
        pagenos: Sequence[int] | None = None,
        skip_exists: bool = False,
    ) -> tuple[Sequence[int], Sequence[int]]:
        out_dir = Path(out_dir)
        all_pagenos = list(range(1, page_count + 1))
        if not pagenos:
            pagenos = all_pagenos
        else:
            pagenos = list(set(pagenos) & set(all_pagenos))
            pagenos.sort()

        pending_pagenos: list[int] = []
        exist_pagenos: list[int] = []
        if skip_exists:
            for pageno in pagenos:
                file = out_dir / f"{pageno}.png"
                if file.is_file():
                    exist_pagenos.append(pageno)
                else:
                    pending_pagenos.append(pageno)
        else:
            pending_pagenos.extend(pagenos)

        return exist_pagenos, pending_pagenos
    

    def parse(self,doc:KDocument,*,show_progress:bool=False):
        timer = utils.Timer.start()
        pagenos:list[int]=[]
        for page in doc.working_pages:
            if doc.is_dev() and page.file.is_file():
                continue
            pagenos.append(page.number)
        
        if not pagenos:
            self._logger.warning('没有页面需要执行')
            doc.state['pdf2image']={
                'total':0,
                'elapsed':0
            }
            return
        
        args = DrawerArgs(file=doc.file,out_dir=doc.pages_dir,max_scale=self._max_scale,max_size=self._max_size)
        self.execute(args,pagenos,show_progress=show_progress)
        doc.state['pdf2image']={
            'total':len(pagenos),
            'elapsed':timer.elapsed()
        }

    def execute(self, args: DrawerArgs,pagenos:Sequence[int]|None=None, *,show_progress:bool=False):
        """
        pagenos:[] 如果为None或者[]，表示所有的页面
        """
        file = args.file
        page_count = self._get_page_count(file)
        _, pagenos = self._get_pagenos(
            args.out_dir, page_count,pagenos,
        )
        if not pagenos:
            self._logger.info("没有页面需要执行")
            return
        
        factory = DrawerFactory(self._provider,args)
        if self._max_workers == 0:
            # 表示在当前进程执行，方便调试
            drawer = factory()
            drawer.draw(pagenos)
        else:
            #表示每次提交多少页，可以为1，或者更多一些的数值也可以，减少序列的次数
            chunk_size=5
            with self._show_progress(show_progress) as progress:
                if progress is not None:
                    task = progress.add_task('[red bold]pdf2image',total=len(pagenos),markup=True)
                else:
                    task = None
                with self._new_executor(factory) as executor:
                    if self._mode=='process':
                        fn=self._draw_on_process
                    else:
                        fn=self._draw_on_thread
                    for k in executor.map(fn,[pagenos[i:i+chunk_size] for i in range(0,len(pagenos),chunk_size)]):
                        if progress is not None:
                            progress.advance(task,advance=k)
                    
                    if progress is not None:
                        progress.update(task,completed=True)
    
    @contextlib.contextmanager
    def _show_progress(self,enable:bool):
        if enable:
            with Progress(console=utils.console) as progress:
                yield progress
        else:
            yield None
        
    def _new_executor(self,factory:DrawerFactory):
        max_workers = self._max_workers
        if self._mode == "process":
            # 这个不会释放进程，进程常驻
            mp_init = MPInit(name='pdf2image')
            mp_init.set_fn(self._init_process,factory)
            return ProcessPoolExecutor(
                max_workers, mp_context=mp.get_context("spawn"), initializer=mp_init
            )
        elif self._mode == "thread":
            # 如果将来pymupdf或者pdfium支持了free-threaded，选择这个
            args=(factory,)
            return ThreadPoolExecutor(max_workers, thread_name_prefix="pdf2image",initializer=self._init_thread,initargs=args)
        else:
            raise ValueError("")

    def _init_thread(self,factory:DrawerFactory):
        """在工作线程执行"""
        assert getattr(self._drawers,'drawer',None) is None
        self._drawers.drawer = factory()

    def _draw_on_thread(self,pagenos:Sequence[int]):
        drawer:Drawer= self._drawers.drawer
        assert drawer is not None
        drawer.draw(pagenos)
        return len(pagenos)

    _mp_drawer:ClassVar[Drawer|None]=None

    @classmethod
    def _init_process(cls,factory:DrawerFactory):
        assert cls._mp_drawer is None
        cls._mp_drawer = factory()

    @classmethod
    def _draw_on_process(cls,pagenos:Sequence[int]):
        assert cls._mp_drawer is not None
        cls._mp_drawer.draw(pagenos)
        return len(pagenos)
    

    
def main():
    # 提供命令行使用
    import sys
    from memect.base.config import setup
    setup()
    settings = Pdf2ImageArgs.model_validate_json(sys.argv[1])
    args = DrawArgs.model_validate_json(sys.argv[2])
    Pdf2Image(settings,only_execute=True).execute(args, max_workers=0)


if __name__ == "__main__":
    main()
