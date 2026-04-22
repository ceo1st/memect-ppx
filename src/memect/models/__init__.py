import hashlib
import logging
from pathlib import Path
import threading
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
