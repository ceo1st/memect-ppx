import logging
import re
import shutil
import sys
import threading
import time
import uuid
import weakref
from concurrent.futures import BrokenExecutor, Executor
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Final, Mapping, Self, Sequence, override

import PIL
import PIL.Image
import orjson
from pydantic import BaseModel
from rich.console import Console
from rich.theme import Theme

from .config import load_data

# 不同的terminal下颜色显示不同，目的是因为terminal的背景颜色可以设置，为了在不同的背景下都显示友好
# terminal会自动修改颜色的显示
_theme = Theme(
    {
        "trace": "",  # 'black',
        "info": "bright_black",
        "warning": "yellow",
        "error": "bold red",
    }
)
console: Final = Console(markup=False, theme=_theme)


class F:
    def __init__[**P, T](self, fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def __call__(self):
        return self._fn(*self._args, **self._kwargs)


class Timer:
    def __init__(self):
        super().__init__()
        self.reset()

    def reset(self):
        """
        重新开始计算
        """
        self._end_clock: float | None = None
        self._end_time: float | None = None
        self._marks: dict[str, float] = {}
        self._start_clock: float = time.monotonic()
        self._start_time: float = time.time()

    def elapsed(
        self,
        *,
        mark: str | None = None,
        start: str | None = None,
        end: str | None = None,
        ndigits: int = 3,
    ) -> float:
        """
        返回耗时的秒数
        mark: 记录当前的位置（时间）
        start: 获得前一个时间，None表示开始时间
        end: 获得结束时间，None表示当前时间
        """
        if start:
            start_clock = self._marks[start]
        else:
            start_clock = self._start_clock

        if end:
            end_clock = self._marks[end]
        else:
            end_clock = time.monotonic()

        if mark:
            self._marks[mark] = time.monotonic()
        return round(end_clock - start_clock, ndigits)

    def mark(self, mark: str):
        self._marks[mark] = time.monotonic()

    def uptime(self, start: str | None = None, end: str | None = None) -> str:
        d = timedelta(seconds=self.elapsed(start=start, end=end))
        days = d.days
        hours, seconds = divmod(d.seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return f"{days} days,{hours:02d}:{minutes:02d}:{seconds:02d}"

    def has_mark(self, mark: str) -> bool:
        return mark in self._marks

    def get_elapseds(self, ndigits: int = 3) -> dict[str, float]:
        """获得start/end之间流逝的时间"""
        elapseds: dict[str, float] = {}
        starts: dict[str, float] = {}
        ends: dict[str, float] = {}
        for key, value in self._marks.items():
            m = re.fullmatch(r"(?P<op>start|end)[\s]*(?P<name>.+)", key)
            if m is not None:
                op = m.group("op")
                name = m.group("name")
                if op == "start":
                    starts[name] = value
                else:
                    ends[name] = value

        for name, start in starts.items():
            if name in ends:
                elapseds[name] = round(ends[name] - start, ndigits)
            else:
                # 忘记标记end？
                pass

        elapseds["total"] = self.elapsed()
        return elapseds

    @classmethod
    def start(cls) -> Self:
        return cls()


class SafeExecutor(Executor):
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(
        self,
        factory: Callable[[], Executor],
        *,
        retry_times: int = 1,
        max_idle_timeout: float | None = None,
    ):
        super().__init__()
        # assert retry_times>0
        self._retry_times: Final = retry_times
        """表示失败了再尝试多少次，0表示不尝试"""
        self._lock: Final = threading.RLock()
        self._factory: Final = factory
        self._max_idle_timeout: Final = max_idle_timeout
        self._last_submit_time: float = 0

        self._shutdown_event: Final = threading.Event()
        self._create_count: int = 0
        self._shutdown_count: int = 0
        self._impl: Executor | None = None
        self._start_idle_worker()

    def _start_idle_worker(self):
        if self._max_idle_timeout is not None and self._max_idle_timeout > 0:
            #避免长时间空闲浪费资源，也容易broken
            t = threading.Thread(
                target=self._check_idle,
                args=(self._max_idle_timeout,),
                daemon=True,
                name="check_executor_idle",
            )
            t.start()

    def _check_idle(self, timeout: float):
        while not self._shutdown_event.is_set():
            old_impl: Executor | None = None
            with self._lock:
                if (
                    self._last_submit_time > 0
                    and time.monotonic() - self._last_submit_time > timeout
                ):
                    old_impl = self._impl
                    self._impl = None

            if old_impl:
                t = threading.Thread(
                    target=self._shutown_executor,
                    args=(old_impl, "空闲"),
                    daemon=True,
                    name="shutdown_executor",
                )
                t.start()

            time.sleep(1)

    def _shutown_executor(self, executor: Executor, reason: str):
        self._shutdown_count += 1
        self._logger.info("第%s次关闭Executor，原因:%s", self._shutdown_count, reason)
        #关闭，不取消，等待，所以不影响正在运行的
        executor.shutdown()

    def _new_executor(self) -> Executor:
        with self._lock:
            self._create_count += 1
            self._last_submit_time = 0
            self._logger.info("第%s次创建Executor", self._create_count)
            return self._factory()

    @override
    def submit[**P](self, fn: Callable[P, Any], *args: P.args, **kwargs: P.kwargs):
        max_times = 1 + self._retry_times
        n = 0
        with self._lock:
            while n < max_times:
                n += 1
                if self._impl is None:
                    self._impl = self._new_executor()
                impl = self._impl
                try:
                    # TODO 如果已经shutdown？
                    self._last_submit_time = time.monotonic()
                    return impl.submit(fn, *args, **kwargs)
                except BrokenExecutor:
                    self._impl = None
                    self._logger.exception("进程池/线程池坏了")
                    self._shutown_executor(impl, "破损")
                    if n >= max_times:
                        raise

        raise RuntimeError("不会执行到这里")

    @override
    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False):
        self._shutdown_event.set()
        with self._lock:
            impl = self._impl

        if impl:
            impl.shutdown(wait, cancel_futures=cancel_futures)


def safe_write(file: str | Path, data:Any, *, encoding: str = "utf-8"):
    file = Path(file)
    file.parent.mkdir(parents=True,exist_ok=True)
    # 减少调用次数
    # file.parent.mkdir(parents=True,exist_ok=True)
    temp_file = Path(f"{file}.{uuid.uuid4().hex}{file.suffix}")
    try:
        if isinstance(data, bytes):
            temp_file.write_bytes(data)
        elif isinstance(data,str):
            temp_file.write_text(data, encoding=encoding)
        elif isinstance(data,PIL.Image.Image):
            data.save(temp_file)
        else:
            #使用orjson而不是json
            temp_file.write_bytes(orjson.dumps(data))
        # 在同文件系统中为原子操作，即使目标文件存在
        temp_file.replace(file)
    finally:
        temp_file.unlink(True)


class MyBaseModel(BaseModel):
    @classmethod
    def create(cls, args: Self | Mapping[str, Any] | str | Path | None) -> Self:
        if not args:
            return cls()
        elif isinstance(args, Mapping):
            return cls.model_validate(args)
        elif isinstance(args, str):
            return cls.model_validate_json(args)
        elif isinstance(args, Path):
            # 后续支持json/yaml/py等格式？
            return cls.model_validate(load_data(args, py_name="settings"))
        else:
            return args


class AutoCleaner:
    """
    这个对象的作用就是提供一个轻量级的对象，当task释放了，这个也会被释放，然后自动清除目录，没有直接使用doc是因为这个在解析后，太重了，不需要存在了，
    只需要保留生成的zip文件，特别是异步请求
    """

    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self, files: Sequence[str | Path]):
        super().__init__()
        self._finalizer: Final = weakref.finalize(self, self._clean, files)

    def __del__(self):
        self._logger.info("gc autocleaner")

    def clean(self):
        """手动清除"""
        if self._finalizer.alive:
            self._finalizer()

    @classmethod
    def _clean(cls, files: Sequence[str | Path]):
        for file in files:
            file = Path(file)
            cls._logger.info("clean file:%s", file)
            if file.is_dir():
                shutil.rmtree(file)
            file.unlink(True)


class thread_safe_cached_property:
    def __init__(self, func: Callable[[Any], Any]):
        self.func = func
        self.attrname = None
        self.__doc__ = func.__doc__

    def __set_name__(self, owner: Any, name: str):
        self.attrname = name
        self.lock_name = f"_lock_{name}"

    def __get__(self, instance: Any, owner: Any = None):
        if instance is None:
            return self

        # 每个实例有自己的锁，互不影响
        lock = instance.__dict__.setdefault(self.lock_name, threading.RLock())
        with lock:
            if self.attrname not in instance.__dict__:
                instance.__dict__[self.attrname] = self.func(instance)
        return instance.__dict__[self.attrname]


def is_free_threaded() -> bool:
    # -Xgil=0
    # return not sys._is_gil_enabled()
    return sys.flags.gil == 0
