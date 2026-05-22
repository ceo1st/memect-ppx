
import re
from dataclasses import dataclass, field

from memect.pdf.base import KText, KTextline


@dataclass
class TOCEntry:
    title: str
    page: int
    lines: list[KTextline] = field(default_factory=list)


# 匹配行尾的页码：省略号/点/空格后跟数字
_PAGE_PATTERN = re.compile(r'^(.*?)[.\s…·]+(\d+)\s*$', re.DOTALL)


class TOCParser:
    def __init__(self):
        super().__init__()

    def parse(self, text: KText) -> list[TOCEntry]:
        #拆开，然后再重新合并，如：
        #第一章xxxxxxxxxxxx (textlin1)
        #  xxxx...........1(textlin1)
        #  1.xxxxx........2(textlin2)
        #  2.xxxxx........3(textlin3)
        #合并为text1=[textline1,textline2]
        #text2=[textline1]
        #text3=[textline2]
        lines = text.lines
        entries: list[TOCEntry] = []
        pending_lines: list[KTextline] = []
        pending_title_parts: list[str] = []

        for line in lines:
            line_text = line.text.strip()
            if not line_text:
                continue

            m = _PAGE_PATTERN.match(line_text)
            if m:
                title_part = m.group(1).strip()
                page_num = int(m.group(2))
                all_lines = pending_lines + [line]
                title = ' '.join(pending_title_parts + ([title_part] if title_part else []))
                entries.append(TOCEntry(title=title, page=page_num, lines=all_lines))
                pending_lines = []
                pending_title_parts = []
            else:
                # 跨行标题的续行
                pending_lines.append(line)
                pending_title_parts.append(line_text)

        return entries