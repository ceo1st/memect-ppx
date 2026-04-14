import io
import logging
import os
import re
import shutil
import zipfile
from collections.abc import Sequence
from logging import Logger
from pathlib import Path
from typing import IO, TypedDict

from . import utils
from .api import ApiError


class _Result(TypedDict):
    name: str
    size: int
    elapsed: float
    path: str


class Archiver:
    _logger: Logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self):
        super().__init__()
        self._cancelled = False

    def unzip(
        self,
        file: str | Path | bytes | IO[bytes],
        out_dir: str | Path,
        *,
        max_file_count: int | None = None,
        max_file_length: int | None = None,
        allow_names: Sequence[str] | None = None,
    ) -> list[_Result]:
        """解压到目录，为了安全，不支持多级目录
        file:
        out_dir:
        max_file_count: 允许包含的文件数
        max_file_length:包含的文件的最大字节数
        allow_names: 仅仅允许指定的文件名
        """
        self._logger.info(
            "start unzip file,max_file_count=%s,max_file_length=%s,allow_names=%s",
            max_file_count,
            max_file_length,
            allow_names,
        )
        t = utils.Timer.start()
        out_dir = Path(out_dir)
        out_dir = out_dir.absolute()
        out_dir.mkdir(parents=True, exist_ok=True)
        if isinstance(file, (bytes, bytearray)):
            file = io.BytesIO(file)
        with zipfile.ZipFile(file, mode="r", metadata_encoding="utf-8") as zf:
            infos: list[zipfile.ZipInfo] = []
            for info in zf.infolist():
                # if not info.filename.endswith('/'):
                # print(info.filename)
                if info.filename[0] == "." or re.fullmatch(
                    r"__MACOSX/.*", info.filename
                ):
                    # 如果包含了.xxx等，忽略，因为可能是手动zip的时候不小心包含了，现在放宽松一些
                    continue

                if info.is_dir():
                    raise ApiError(ApiError.ANY, "zip文件中不允许包含目录")

                # 文件
                filename = out_dir.joinpath(info.filename).absolute()
                if filename.parent != out_dir:
                    # 不支持多级路径
                    raise ApiError(
                        ApiError.ANY, f"zip文件中包含无效的文件名:{info.filename}"
                    )

                if allow_names and filename.name not in allow_names:
                    raise ApiError(
                        ApiError.ANY, f"zip包含不允许的文件名:{info.filename}"
                    )

                infos.append(info)
                if max_file_length is not None and info.file_size > max_file_length:
                    raise ApiError(
                        ApiError.ANY,
                        f"zip中的文件最大只允许:{max_file_length}，现在文件:{info.filename}的大小为:{info.file_size}",
                    )
                if max_file_count is not None and len(infos) > max_file_count:
                    raise ApiError(
                        ApiError.ANY, f"zip文件只允许最多包含:{max_file_count}个文件"
                    )

            results: list[_Result] = []
            # 如果想简单的，就这样
            # zf.extractall(out_dir,infos)
            for info in infos:
                t.mark("unzip_member")
                filename = out_dir.joinpath(info.filename).absolute()
                with zf.open(info) as source, filename.open("wb") as target:
                    shutil.copyfileobj(source, target)
                size = filename.stat().st_size
                if info.file_size != filename.stat().st_size:
                    raise ApiError(
                        ApiError.ANY,
                        f"解压文件失败，文件大小不一致:{info.file_size}!={size}",
                    )
                results.append(
                    {
                        "name": filename.name,
                        "size": size,
                        "elapsed": t.elapsed(start="unzip_member"),
                        "path": str(filename),
                    }
                )

            self._logger.info(
                "end unzip file,files=%s,max_file_count=%s,max_file_length=%s,allow_names=%s,elapsed=%.3f",
                len(results),
                max_file_count,
                max_file_length,
                allow_names,
                t.elapsed(),
            )
            return results

    def zip(
        self,
        zip_filename: str | Path | IO[bytes],
        *,
        files: Sequence[Path | str] | None = None,
        dir: Path | str | None = None,
    ):
        """
        打包指定的目录，或者打包指定的几个文件
        """

        # import shutil
        # shutil.make_archive() 这个会改变目前的工作目录，所以不使用
        def make_dir(zf: zipfile.ZipFile, dir: str | Path, relative_dir: str | Path):
            relative_dir = str(relative_dir)
            for dirpath, dirnames, filenames in os.walk(dir):
                for name in sorted(dirnames):
                    if name[0] == ".":
                        continue
                    path = os.path.join(dirpath, name)
                    zf.write(path, path[len(relative_dir) + 1 :])

                for name in filenames:
                    if name[0] == ".":
                        continue
                    path = os.path.normpath(os.path.join(dirpath, name))
                    if os.path.isfile(path):
                        zf.write(path, path[len(relative_dir) + 1 :])

        with zipfile.ZipFile(zip_filename, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if dir:
                make_dir(zf, dir, dir)
            elif files:
                for file in files:
                    file = Path(file).absolute()
                    if file.is_file():
                        # /a/b/c.txt => /c.txt
                        zf.write(file, file.name)
                    elif file.is_dir():
                        # /a/b/dir1 =>  /dir1
                        make_dir(zf, file, file.parent)
                    else:
                        pass
