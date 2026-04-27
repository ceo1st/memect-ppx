import functools
import inspect
import logging
import re
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import KW_ONLY, dataclass, field
from inspect import Traceback
from logging import Logger
from pathlib import Path
from types import FrameType
from typing import Any, ClassVar, Final, Literal, cast

# import regex

from memect.base import utils
from memect.base.utils import console


# 类型默认值需要3.13版本，如：T=str
# 暂时就先不使用了


@dataclass
class MatchStats:
    count: int = 0
    """匹配的次数"""
    min_match: re.Match[str] | None = None
    max_match: re.Match[str] | None = None
    min_elapsed: float = -1
    max_elapsed: float = -1
    avg_elapsed: float = -1
    total_elapsed: float = 0

    def log(self, match: re.Match[str], elapsed: float):
        self.count += 1
        self.total_elapsed += elapsed
        self.avg_elapsed = self.total_elapsed / self.count
        if self.min_elapsed == -1:
            self.min_elapsed = elapsed
            self.min_match = match
        else:
            if self.min_elapsed > elapsed:
                self.min_elapsed = elapsed
                self.min_match = match
            else:
                pass

        if self.max_elapsed == -1:
            self.max_elapsed = elapsed
            self.max_match = match
        else:
            if self.max_elapsed < elapsed:
                self.max_elapsed = elapsed
                self.max_match = match
            else:
                pass


@dataclass
class OPStats:
    _: KW_ONLY
    op: str
    count: int = 0
    """执行的次数"""
    iter_pattern_count: int = 0
    """表示执行pattern的次数"""
    min_elapsed: float = -1
    max_elapsed: float = -1
    avg_elapsed: float = -1
    total_elapsed: float = 0
    match: MatchStats = field(init=False, default_factory=MatchStats)

    def log(self, match: re.Match[str] | None, elapsed: float):
        self.count += 1
        self.total_elapsed += elapsed
        self.avg_elapsed = self.total_elapsed / self.count
        if self.min_elapsed == -1:
            self.min_elapsed = elapsed
        else:
            self.min_elapsed = min(self.min_elapsed, elapsed)

        if self.max_elapsed == -1:
            self.max_elapsed = elapsed
        else:
            self.max_elapsed = max(self.max_elapsed, elapsed)

        if match is not None:
            self.match.log(match, elapsed)


@dataclass
class PatternStats:
    _: KW_ONLY
    tb: Traceback
    count: int = 0
    """创建的次数"""
    ops: dict[str, OPStats] = field(default_factory=dict)
    min_elapsed: float = -1
    max_elapsed: float = -1
    avg_elapsed: float = -1
    total_elapsed: float = 0

    def log(self, op: str, match: re.Match[str] | None, elapsed: float):
        stats = self.ops.get(op)
        if stats is None:
            stats = OPStats(op=op)
            self.ops[op] = stats
        stats.log(match, elapsed)

    def log_create(self, elapsed: float):
        self.count += 1
        self.total_elapsed += elapsed
        self.avg_elapsed = self.total_elapsed / self.count
        if self.min_elapsed == -1:
            self.min_elapsed = elapsed
        else:
            self.min_elapsed = min(self.min_elapsed, elapsed)

        if self.max_elapsed == -1:
            self.max_elapsed = elapsed
        else:
            self.max_elapsed = max(self.max_elapsed, elapsed)


class Stats:
    def __init__(self):
        super().__init__()
        self._lock = threading.RLock()
        self._patterns: dict[tuple[str, int], PatternStats] = {}

    @property
    def patterns(self) -> dict[tuple[str, int], PatternStats]:
        return self._patterns

    def add_pattern(self, pattern: "XPattern", elapsed: float):
        if pattern.key is None or pattern.tb is None:
            raise ValueError("")
        with self._lock:
            # 使用文件路径+行号即可，因为目的是记录在哪个位置创建的XPattern，即使创建参数不一致也可以
            key = pattern.key
            # 存储的是PatternStats对象，而不是XPattern，因为XPattern可以反复创建（即使同一个位置）
            stats = self._patterns.get(key)
            if not stats:
                stats = PatternStats(tb=pattern.tb)
                self._patterns[key] = stats
            stats.log_create(elapsed)

    def log(
        self, pattern: "XPattern", op: str, match: re.Match[str] | None, elapsed: float
    ):
        if pattern.key is None:
            raise ValueError("")

        with self._lock:
            stats = self._patterns[pattern.key]
            stats.log(op, match, elapsed)

    def print(self):
        console = utils.console
        for key, stats in self.patterns.items():
            console.print(key, stats)

            for op in stats.ops.items():
                console.print(op)


def _watch[**P, T](fn: Callable[P, T]) -> Callable[P, T]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        self: XPattern = cast(XPattern, args[0])
        self.ensure_init()
        if not self.enable_stats:
            return fn(*args, **kwargs)
        timer = utils.Timer.start()
        op: str = fn.__name__
        match: re.Match[str] | None = None
        try:
            # 如果执行的是findall或者finditer，就不统计match了
            result  = fn(*args, **kwargs)
            if isinstance(result, re.Match):
                match = result # type: ignore
            return cast(T,result)
        finally:
            self.stats.log(self, op, match, timer.elapsed())

    return wrapper

def _getframe(level:int=0)->FrameType:
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
def _getframeinfo(level:int=0)->inspect.Traceback:
    """获得调用frame的信息，level=0表示获得当前调用frame的信息，level=1表示再上一级"""
    level+=1
    method=1
    if method==1:
        return inspect.getframeinfo(_getframe(level))
    else:
        stack = inspect.stack()
        return inspect.getframeinfo(stack[min(level,len(stack)-1)][0])
    
class XPattern:
    _logger: Logger = logging.getLogger(f"{__module__}.{__qualname__}")
    """如果是使用fullmatch/match，join=True和False的结果是一样的，只是性能有差异，search/findall/finditer结果是不一样的
    所以，对于search，join=True表示合并在一起查找，join=False，表示按顺序查找（也就是有优先级）
    """
    # 如果实体没有设置自己的，就表示使用全局的对象，也就是全局统计
    stats: Stats = Stats()
    enable_stats: bool = False
    force_lazy: bool | None = None
    """True表示所有的都延迟载入，False表示所有的都马上载入（快速测试对错），None表示由参数lazy决定"""
    # 默认为join，目的是方便统一切换没有指定的，如果没有特别的要求，join的性能更好
    # 如果在构造XPattern的时候，不允许join的，必须join=False
    default_join: ClassVar[bool] = True

    # 或者直接使用re/regex对象？目的是方便统一切换（方便测试性能），regex可以替代re，但是re不可以替代regex（多了一些语法），re速度快
    # 如果在构造XPattern的时候，必须使用regex的，需要impl='regex'
    # default_impl:ClassVar[Literal['re','regex']]='re'
    def __init__(
        self,
        op: Literal["fullmatch", "match", "search"],
        patterns: str | re.Pattern[str] | Iterable[str | re.Pattern[str]] | None = None,
        *,
        texts: Iterable[str] | None = None,
        files: Iterable[str | Path] | None = None,
        flags: int = 0,
        join: bool | None = None,
        lazy: bool = True,
        recursive: bool = False,
        strict: bool = True,
        stats_mode: Literal["self", "global", "off"] = "global",
        context: Literal["table", "section"] | None = None,
        comment: str | None = None,
    ):
        super().__init__()
        if isinstance(patterns, str | re.Pattern):  # |regex.Pattern):
            patterns = [patterns]

        # 允许为None的时候使用默认值，是为了方便测试join=True和False的性能，也可以强制必须设置为join=True/False
        if join is None:
            if op in ("search",):
                join = True
                # raise ValueError(f'op={op}的时候，必须设置join=True或者False')
            else:
                # fullmatch/match，如果没有指定的，表示在join=True和False都是可以的，这样可以统一切换测试性能
                join = self.default_join
        
        self.re:Final= re
        self.op: str = op
        """指定仅仅允许使用哪个操作"""
        self.flags: int = flags

        # re.Pattern or regex.Pattern
        self.patterns: list[re.Pattern[str]] = []

        self.comment: str | None = comment
        self.context: str | None = context

        # 统计的时候需要
        self.key: tuple[str, int] | None = None
        self.tb: Traceback | None = None
        # 不使用StrEnum?
        self.stats_mode: str = stats_mode

        if stats_mode == "off":
            # 表示禁用统计
            self.enable_stats = False
            # 也使用自己的
            self.stats = Stats()
        elif stats_mode == "self":
            # 表示独立统计，方便仅仅查看某个对象
            self.enable_stats = True
            self.stats = Stats()
        elif stats_mode == "global":
            # 表示使用全局统计，不需要设置任何内容
            pass
        if self.enable_stats:
            # 某个XPattern可以单独设置为self.enable_status=False，表示该对象不需要统计，反之设置为True需要
            self.tb = _getframeinfo(level=1)
            # 使用文件路径+行号即可，因为目的是记录在哪个位置创建的XPattern，即使创建参数不一致也可以
            self.key = (self.tb.filename, self.tb.lineno)

        def do_init():
            timer = utils.Timer.start()
            self._load(
                patterns, texts, files, join=join, recursive=recursive, strict=strict
            )
            if self.enable_stats:
                self.stats.add_pattern(self, timer.elapsed())

        self._lazy_init: Callable[[], Any] | None = None

        if self.force_lazy is not None:
            lazy = self.force_lazy

        if not lazy:
            do_init()
        else:
            # 第一次调用的时候才载入初始化
            self._lazy_init = do_init

    def ensure_init(self):
        # 为了可以准确的记录fullmatch/match等时间，在_watch中调用即可，虽然不是很好，暂时如此
        if self._lazy_init is None:
            return
        init = self._lazy_init
        self._lazy_init = None
        init()

    def _load(
        self,
        patterns: Iterable[str | re.Pattern[str]] | None,
        texts: Iterable[str] | None,
        files: Iterable[str | Path] | None,
        *,
        join: bool = True,
        recursive: bool = False,
        strict: bool = True,
    ):
        all_patterns: list[str | re.Pattern[str]] = []
        if patterns:
            self._load_patterns(all_patterns, patterns, strict=strict)

        if texts:
            self._load_texts(all_patterns, texts, strict=strict)

        if files:
            self._load_files(all_patterns, files, recursive=recursive, strict=strict)

        if join:
            self.patterns.append(self._join(all_patterns))
        else:
            for pattern in all_patterns:
                if isinstance(pattern, str):
                    self.patterns.append(self.re.compile(pattern, self.flags))
                else:
                    self.patterns.append(pattern)

    def _load_texts(
        self,
        patterns: list[str | re.Pattern[str]],
        texts: Iterable[str],
        strict: bool = True,
    ):
        for text in texts:
            pattern = self.re.escape(text)
            if strict and pattern in patterns:
                raise ValueError(f"存在相同的pattern:{pattern},text={text}")
            patterns.append(pattern)

    def _load_patterns(
        self,
        patterns: list[str | re.Pattern[str]],
        new_patterns: Iterable[str | re.Pattern[str]],
        strict: bool = True,
    ):
        for pattern in new_patterns:
            if strict and pattern in patterns:
                raise ValueError(f"存在相同的pattern:{pattern}")
            patterns.append(pattern)

    def _load_files(
        self,
        patterns: list[str | re.Pattern[str]],
        files: Iterable[str | Path],
        recursive: bool = False,
        strict: bool = True,
    ):

        def load_text(text: str, strict: bool):
            lines = text.splitlines()
            if not lines:
                return

            is_text: bool = False
            for line in lines:
                # 去掉前后的空格，所以如果需要匹配前后的空格的，需要[\s]*
                line = line.strip()

                # 表示后续的为text
                if line == "#text":
                    is_text = True
                    continue

                # 表示后续的为正则表达式
                if line == "#pattern":
                    is_text = False
                    continue

                if line.startswith("#"):
                    # 注释跳过
                    continue

                if line.startswith(r"\#"):
                    # \#xxx 表示#xxxx
                    line = "#" + line[1:]

                if line:
                    if is_text:
                        pattern = self.re.escape(line)
                    else:
                        pattern = line
                    if strict and pattern in patterns:
                        # 自动过滤重复的模式？
                        if is_text:
                            raise ValueError(
                                f"存在重复的pattern={pattern},text={line}，所在文件:{file}"
                            )
                        else:
                            raise ValueError(
                                f"存在重复的pattern={pattern}，所在文件:{file}"
                            )

                    patterns.append(pattern)

        def load_data(data: Mapping[str, Any], strict: bool):
            self._load_patterns(patterns, data.get("patterns") or [], strict=strict)
            self._load_texts(patterns, data.get("texts") or [], strict=strict)

        for file in files:
            file = Path(file)
            if file.is_file():
                self._logger.info("load file=%s", file)
                if file.suffix in (".txt",):
                    load_text(file.read_text("utf-8"), strict=strict)
                elif file.suffix in (".json", ".yaml", ".yml", ".py"):
                    from memect.base.config import load_data as load

                    load_data(load(file, py_name="settings"), strict=strict)
                else:
                    raise ValueError(f"不支持的文件后缀:{file}")
            elif file.is_dir():
                # 如果是当前传递的目录，是允许的
                for child_file in file.iterdir():
                    # TODO 同时判断扩展名？这里不判断了
                    if child_file.name[0] == ".":
                        continue
                    if child_file.is_dir() and not recursive:
                        continue

                    self._load_files(
                        patterns, [child_file], recursive=recursive, strict=strict
                    )
            else:
                raise ValueError(f"文件不存在:{file}")

    def _check(self, op: str):
        if self.op != op:
            raise RuntimeError(f"仅仅允许:{self.op}，现在执行:{op}")

    def _join(self, patterns: Sequence[str | re.Pattern[str]]) -> re.Pattern[str]:
        # 如果为join，必须全部是字符串
        str_patterns: list[str] = []
        for pattern in patterns:
            if not isinstance(pattern, str):
                raise ValueError(
                    f"join=True，patterns必须全部为str，现在存在:{pattern}"
                )
            str_patterns.append(pattern)
        method = 1
        if method == 1:
            fullpattern: str = "|".join(str_patterns)
        else:
            fullpattern = "|".join([f"({p})" for p in str_patterns])
        return self.re.compile(fullpattern, self.flags)

    @_watch
    def fullmatch(
        self, s: str, pos: int = 0, endpos: int | None = None, *, trace: bool = False
    ) -> re.Match[str] | None:
        self._check("fullmatch")
        self.ensure_init()
        pos, endpos = self._ensure_pos(s, pos, endpos)
        for p in self.patterns:
            m = p.fullmatch(s, pos, endpos)
            if trace:
                console.log(
                    "fullmatch", {"text": s, "pos": pos, "endpos": endpos, "match": m}
                )
            if m is not None:
                return m
        return None

    @_watch
    def match(
        self, s: str, pos: int = 0, endpos: int | None = None, *, trace: bool = False
    ) -> re.Match[str] | None:
        self._check("match")
        self.ensure_init()
        pos, endpos = self._ensure_pos(s, pos, endpos)
        for p in self.patterns:
            m = p.match(s, pos, endpos)
            if trace:
                console.log(
                    "match", {"text": s, "pos": pos, "endpos": endpos, "match": m}
                )
            if m is not None:
                return m
        return None

    @_watch
    def search(
        self, s: str, pos: int = 0, endpos: int | None = None, *, trace: bool = False
    ) -> re.Match[str] | None:
        self._check("search")
        self.ensure_init()
        pos, endpos = self._ensure_pos(s, pos, endpos)
        for p in self.patterns:
            m = p.search(s, pos, endpos)
            if trace:
                console.log(
                    "search", {"text": s, "pos": pos, "endpos": endpos, "match": m}
                )
            if m is not None:
                return m
        return None

    @_watch
    def findall(
        self, s: str, pos: int = 0, endpos: int | None = None, *, trace: bool = False
    ) -> list[str]:
        self._check("search")
        self.ensure_init()
        pos, endpos = self._ensure_pos(s, pos, endpos)
        items: list[str] = []
        i = pos
        while i < endpos:
            m = self.search(s, i, endpos)
            if m is not None:
                items.append(m.group())
                i = m.end()
            else:
                break
        if trace:
            console.log(
                "findall", {"text": s, "pos": pos, "endpos": endpos, "items": items}
            )
        return items

    @_watch
    def finditer(
        self, s: str, pos: int = 0, endpos: int | None = None, *, trace: bool = False
    ) -> Iterator[re.Match[str]]:
        self._check("search")
        self.ensure_init()
        pos, endpos = self._ensure_pos(s, pos, endpos)
        while pos < endpos:
            m = self.search(s, pos, endpos)
            if trace:
                console.log(
                    "finditer", {"text": s, "pos": pos, "endpos": endpos, "match": m}
                )
            if m:
                yield m
                pos = m.end()
            else:
                break

    @_watch
    def alike(self, texts: Sequence[str], strict: bool = True) -> bool:
        if len(texts) < 2:
            raise ValueError("")

        if self.op == "fullmatch":
            fn = self.fullmatch
        elif self.op == "match":
            fn = self.match
        elif self.op == "search":
            fn = self.search
        else:
            raise RuntimeError("")

        last_match: re.Match[str] | None = None
        for s in texts:
            m = fn(s)
            if m is None:
                return False

            if strict:
                # 严格模式还需要判断是否为同一个正则
                if last_match is None:
                    last_match = m
                elif last_match.re is not m.re:
                    return False
                else:
                    pass
            else:
                pass

        return True

    def _ensure_pos(
        self, s: str, pos: int | None, endpos: int | None = None
    ) -> tuple[int, int]:
        if pos is None:
            pos = 0
        if endpos is None:
            endpos = len(s)

        if endpos < 0:
            endpos += len(s)
        return pos, endpos

    @staticmethod
    def b2q(s: str) -> tuple[str, str]:
        """
        半角字符和全角字符
        """
        if len(s) != 1:
            raise ValueError(f"只能够为1个字符:{s}")
        c = ord(s)
        if 0x21 <= c <= 0x7E:
            # 半角
            return (chr(c), chr(c + 65248))
        elif 0xFF01 <= c <= 0xFF5E:
            # 全角
            return (chr(c - 65248), chr(c))
        elif c == 32 or c == 12288:
            # 空格
            return (chr(32), chr(12288))
        else:
            return (s, s)
