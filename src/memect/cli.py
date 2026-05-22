# coding=utf-8
import json
import os
from pathlib import Path
import re
import sys
from typing import Annotated, Any, Literal, Sequence

import httpx
import typer

from .pdf.base import Backend, OCRMode, ParseMode, TableMode

app = typer.Typer()


def ensure_packages(
    installed_packages: Sequence[str],
    uninstalled_packages: Sequence[str] | None = None,
    tool: str | None = None,
):
    import subprocess
    import shutil
    import sys
    from importlib import metadata

    def _is_installed(pkg: str) -> bool:
        try:
            metadata.distribution(pkg)
            return True
        except metadata.PackageNotFoundError:
            return False

    def _use_uv() -> bool:
        if not shutil.which("uv"):
            return False
        cwd = Path.cwd()
        for d in [cwd, *cwd.parents]:
            if (d / "uv.lock").exists():
                return True
        return False

    if not tool:
        use_uv = _use_uv()
    elif tool == "uv":
        use_uv = True
    else:
        use_uv = False

    to_remove = [pkg for pkg in uninstalled_packages or [] if _is_installed(pkg)]
    if to_remove:
        if use_uv:
            # 使用执行的python
            subprocess.check_call(
                [
                    "uv",
                    "pip",
                    "uninstall",
                    "--no-config",
                    "--python",
                    sys.executable,
                    *to_remove,
                ]
            )
        else:
            # 使用执行的python环境
            subprocess.check_call(
                [sys.executable, "-m", "pip", "uninstall", "-y", *to_remove]
            )

    for pkg in installed_packages:
        if not _is_installed(pkg):
            if use_uv:
                subprocess.check_call(
                    [
                        "uv",
                        "pip",
                        "install",
                        "--no-config",
                        "--python",
                        sys.executable,
                        pkg,
                    ]
                )
            else:
                subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])


def _parse_pages(pages: str | None) -> list[int]:
    if not pages:
        return []

    result: set[int] = set()
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            result.update(range(int(a), int(b) + 1))
        else:
            result.add(int(part))
    return sorted(result)


def _set_device(
    cpu: str | None, cuda: str | None | None = None, cann: str | None = None
):
    if cpu:
        # 强制使用cpu，即使当前支持gpu
        os.environ["PPX_CPU"] = cpu

    if cuda:
        # 指定使用哪个设备
        os.environ["CUDA_VISIBLE_DEVICES"] = cuda

    if cann:
        # 设置CANN设备（华为昇腾）
        # 根据具体的深度学习框架设置
        os.environ["ASCEND_DEVICE_ID"] = cann
        # 或者对于某些框架：
        # os.environ['NPU_VISIBLE_DEVICES'] = cann


def _parse_llm(s: str) -> dict[str, Any]:
    from memect.base.utils import console

    s = s.strip()
    if re.fullmatch(r"http[s]?://.+", s):
        url = s
        console.log(f"get {url}/models")
        result = httpx.get(f"{url}/models").json()
        console.log(result)
        # {'data':[{},{}]}
        info = result["data"][0]
        id_ = info["id"]
        # litellm没有返回这个
        # max_model_len = info.get('max_model_len',-1)
        if "paddle" in id_:
            name = "paddle"
        elif "deepseek" in id_:
            name = "deepseek"
        elif "glm" in id_:
            name = "glm"
        else:
            raise ValueError(f"不支持的模型:{id_}，id需要包含:deepseek,paddle,glm")
        return {
            "name": name,
            "model": id_,
            "base_url": url,
            #'api_key':'',
            #'max_model_len':max_model_len
        }
    else:
        return json.loads(s)


@app.command()
def start(
    host: Annotated[str | None, typer.Option(help="监听地址")] = None,
    port: Annotated[int | None, typer.Option(help="监听端口")] = None,
    cpu: Annotated[bool, typer.Option(help="强制使用cpu，即使当前有gpu")] = False,
    cuda: Annotated[
        str | None,
        typer.Option(help="指定使用哪些gpu，等同于CUDA_VISIBLE_DEVICES的设置"),
    ] = None,
    kvs: Annotated[
        list[str] | None, typer.Option("--set", help='如：--set server.host="0.0.0.0"')
    ] = None,
    log_kvs: Annotated[
        list[str] | None,
        typer.Option("--set-log", help='如：--set-log root.level="debug"'),
    ] = None,
) -> None:
    """启动服务"""
    from .app import App
    from .base.config import parse_kvs, setup

    _set_device(cpu, cuda=cuda)
    custom_settings = parse_kvs(kvs)
    custom_log_settings = parse_kvs(log_kvs)
    if host is not None:
        custom_settings["server.host"] = host
    if port is not None:
        custom_settings["server.port"] = port
    # 自动配置日志
    setup(settings=custom_settings, log_settings=custom_log_settings)
    App.run()


@app.command()
def parse(
    file: Annotated[Path, typer.Argument(help="PDF 文件、图片文件或图片目录")],
    out_dir: Annotated[
        Path | None, typer.Option("-o", "--out-dir", help="输出目录")
    ] = None,
    as_doc: Annotated[
        bool,
        typer.Option(
            help="当file为目录且这个为true，表示为一个文档连续的页面，如：1.png,2.png,3.png"
        ),
    ] = False,
    parallel: Annotated[
        int | None,
        typer.Option(
            "-p",
            help="表示在解析单个文档的时候，同时解析多少个页面，0表示不并行执行，注意：越大需要的内存/显存就越多",
        ),
    ] = None,
    max_workers: Annotated[
        int,
        typer.Option(
            "-w",
            "--workers",
            help="如果指定的file是目录，可以设置同时执行多少个，0表示不使用多进程执行",
        ),
    ] = 0,
    pages: Annotated[str | None, typer.Option(help="页码范围，如 1-3,5")] = None,
    backend: Annotated[Backend | None, typer.Option()] = None,
    llm: Annotated[
        str | None,
        typer.Option(
            help='使用指定的llm解析，可以为url，或者json格式，如：{"name":"deepseek","base_url":"","api_key":""}'
        ),
    ] = None,
    # deepseek:Annotated[str|None,typer.Option(help="")]=None,
    # paddle:Annotated[str|None,typer.Option(help="")]=None,
    # glm:Annotated[str|None,typer.Option(help="")]=None,
    mode: Annotated[ParseMode | None, typer.Option(help="")] = None,
    ocr: Annotated[OCRMode | None, typer.Option(help="")] = None,
    table: Annotated[TableMode | None, typer.Option(help="")] = None,
    formula:Annotated[str|None,typer.Option(help='可以指定解析公式的paddle/glm的url，或者no|pp|mfr|paddle|glm，指定paddle/glm，需要先配置url，no表示不解析公式，仅仅保存为图片')]=None,
    # remove_watermark:Annotated[bool|None,typer.Option(help='设置是否需要清除水印')]=None,
    # all:Annotated[bool,typer.Option()]=None,
    docx: Annotated[bool | None, typer.Option(help="")] = None,
    pptx: Annotated[bool | None, typer.Option(help="")] = None,
    md: Annotated[bool | None, typer.Option(help="")] = None,
    html: Annotated[bool | None, typer.Option(help="")] = None,
    doc_json: Annotated[bool | None, typer.Option("--json", help="")] = None,
    cpu: Annotated[
        Literal["all", "ocr", "layout","table","formula"] | None,
        typer.Option(help="强制使用cpu，即使当前有gpu"),
    ] = None,
    cuda: Annotated[
        str | None,
        typer.Option(help="指定使用哪些gpu，等同于CUDA_VISIBLE_DEVICES的设置"),
    ] = None,
    # 如果修改个别参数，通过--set --set-log 会简便
    # 如果修改多个参数，通过./conf/settings.py,./conf/log.py
    kvs: Annotated[
        list[str] | None, typer.Option("--set", help='如：--set server.host="0.0.0.0"')
    ] = None,
    log_kvs: Annotated[
        list[str] | None,
        typer.Option("--set-log", help='如：--set-log root.level="debug"'),
    ] = None,
    conf: Annotated[Path, typer.Option(help="自定义的配置目录")] = Path("./conf"),
    dev: Annotated[
        bool | None,
        typer.Option(
            help="开发模式，保存中间结果和使用缓存结果，如果两次之间参数改变过大，建议删除缓存"
        ),
    ] = None,
    debug: Annotated[
        bool, typer.Option("-x", "--debug", help="输出调试信息和调试图片等")
    ] = False,
    params_text: Annotated[
        str | None, typer.Option("--params", help="解析参数，JSON 字符串")
    ] = None,
    params_file: Annotated[
        Path | None, typer.Option(help="解析参数文件，JSON 文件")
    ] = None,
    dry: Annotated[bool, typer.Option(help="表示仅仅测试设置参数等，不执行")] = False,
) -> None:
    """解析 PDF 文件"""
    from .base.config import setup, parse_kvs
    from .base.debug import XDebugger
    from .pdf.base import KDocumentFactory, ParseParams
    from .pdf.parser import Parser
    from .base.utils import console

    def set_custom_values(
        settings: dict[str, Any], text: str | dict[str, Any] | None, prefix: str
    ):
        if not text:
            return
        if isinstance(text, str):
            data = json.loads(text)
        else:
            data = text
        for k, v in data.items():
            settings[f"{prefix}.{k}"] = v

    custom_settings: dict[str, Any] = {}
    log_custom_settings: dict[str, Any] = {}
    if kvs:
        custom_settings.update(parse_kvs(kvs))
    if log_kvs:
        log_custom_settings.update(parse_kvs(log_kvs))
    
    params = ParseParams.create(params_file or params_text)

    if llm:
        llm_args = _parse_llm(llm)
        name = llm_args.pop("name")
        backend = Backend(name)
        set_custom_values(custom_settings, llm_args, f"pdf_parser.{name}.model")
    else:
        # 常用的设置，更加简便
        # set_custom_values(custom_settings,deepseek,'pdf_parser.deepseek.model')
        # set_custom_values(custom_settings,paddle,'pdf_parser.paddle.model')
        # set_custom_values(custom_settings,glm,'pdf_parser.glm.model')
        pass



    if formula:
        if formula == 'no':
            params.formula=False
        elif formula in ('paddle','glm'):
            custom_settings['model_manager.executors.formula.model']=formula
        elif formula == 'mfr':
            custom_settings['model_manager.executors.formula.model']='formula-mfr'
        elif formula =='pp':
            custom_settings['model_manager.executors.formula.model']='formula-pp'
        else:
            formula_args = _parse_llm(formula)
            if formula_args['name'] not in ('paddle','glm'):
                raise ValueError('')
            custom_settings['model_manager.executors.formula.model']=formula_args['name']
            custom_settings[f'model_manager.models.{formula_args['name']}.model']=formula_args['model']
            custom_settings[f'model_manager.models.{formula_args['name']}.client.base_url']=formula_args['base_url']
            


    if parallel is not None:
        # 如果使用gpu，将需要更大的内存
        for n in ["ocr", "layout", "formula", "table_det"]:
            custom_settings[f"model_manager.executors.{n}.max_workers"] = parallel

    _set_device(cpu, cuda=cuda)

    setup(settings=custom_settings, conf_dir=conf)

    if debug:
        XDebugger.setup()

    
    if dev is not None:
        params.dev = dev
    if backend is not None:
        params.backend = backend

    if mode is not None:
        params.mode = mode

    if pages:
        params.pagenos = _parse_pages(pages)

    # if remove_watermark is not None:
    # params.remove_watermark=remove_watermark

    if ocr is not None:
        params.ocr = ocr
    if table is not None:
        params.table = table

    if pptx is not None:
        params.pptx = pptx

    if docx is not None:
        params.docx = docx

    if md is not None:
        params.markdown = md
    if doc_json is not None:
        params.doc_json = doc_json
    
    if html is not None:
        params.html = html

    # 表示为多个文件，需要并行吗？可能需要比较多的内存
    def get_docs(dir_: Path):
        if dir_.is_file():
            yield KDocumentFactory(dir_, params, out_dir)
        elif dir_.is_dir() and as_doc:
            # 表示为一个文档连续的页面，如：1.png,2.png,3.png
            yield KDocumentFactory(dir_, params, out_dir)
        else:
            for file in dir_.iterdir():
                if (
                    file.is_file()
                    and file.name[0] != "."
                    and file.suffix.lower()
                    in (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp")
                ):
                    file_out_dir = None
                    if out_dir is not None:
                        # 表示输出到这个目录，为了统一，同样添加".out"
                        file_out_dir = out_dir.joinpath(file.name + ".out")
                    yield KDocumentFactory(file, params, file_out_dir)
                else:
                    pass

    # 考虑到文件数不会太多，为了获得总数，使用list
    if dry:
        docs = list(get_docs(file))
        console.print(params)
        console.log(f"共需要解析:{len(docs)}")

    else:
        # 如果已经启动了apiserver，可以在server执行，如果又是本地，可以直接读写文件，避免上传下载
        # 如果是远程，和正常一样调用
        try:
            Parser.batch(list(get_docs(file)), max_workers=max_workers)
        finally:
            pass


@app.command()
def pdf2image(
    file: Annotated[Path, typer.Argument(help="PDF文件")],
    out_dir: Annotated[
        Path | None, typer.Option("-o", "--out-dir", help="输出目录")
    ] = None,
    pages: Annotated[str | None, typer.Option(help="页码范围，如 1-3,5")] = None,
    scale: Annotated[int | None, typer.Option(help="设置scale")] = None,
    max_size: Annotated[
        str | None, typer.Option(help="设置最大宽度和高度，如：2000，或者2000,10000")
    ] = None,
    chunk_size: Annotated[
        int | None, typer.Option(help="设置每批的大小，如：10")
    ] = None,
    use_job: Annotated[
        bool, typer.Option(help="设置使用job的方式执行，主要是测试作用")
    ] = False,
    dev: Annotated[bool, typer.Option(help="开发模式，跳过pdf2image")] = False,
):

    pass


@app.command()
def test(
    dir: Annotated[Path, typer.Argument(help="测试该目录下的pdf或者图片")],
    url: Annotated[
        str | None, typer.Option(help="设置请求的url，默认使用当前环境的")
    ] = None,
    max_workers: Annotated[int, typer.Option(help="同时执行多少个")] = 5,
):
    """测试api"""
    from memect.base.config import setup
    from memect.base.test import Tester

    setup()
    tester = Tester(url=url, max_workers=max_workers)
    tester.run(dir)


@app.command(help="提前下载好需要的模型")
def download():
    from memect.base.config import setup
    from memect.models import download_all

    setup()
    download_all()


def detect_gpu() -> dict[str, bool]:
    import subprocess
    import shutil

    result: dict[str, bool] = {"nvidia": False, "cann": False}
    # 检测 NVIDIA
    try:
        nvidia_smi = shutil.which("nvidia-smi")
        if nvidia_smi:
            out = subprocess.run([nvidia_smi], capture_output=True, text=True)
            result["nvidia"] = out.returncode == 0
    except FileNotFoundError:
        result["nvidia"] = False

    # 检测 昇腾 CANN
    try:
        npu_smi = shutil.which("npu-smi")
        if npu_smi:
            out = subprocess.run([npu_smi, "info"], capture_output=True, text=True)
            result["cann"] = out.returncode == 0
    except FileNotFoundError:
        # 兜底：检查设备文件
        result["cann"] = os.path.exists("/dev/davinci_manager")

    return result


@app.command(help="根据需求安装其他的包，避免冲突")
def install(
    pip: Annotated[bool, typer.Option(help="表示使用pip来安装包")] = False,
    gpu: Annotated[
        Literal["auto", "cuda", "cann", "dml", "no"],
        typer.Option(help="安装哪个gpu的库"),
    ] = "auto",
    headless: Annotated[bool, typer.Option(help="表示opencv安装headless")] = False,
):
    from memect.base.utils import console

    #TODO 这个和onnxruntime的版本需要一致，如果安装的是onnxruntime,cu12.6,cu12.8
    cuda_packages = [
        "nvidia-cuda-runtime-cu12",
        "nvidia-cudnn-cu12",
        "nvidia-cublas-cu12",
        "nvidia-cufft-cu12",
        "nvidia-curand-cu12",
        "nvidia-cuda-nvrtc-cu12",
        # "nvidia-nvjpeg-cu12",
        "nvidia-nvjitlink-cu12",
    ]
    if pip:
        tool = "pip"
    else:
        # 自动判断
        tool = None

    installed_packages: list[str] = []
    uninstalled_packages = [
        "onnxruntime",
        "onnxruntime-gpu",
        "onnxruntime-cann",
        "onnxruntime-directml",
    ]
    if gpu == "auto":
        # 如果是windows，安装onnxruntime-directml
        # 如果是linux，安装onnxruntime-gpu
        # 如果是mac，不需要安装
        result = detect_gpu()
        console.log(f"detect gpu:{result}")
        if result["cann"]:
            installed_packages.append("onnxruntime-cann")
        elif result["nvidia"]:
            if sys.platform == "win32":
                # windows下，2080/3090显卡，使用cuda比使用directml慢
                installed_packages.append("onnxruntime-directml")
            else:
                installed_packages.append("onnxruntime-gpu")
                installed_packages.extend(cuda_packages)
        else:
            installed_packages.append("onnxruntime")
    elif gpu == "cuda":
        installed_packages.append("onnxruntime-gpu")
        installed_packages.extend(cuda_packages)
    elif gpu == "cann":
        installed_packages.append("onnxruntime-cann")
    elif gpu == "dml":
        installed_packages.append("onnxruntime-directml")
    else:
        installed_packages.append("onnxruntime")

    console.log(
        f"gpu={gpu},install packages={installed_packages},uninstall packages={uninstalled_packages}"
    )
    ensure_packages(installed_packages, uninstalled_packages, tool=tool)

    # opencv-python
    uninstalled_packages = [
        "opencv-python",
        "opencv-python-headless",
        "opencv-contrib-python",
        "opencv-contrib-python-headless",
    ]
    if headless:
        installed_packages = ["opencv-contrib-python-headless"]
    else:
        installed_packages = ["opencv-contrib-python"]

    console.log(
        f"install packages={installed_packages},uninstall packages={uninstalled_packages}"
    )
    ensure_packages(installed_packages, uninstalled_packages, tool=tool)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
elif __name__ == "__mp_main__":
    print("mpx")
