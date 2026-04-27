from pathlib import Path

from rapid_latex_ocr.utils import DownloadModel

def patch_url():
    #old_init=DownloadModel.__init__
    def new_init(self) -> None:
        #self.url = "https://github.com/RapidAI/RapidLaTeXOCR/releases/download/v0.0.0"
        self.url = "https://modelscope.cn/models/Memect/rapid_latex_ocr/resolve/v1.0.0"
        self.cur_dir = Path(__file__).resolve().parent
    DownloadModel.__init__=new_init

patch_url()