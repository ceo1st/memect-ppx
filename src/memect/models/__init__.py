import hashlib
import logging
import threading
from pathlib import Path
from typing import Any, Final

import httpx
from filelock import FileLock

# 这个只是支持多线程下
_lock: Final = threading.Lock()

# 支持多进程，因为可能同时启动多个进程
_download_lock = FileLock(Path(__file__).parent.joinpath("download.lock"))

# 如果模型更新了，上传新的模型，使用新的版本
# 用户需要更新代码才能够使用新的模型
_models: dict[str, Any] = {
    "table_det.onnx": {
        "url": "https://modelscope.cn/models/Memect/memect-table-det/resolve/v1.0.0/table_det.onnx",
        "sha256": "c267cafe004067be73c44cc3aa7990f34e1026c467464372fa6843500f5da1c2",
        "verified": False,
    }
}


def get_model_path(name: str):
    logger = logging.getLogger(f"{__name__}")
    path = Path(__file__).parent.joinpath(name)
    cfg = _models[path.name]

    def has_model():
        if cfg["verified"]:
            return True

        if path.is_file():
            hash = hashlib.sha256(path.read_bytes()).digest().hex()
            if hash == cfg["sha256"]:
                logger.info("模型已经存在:%s", name)
                cfg["verified"] = True
                return True
            else:
                logger.warning("模型已经存在但是不完整:%s", name)
        return False

    #除了第一次，其他模型已经存在了，所以只需要在本地多线程下判断即可
    with _lock:
        if has_model():
            return path
        
    #模型不存在，支持多个进程同时执行的情况
    with _download_lock:
        if has_model():
            return path
        logger.info("模型不存在，开始下载模型:%s", name)
        download(cfg["url"], path)
        hash = hashlib.sha256(path.read_bytes()).digest().hex()
        if hash != cfg["sha256"]:
            # 模型更新了？代码没有更新
            raise RuntimeError("下载的模型不完整")
        cfg["verified"] = True
        return path


def download(url: str, file: Path):
    with httpx.stream("GET", url, follow_redirects=True) as r:
        total = int(r.headers.get("content-length", 0))
        from rich.progress import Progress

        with Progress() as progress:
            task = progress.add_task(f"[cyan]{file.name}", total=total or None)
            with file.open("wb") as f:
                for chunk in r.iter_bytes(chunk_size=1024 * 64):
                    f.write(chunk)
                    progress.advance(task, len(chunk))


def download_all():
    #因为第三方库需要的下载模型，但是下载并不支持多进程也不执行多线程，也就是如果同时启动多个进程或者多个线程
    #执行就会冲突，所以先下载好

    #rapid_ocr
    download_ocr()
    #rapid_latex_ocr
    download_latex()
    #rapid_layout
    download_layout()
    #table_det
    download_table_cls()
    get_model_path('table_det.onnx')


def download_latex():
    from pathlib import Path

    from rapid_latex_ocr import LatexOCR, utils
    from rapid_latex_ocr.utils import DownloadModel
    def patch_url():
        #old_init=DownloadModel.__init__
        def new_init(self) -> None:
            #这个太慢
            #self.url = "https://github.com/RapidAI/RapidLaTeXOCR/releases/download/v0.0.0"
            self.url = "https://modelscope.cn/models/Memect/rapid_latex_ocr/resolve/v1.0.0"
            self.cur_dir = Path(utils.__file__).resolve().parent
        DownloadModel.__init__=new_init

    patch_url()

    LatexOCR()

def download_ocr():
    from rapidocr import ModelType, OCRVersion, RapidOCR
    for version in [OCRVersion.PPOCRV5, OCRVersion.PPOCRV4]:
        for model_type in [ModelType.MOBILE, ModelType.SERVER]:
            params = {
                'Det.ocr_version': version,
                'Rec.ocr_version': version,
                # 目前没有配置v5的，必须使用v4的
                'Cls.ocr_version': version,#OCRVersion.PPOCRV4,
                # server or mobile
                'Det.model_type': model_type,
                'Rec.model_type': model_type,
                #v4仅仅有mobile
                'Cls.model_type': ModelType.MOBILE if version==OCRVersion.PPOCRV4 else model_type,
            }
            RapidOCR(params=params)

def download_layout():
    from rapid_layout import ModelType
    from rapid_layout.model_handler import ModelProcessor
    ModelProcessor.get_model_path(ModelType.PP_DOC_LAYOUTV2)
    ModelProcessor.get_model_path(ModelType.PP_DOC_LAYOUTV3)

def download_table_cls():
    from table_cls import TableCls
    from table_cls.main import ModelType

    TableCls.get_model_path(ModelType.YOLO_CLS_X.value,None)
    TableCls.get_model_path(ModelType.PADDLE_CLS.value,None)
    TableCls.get_model_path(ModelType.YOLO_CLS.value,None)
    TableCls.get_model_path(ModelType.Q_CLS.value,None)