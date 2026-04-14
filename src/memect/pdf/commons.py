
import io
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import PIL.Image

from memect.base import images


@dataclass
class FileInfo:
    file: str | Path | bytes|PIL.Image.Image|np.ndarray #npt.NDArray[np.uint8]
    rotation: int = 0
    """这个度数应该去掉，如果需要rotation"""
    params: Mapping[str,Any]|None=None

    @cached_property
    def size(self)->tuple[int,int]:
        """获得图片的大小"""
        return self.pil_image.size
    
    @cached_property
    def pil_image(self)->PIL.Image.Image:
        if isinstance(self.file,PIL.Image.Image):
            if self.rotation==0:
                return self.file
            else:
                return self.file.rotate(-self.rotation,expand=True)
        elif isinstance(self.file,np.ndarray):
            #bgr to rgb
            img = images.cv2_to_pil(self.file)
            if self.rotation==0:
                return img
            else:
                return img.rotate(-self.rotation,expand=True)
        else:
            return images.open(self.file,rotation=self.rotation)

    @cached_property
    def cv2_image(self)->np.ndarray:
        if isinstance(self.file,np.ndarray):
            if self.rotation==0:
                return self.file
            else:
                return images.rotate_cv2(self.file,self.rotation)
        elif isinstance(self.file,PIL.Image.Image):
            img = images.pil_to_cv2(self.file)
            if self.rotation==0:
                return img
            else:
                return images.rotate_cv2(img,self.rotation)
        else:
            return images.open_cv2(self.file,rotation=self.rotation)
    
    def read_bytes(self) -> bytes:
        if self.rotation==0:
            if isinstance(self.file, bytes):
                return self.file
            elif isinstance(self.file,(str,Path)):
                return Path(self.file).read_bytes()
            else:
                #pil或者cv2图片，都使用pil
                with io.BytesIO() as buf:
                    self.pil_image.save(buf,format='png')
                    return buf.getvalue()

        else:
            with io.BytesIO() as buf:
                self.pil_image.save(buf,format='png')
                return buf.getvalue()
    
    def to_url(self,*,format:str|None=None,size:tuple[int,int]|None=None,max_size:tuple[int,int]|None=None)->tuple[PIL.Image.Image,str]:
        file:str|Path|bytes|None=None
        if isinstance(self.file,(str,Path,bytes)):
            #如果不需要做任何改变，可以直接使用原始数据，不需要编码和解码
            file = self.file
        return images.to_url(self.pil_image,format=format,size=size,max_size=max_size,file=file)





