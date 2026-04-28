
import contextlib
import datetime
import inspect
import os
import threading
from pathlib import Path
from types import FrameType
from typing import (
    Annotated,
    Any,
    Callable,
    ClassVar,
    Final,
    Literal,
    Mapping,
    Self,
    Sequence,
)

from pydantic import ConfigDict, Field
from rich.console import Console
from rich.theme import Theme

from .utils import MyBaseModel

# 不同的terminal下颜色显示不同，目的是因为terminal的背景颜色可以设置，为了在不同的背景下都显示友好
# terminal会自动修改颜色的显示
_theme = Theme({
    'trace': '',  # 'black',
    'info': 'bright_black',
    'warning': 'yellow',
    'error': 'bold red'
})
console: Final = Console(markup=False, theme=_theme)

def getframe(level:int=0)->FrameType:
    #执行cython得到的so文件内的代码，无法获得frame，也就是会被跳过，所以在二进制下
    #无法获得准确的frame
    original_level=level
    level+=1
    frame = inspect.currentframe()
    while level>0 and frame:
        #如果没有了，返回最靠近的，出现这种的可能，level大了
        if frame.f_back is None:
            break
        frame = frame.f_back
        level-=1
    if not frame:
        raise ValueError(f'无法获得frame，level={original_level}')
    return frame

class Config(MyBaseModel):
    model_config = ConfigDict(title='debug config')
    enable: Annotated[bool, Field(description='')] = False
    modules: Annotated[Mapping[str, Sequence[str] | None]
                       | None, Field(description='')] = None
    # methods:Annotated[Sequence[str]|None,Field(description='')]=None
    pages: Annotated[Sequence[int] | None, Field(description='')] = None
    actions: Annotated[Sequence[str] | None, Field(description='')] = None


class XDebugger:
    _config: ClassVar[Config] = Config.create({})
    _lock: Final = threading.RLock()

    def __init__(self, name: str, *, function: str | None = None, level: int | None = None, page: int | None = None):
        super().__init__()
        self.name = name
        self._parts = name.split('.')
        self.force: bool = False
        if level is not None:
            function = inspect.stack()[level].function
        self.function: str | None = function
        self.page = page
        self.console = console

        if function:
            self._fullname = f'{self.name}::{function}'
        else:
            self._fullname = self.name

    def bind(self, level: int = 1, page: int | None = None) -> Self:
        """绑定当前的方法和页码"""
        return self.__class__(self.name, level=level+1, page=page)

    def title(self, name: str, *, stack_level: int = 0) -> str:
        """给标题添加代码的位置信息"""
        frame = getframe(stack_level+1)
        tb = inspect.getframeinfo(frame)
        info = f'[{os.path.basename(tb.filename)}:{tb.function}:{tb.lineno}]'
        return f'{name}{info}'

    def allow(self, action: Literal['gui', 'info', 'save','draw'], *, force: bool = False, page: int | None = None, **query: Any) -> bool:
        """判断是否需要输出调试信息"""
        if force:
            # 如果临时想强制输出
            return True

        config = self._config
        if not config.enable:
            # 如果全局关闭了
            return False

        if self.force:
            # 如果整个强制输出
            return True

        if not self._allow_module():
            return False

        if not self._allow_action(action):
            # 如果没有指定操作或者不允许
            return False

        if page is None:
            # 如果没有指定，就使用默认的
            page = self.page

        if page is not None and not self._allow_page(page):
            return False

        return True

    def _allow_module(self) -> bool:
        modules = self._config.modules
        if not modules:
            # 如果没有设置，表示不允许
            return False

        parts = self._parts[:]
        end = False
        while not end:
            # ['a','b','c'] => 'a.b.c'
            if parts:
                s = '.'.join(parts)
                parts.pop()
            else:
                s = '*'
                end = True
            functions = modules.get(s)
            if functions is not None:
                if len(functions) == 0:
                    # []表示允许全部方法
                    return True
                elif self.function and self.function in functions:
                    # 表示仅仅允许f1,f2
                    # a.b.A1=['f1','f2']
                    return True
                else:
                    pass
            else:
                # None or 没有设置
                pass

        return False

    def _allow_page(self, page: int) -> bool:
        if not self._config.pages:
            return True
        return page in self._config.pages

    def _allow_action(self, name: str) -> bool:
        if not self._config.actions:
            return True
        return name in self._config.actions

    def print(self, *args: Any, page: int | Sequence[int] | None = None, stack_level: int = 0):
        # 参数中stack_level=0表示调用者的frame，这里就需要+1

        stack_level += 1
        # cython二进制后，无法获得准确的frame
        frame = getframe(stack_level)
        tb = inspect.getframeinfo(frame)
        time = datetime.datetime.now().strftime('%H:%M:%S')
        buf: list[str] = []
        buf.append('verbose:')
        buf.append(f'[{time}]')
        buf.append(f'[{os.getpid()}:{threading.current_thread().name}]')
        buf.append(
            f'[{os.path.basename(tb.filename)}:{tb.function}:{tb.lineno}]')
        # buf.append(f'[{name}]')

        if page is None and self.page is not None:
            page = self.page

        if page:
            if isinstance(page, int):
                buf.append(f'[{page}]')
            else:
                # 就认为是连续的
                page = sorted(page)
                buf.append(f'[{page[0]}-{page[-1]}]')
        self.console.print(''.join(buf), *args)

    def print_group[T](self, pagenos: int | Sequence[int] | None, start: str, end: str, objects: Sequence[T], fn: Callable[[int, T], Any] | None = None, stack_level: int = 0):
        with self._lock:
            self.console.rule(start)
            for i, obj in enumerate(objects):
                s: Any
                if fn:
                    s = fn(i, obj)
                else:
                    s = obj
                self.print(pagenos, s, stack_level=1+stack_level)
            self.console.rule(end)

    def __enter__(self) -> Self:
        self._lock.acquire()
        return self

    def __exit__(self, et: type[Any], ev: BaseException | None, tb: inspect.Traceback):
        self._lock.release()

    @contextlib.contextmanager
    def group(self, title: str,*,page:int|Sequence[int]|None=None):
        """输出一组内容
        title:标题
        page：可以输出页码
        """

        # 对于都是使用XDebug，避免多线程混合输出，但是通过其他方式输出的，无法阻止
        self._lock.acquire()
        try:
            self.print(title,page=page,stack_level=2)
            self.console.rule(f'start {title}')
            yield
            self.console.rule(f'end {title}')
        finally:
            self._lock.release()

    _setup_lock:ClassVar[threading.RLock]=threading.RLock()
    _setup_done:ClassVar[bool]=False
    @classmethod
    def setup(cls, config: Mapping[str, Any] | str | Path|None=None):
        """字符串表示json字符串"""
        with cls._setup_lock:
            if not cls._setup_done:
                if config is None:
                    config = Path('./xdebug.py')
                cls._config = Config.create(config)
                cls._setup_done=True

    @classmethod
    def get_config(cls) -> Config:
        return cls._config




