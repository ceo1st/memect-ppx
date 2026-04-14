import importlib.util
import json
import logging
import logging.config
import os
import re
import signal
import tomllib
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Iterable, cast


def load_py(filename: str | Path, *, name: str | None = None) -> ModuleType:
    filename = Path(filename)
    spec = importlib.util.spec_from_file_location(name or filename.stem, filename)
    if spec is None:
        raise ValueError(f"不能够载入module:{filename}")
    m = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ValueError(f"不能够载入module:{filename}")
    spec.loader.exec_module(m)
    return m


def load_data(filename: str | Path, *, py_name: str = "data") -> dict[str, Any]:
    filename = Path(filename)
    suffix: str = filename.suffix
    if suffix == ".json":
        return json.loads(filename.read_text("utf-8"))
    elif suffix == ".yaml":
        import yaml

        return yaml.safe_load(filename.read_text("utf-8"))
    elif suffix == ".toml":
        return tomllib.loads(filename.read_text("utf-8"))
    elif suffix == ".py":
        return getattr(load_py(filename), py_name)
    else:
        raise ValueError(f"不支持的文件格式:{filename}")


def load_settings(
    paths: str | Path | Sequence[str | Path],
    *,
    custom_settings: Mapping[str, Any] | None = None,
    py_name: str = "settings",
    path_names: Sequence[str] | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """载入配置，按顺序查找，返回第一个找到的，支持py，json，toml，yaml格式"""
    if isinstance(paths, (str, Path)):
        paths = [paths]

    for path in paths:
        path = Path(path)
        # path = get_path(path)
        if path.is_file():
            if verbose:
                from .utils import console

                console.log(f"load settings file={path.name}")
            data = _load_settings(path, py_name=py_name)
            if custom_settings:
                set_values(data, custom_settings)

            # 调整路径属性，如果是相对路径，相对配置文件所在的目录，绝对路径不改变
            if path_names:

                def adjust_value(value: Any, cwd: Path | None = None) -> Any:
                    if isinstance(value, (str, Path)):
                        return get_path(value, cwd=cwd)
                    elif isinstance(value, Sequence):
                        return [get_path(a, cwd=cwd) for a in value]  # type: ignore
                    else:
                        return value

                for pn in path_names:
                    set_value(
                        data,
                        pn,
                        fn=lambda old, _: adjust_value(old, cwd=path.parent),
                        force=False,
                    )

            return data
    raise ValueError(f"没有一个路径存在:{paths}")


def _load_settings2(default_file:str|Path,custom_settings:Mapping[str,Any]|None=None)->Any:
    """先读取默认配置，然后再合并自定义配置，再合并来自命令行的配置"""
    default_file = Path(default_file)
    name = default_file.name.split('.')[0]
    data = load_data(default_file,py_name='settings')
    custom_file=Path(f'./conf/{name}.py')
    for custom_file in [Path(f'./conf/{name}.py'),Path(f'./conf/{name}.json')]:
        if custom_file.is_file():
            set_values(data,load_data(custom_file,py_name='settings'))
            break
    
    if custom_settings:
        set_values(data,custom_settings)
    return data


def set_values(
    data: MutableMapping[str, Any],
    values: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
    *,
    fn: Callable[[Any, Any], Any] | None = None,
    force: bool = True,
):
    if not values:
        return
    if isinstance(values, Mapping):
        items = values.items()
    else:
        items = values

    for k, v in items:
        if fn is not None:
            set_value(data, k, fn=fn, force=force)
        else:
            set_value(data, k, value=v, force=force)


def set_value(
    data: MutableMapping[str, Any],
    key: str,
    *,
    value: Any = ...,
    fn: Callable[[Any, Any], Any] | None = None,
    force: bool = True,
):
    # 为了支持a."b.c".d => x['a']['b.c']['d']
    # 需要使用>=python3.12
    matchs = re.findall(r'([^".]+|(?P<q>")?[^"]+(?(q)"))[.]?', key)
    names: list[str] = []
    for m in matchs:
        m = m[0]
        if m[0] == '"' and m[-1] == '"':
            m = m[1:-1]
        names.append(m)
    # names = key.split('.')
    obj = data
    for name in names[:-1]:
        v: Any = obj.get(name, None)
        if not isinstance(v, MutableMapping):
            if force:
                v = {}
                obj[name] = v
                obj = v
            else:
                obj = None
                break
        else:
            obj = cast(MutableMapping[str, Any], v)

    if obj is None:
        return

    name = names[-1]
    if force or name in obj:
        if fn is not None:
            obj[name] = fn(obj.get(name, ...), value)
        else:
            obj[name] = value
        # TODO 表示删除？
        if obj[name] is ...:
            del obj[name]


def _load_settings(
    filename: str | Path, *, py_name: str = "settings"
) -> dict[str, Any]:
    filename = Path(filename)
    data: dict[str, Any] = load_data(filename, py_name=py_name)
    template_path: str | Path | None = data.pop("$extend", None)
    sets: dict[str, Any] | None = data.pop("$set", None)
    template: dict[str, Any] = {}
    if template_path:
        template_path = get_path(template_path, cwd=filename.parent)
        if not template_path.is_file():
            raise ValueError(f"模版文件不存在:{template_path}")
        template = _load_settings(template_path)
        # 仅仅设置模版
        set_values(template, sets)
    template.update(data)
    return template


def get_path(
    path: str | Path,
    *,
    cwd: str | Path | None = None,
    schemes: Mapping[str, str | Path] | None = None,
) -> Path:
    # app://a/b/c.txt
    if cwd is None:
        cwd = Path.cwd()
    else:
        cwd = Path(cwd)

    new_schemes: dict[str, Path] = {}
    new_schemes["app"] = Path(".").resolve()
    new_schemes["project"] = Path(".").resolve()
    if schemes:
        for k, v in schemes.items():
            new_schemes[k] = Path(v)

    scheme_names = "|".join([re.escape(v) for v in new_schemes.keys()])

    path = str(path)
    m = re.fullmatch(rf"(?P<scheme>{scheme_names})[:]//(?P<path>.+)", path)
    if m is not None:
        scheme = m.groupdict()["scheme"]
        path = m.groupdict()["path"]
        p = new_schemes[scheme].joinpath(path).absolute()
    else:
        # 如果不是app://，path可以为绝对路径或者相对路径
        p = cwd.joinpath(path).absolute()

    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_paths(
    paths: Iterable[str | Path],
    *,
    cwd: str | Path | None = None,
    schemes: Mapping[str, str | Path] | None = None,
) -> list[Path]:
    return [get_path(path, cwd=cwd, schemes=schemes) for path in paths]


class _KV:
    def parse(self, s: str) -> tuple[str, Any]:
        # a=1 or a="xx"
        m = re.fullmatch(r"(?P<name>.+?)=(?P<value>.*)", s)
        if not m:
            raise ValueError(f"错误的设置:{s}，不符合k=v形式")
        name = m.group("name")
        value: Any = m.group("value")
        name = name.strip()
        if value:
            value = value.strip()
        if value is None:
            # -s a
            value = True
        else:
            # -s a=  => '' 空字符串
            value = self._parse_value(value)
        return (name, value)

    def _parse_value(self, value: str) -> Any:
        if (
            value in ("true", "false", "null")
            or (value[0] == "{" and value[-1] == "}")
            or (value[0] == "[" and value[-1] == "]")
        ):
            # a=true or a=false or a=null or a={} or a=[]
            value = json.loads(value)
        else:
            # 为了简化字符串的输入，如果是a=1 这样的，就认为是int/float，如果是a=xyz，就认为是字符串，当然，如果是a="1"，肯定是字符串
            if value[0] == "'" and value[-1] == "'":
                # 命令行 a=\'x\' => a='x' => x 认为是字符串，虽然不是json格式
                # 命令行 a=\"x\" => a="x" => x 为json字符串
                value = value[1:-1]
            else:
                try:
                    # 命令行a=1 => a=1 => 1
                    # 命令行a=\"x\"x => a="x" => x
                    value = json.loads(value)
                except json.JSONDecodeError:
                    # 认为是字符串，不需要处理，如：
                    # a=xyz => xyz
                    pass
        return value


def parse_kvs(items: Sequence[str] | None) -> dict[str, Any]:
    """
    解析：['a.b=1','a.c=2'] => {'a.b':1,'a.c':2}
    """
    if not items:
        return {}
    data: dict[str, Any] = {}
    kv = _KV()
    for item in items:
        k, v = kv.parse(item)
        data[k] = v
    return data


_state: Final[dict[str, Any]] = {
    "done": False,
    "custom_settings": None,
    "custom_log_settings": None,
    "env_prefix": None,
    "settings": {},
}


def setup(
    settings: Mapping[str, Any] | None = None,
    log_settings: Mapping[str, Any] | None = None,
    env_prefix: str | None = None,
):
    from .utils import console

    if _state["done"]:
        return
    # 同时可以获得环境变量？如：
    # pdf2md_server_port=xxxx
    # pdf2md_server_host=xxxx
    if not env_prefix:
        # 使用当前项目的名字？
        env_prefix = f"memect_{Path('.').absolute().name}"

    console.log("config setup")
    console.rule("config setup")
    console.log(f"pid={os.getpid()}")
    console.log(f"cwd={os.path.abspath('.')}")
    console.log(f"env_prefix={env_prefix}")
    console.log(f"custom_settings={settings}")
    console.log(f"custom_log_settings={log_settings}")

    # 然后获得环境变量，然后设置？
    _state["done"] = True
    #TODO
    import memect.conf
    conf_dir = Path(memect.conf.__file__).parent
    _state["settings"] = _load_settings2(conf_dir/"settings.default.py", custom_settings=settings)
    # 设置日志
    log_cfg = _load_settings2(conf_dir/ "log.default.py", custom_settings=log_settings)
    logging.config.dictConfig(log_cfg)



def get_settings(name: str | None = None) -> Mapping[str, Any]:
    if not _state["done"]:
        raise RuntimeError("还没有执行过setup()")
    if not name:
        return _state["settings"]
    return _state["settings"].get(name) or {}


class _F:
    def __init__[**P](self, fn: Callable[P, Any], *args: P.args, **kwargs: P.kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def __call__(self):
        return self._fn(*self._args, **self._kwargs)


class MPInit:
    """在多进程的时候，执行这个初始化，应用相同的配置"""

    def __init__(self, use_log: bool = True):
        super().__init__()
        global _state

        self._env_prefix = _state["env_prefix"]
        self._custom_settings = _state["custom_settings"]
        self._custom_log_settings = _state["custom_log_settings"]
        self._use_log = use_log
        self._fn: Callable[[], Any] | None = None

    def set_fn[**P](self, fn: Callable[P, Any], *args: P.args, **kwargs: P.kwargs):
        """设置额外需要执行的操作，注意：需要支持序列化"""
        self._fn = _F(fn, *args, **kwargs)

    def __call__(self):
        # 在多进程下，也启用日志，可能会很慢
        setup(
            self._custom_settings,
            self._custom_log_settings,
            env_prefix=self._env_prefix,
        )
        # 忽略ctrl+c，等待主进程关闭释放
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        # 主进程获得，子进程不会获得，除非直接kill子进程
        # signal.signal(signal.SIGTERM, lambda s,f: print(f"子进程 {os.getpid()} 收到 SIGTERM"))
        if self._fn:
            self._fn()


def usage():
    setup({}, {})
    get_settings()
