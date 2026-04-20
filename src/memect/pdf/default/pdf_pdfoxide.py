from enum import StrEnum, auto
import logging
import re
from typing import Any, Final, Mapping, Sequence, cast
import typing

from memect.base import lists
from memect.base.debug import XDebugger


from memect.base.matrix import Matrix

from memect.base.bbox import BBox, Quad

from memect.base.utils import MyBaseModel
from pdf_oxide import PdfDocument
from pdf_oxide.pdf_oxide import TextChar
from memect.pdf.base import (
    CharSource,
    KChar,
    KColor,
    KDocument,
    KFont,
    KPDFFigure,
    KPage,
    PageType,
)
from memect.base import utils


class Parser:
    def __init__(self):
        super().__init__()

    def parse(self, kdoc: KDocument):
        with PdfDocument(kdoc.file) as doc:
            for page in kdoc.working_pages:
                self._parse_page(doc, page)

    def _parse_page(self, doc: PdfDocument, kpage: KPage):
        kpage.pdf_chars.clear()
        n = kpage.number - 1
        method = 2
        if method == 1:
            p = doc.page(n)
            # pdf.set_page_rotation(0)
            for el in p.children():
                # print(el)
                # el.is_text()
                # el.is_image()
                # el.is_path()
                # el.is_table()
                pass
        else:
            # TODO 如果有旋转
            # p = pdf.page(n)
            # pdf.set_page_rotation(0)
            timer = utils.Timer.start()
            timer.mark("start chars")
            chars = doc.extract_chars(n)
            timer.mark("end chars")
            timer.mark("start images")
            images = doc.extract_images(n)
            timer.mark("end images")
            timer.mark("start lines")
            lines = doc.extract_lines(n)
            timer.mark("end lines")
            timer.mark("start rects")
            rects = doc.extract_rects(n)
            timer.mark("end rects")
            timer.mark("start paths")
            paths = doc.extract_paths(n)
            timer.mark("end paths")
            # 这个为合并和处理后的
            # spans = doc.extract_spans(n)
            for char in chars:
                # （x, y, width, height）
                # 一个cid对应多个字符的情况，这里是平均分配宽度
                # 如果cid对应的unicode无效或者没有，直接使用cid
                #'advance_width', 'bbox', 'char', 'color', 'font_name', 'font_size', 'font_weight', 'is_italic', 'mcid', 'origin_x', 'origin_y', 'rotation_degrees'
                print(
                    char.char,
                    hex(ord(char.char)),
                    char.bbox,
                    char.advance_width,
                    char.font_name,
                    char.font_weight,
                    char.font_size,
                    (char.origin_x, char.origin_y),
                    char.mcid,
                )  # ,char.color,char.font_name,char.font_size)

                text = char.char
                x0, y0, w, h = char.bbox
                bbox = BBox(x0, y0, x0 + w, y0 + h)
                is_bold = False
                is_italic = char.is_italic
                if re.search("bold", char.font_weight.lower()):
                    is_bold = True
                wingdings = re.search("wingdings", char.font_name.lower())
                if wingdings:
                    font = KFont.WINGDINGS
                elif char.is_monospace:
                    font = KFont.MONOSPACE
                else:
                    font = KFont.SERIF
                color = KColor.from_list(
                    char.color, is_float=True, colors=kpage.doc.colors
                )
                kpage.pdf_chars.append(
                    KChar(
                        kpage,
                        bbox.to_quad(),
                        text=text,
                        source=CharSource.PDF,
                        bold=is_bold,
                        italic=is_italic,
                        underline=False,
                        strikeout=False,
                        font=font,
                        color=color,
                    )
                )
            # 使用截图的方式更好，使用原图还需要考虑旋转/反转等
            print(
                f"chars={len(chars)},images={len(images)},lines={len(lines)},rects={len(rects)},paths={len(paths)}"
            )

            print(f"elapsed={timer.elapsed()}")
            print(timer.get_elapseds())
