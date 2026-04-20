import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OTSLCell:
    """表格中的一个逻辑单元格"""
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    content: str = ""
    is_header: bool = False      # ched / rhed
    is_section_row: bool = False # srow
    is_empty: bool = False       # ecel
    tag: str = "fcel"            # 原始 tag


class OTSLParser:
    """
    解析 OTSL (Optimized Table Structure Language) 格式的表格字符串。

    Token 说明（来自 IBM Research 论文 arXiv:2305.03393）：
      fcel  - 有内容的普通单元格
      ecel  - 空单元格
      lcel  - 横向合并占位（向左主单元格延伸）
      ucel  - 纵向合并占位（向上主单元格延伸）
      xcel  - 二维合并占位（右下角覆盖区域）
      nl    - 换行（行结束）
      ched  - 列表头单元格
      rhed  - 行表头单元格
      srow  - 分隔行单元格（表内小节标题）

    支持两种输入格式：
      1. PaddleOCR-VL 风格：<fcel>内容<fcel>内容<nl>
      2. Docling 风格：    <fcel>内容<sep/>内容<nl/>
    """

    # 所有结构 token
    STRUCT_TAGS = {"fcel", "ecel", "lcel", "ucel", "xcel",
                   "nl", "ched", "rhed", "srow"}
    # 内容单元格（需要关联文本）
    CONTENT_TAGS = {"fcel", "ecel", "ched", "rhed", "srow"}
    # 合并占位 token（不独立持有内容）
    SPAN_TAGS = {"lcel", "ucel", "xcel"}
    # 表头 token
    HEADER_TAGS = {"ched", "rhed"}

    def __init__(self):
        self._grid: list[list[Optional[OTSLCell]]] = []  # 二维网格（含 span 占位）
        self._cells: list[OTSLCell] = []                 # 所有逻辑单元格（去重）

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def parse(self, otsl_str: str) -> "OTSLParser":
        """解析 OTSL 字符串，返回 self 以支持链式调用"""
        tokens = self._tokenize(otsl_str)
        self._build_grid(tokens)
        return self

    @property
    def cells(self) -> list[OTSLCell]:
        """返回所有逻辑单元格列表"""
        return self._cells

    @property
    def num_rows(self) -> int:
        return len(self._grid)

    @property
    def num_cols(self) -> int:
        return max((len(row) for row in self._grid), default=0)

    def to_list(self) -> list[list[str]]:
        """返回二维字符串列表（含 span 展开，合并格内容重复填充）"""
        if not self._grid:
            return []
        cols = self.num_cols
        result = []
        for row in self._grid:
            result.append([
                (row[c].content if c < len(row) and row[c] else "")
                for c in range(cols)
            ])
        return result

    def to_html(self) -> str:
        """转换为 HTML <table> 字符串，正确处理 colspan / rowspan"""
        if not self._cells:
            return "<table></table>"

        # 用坐标集合标记已被 span 覆盖的格子
        spanned: set[tuple[int, int]] = set()
        for cell in self._cells:
            for dr in range(cell.row_span):
                for dc in range(cell.col_span):
                    if dr == 0 and dc == 0:
                        continue
                    spanned.add((cell.row + dr, cell.col + dc))

        rows_html = []
        for r in range(self.num_rows):
            row_cells = [c for c in self._cells if c.row == r]
            row_cells.sort(key=lambda c: c.col)

            tds = []
            for cell in row_cells:
                if (cell.row, cell.col) in spanned:
                    continue
                tag = "th" if (cell.is_header or cell.is_section_row) else "td"
                attrs = ""
                if cell.col_span > 1:
                    attrs += f' colspan="{cell.col_span}"'
                if cell.row_span > 1:
                    attrs += f' rowspan="{cell.row_span}"'
                tds.append(f"<{tag}{attrs}>{cell.content}</{tag}>")

            rows_html.append("  <tr>" + "".join(tds) + "</tr>")

        return "<table>\n" + "\n".join(rows_html) + "\n</table>"

    def to_markdown(self) -> str:
        """转换为 Markdown 表格（不支持合并格，合并格内容取主单元格）"""
        table = self.to_list()
        if not table:
            return ""

        col_widths = [
            max(len(str(table[r][c])) for r in range(len(table)))
            for c in range(len(table[0]))
        ]

        def fmt_row(row):
            return "| " + " | ".join(
                str(v).ljust(col_widths[i]) for i, v in enumerate(row)
            ) + " |"

        lines = [fmt_row(table[0])]
        lines.append("| " + " | ".join("-" * w for w in col_widths) + " |")
        for row in table[1:]:
            lines.append(fmt_row(row))

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _tokenize(self, otsl_str: str) -> list[tuple[str, str]]:
        """
        将 OTSL 字符串分解为 (tag, content) 列表。

        支持两种格式：
          - <fcel>text  （PaddleOCR-VL，无闭合标签，下一个 tag 为分隔）
          - <fcel>text<sep/>  （Docling，用 <sep/> 分隔内容）
        """
        tokens: list[tuple[str, str]] = []

        # 统一替换 Docling 的 <sep/> / <nl/> 为 PaddleOCR 风格
        s = re.sub(r"<sep/>", "", otsl_str)
        s = re.sub(r"<nl/>", "<nl>", s)
        s = re.sub(r"</?otsl>", "", s)  # 去掉外层 <otsl> 包裹

        # 匹配所有 <tag> 及其后续内容（直到下一个 <tag> 或字符串结尾）
        pattern = re.compile(
            r"<(" + "|".join(self.STRUCT_TAGS) + r")>(.*?)(?=<(?:"
            + "|".join(self.STRUCT_TAGS) + r")>|$)",
            re.DOTALL
        )

        for m in pattern.finditer(s):
            tag = m.group(1)
            content = m.group(2).strip()
            tokens.append((tag, content))

        return tokens

    def _build_grid(self, tokens: list[tuple[str, str]]):
        """根据 token 序列构建二维网格，处理 span 关系"""
        self._grid = []
        self._cells = []

        current_row: list[Optional[OTSLCell]] = []
        content_iter = iter(
            content for tag, content in tokens
            if tag in self.CONTENT_TAGS
        )

        # 第一遍：确定网格尺寸（按行 token 数）
        rows_tokens: list[list[tuple[str, str]]] = []
        cur: list[tuple[str, str]] = []
        for tag, content in tokens:
            if tag == "nl":
                if cur:
                    rows_tokens.append(cur)
                cur = []
            else:
                cur.append((tag, content))
        if cur:
            rows_tokens.append(cur)

        if not rows_tokens:
            return

        # 第二遍：构建带 span 的网格
        # 用字典记录被占用的 (row, col)
        occupied: dict[tuple[int, int], OTSLCell] = {}

        for r, row_toks in enumerate(rows_tokens):
            col_cursor = 0
            for tag, content in row_toks:
                # 跳过已被 span 占用的列
                while (r, col_cursor) in occupied:
                    col_cursor += 1

                if tag in self.SPAN_TAGS:
                    # lcel: 横向延伸，找左边主单元格
                    if tag == "lcel":
                        main = self._find_main_cell(r, col_cursor, occupied, "left")
                        if main:
                            main.col_span += 1
                            occupied[(r, col_cursor)] = main
                    # ucel: 纵向延伸，找上边主单元格
                    elif tag == "ucel":
                        main = self._find_main_cell(r, col_cursor, occupied, "up")
                        if main:
                            main.row_span += 1
                            occupied[(r, col_cursor)] = main
                    # xcel: 二维延伸
                    elif tag == "xcel":
                        main = self._find_main_cell(r, col_cursor, occupied, "up")
                        if main:
                            main.row_span = max(main.row_span, r - main.row + 1)
                            main.col_span = max(main.col_span, col_cursor - main.col + 1)
                            occupied[(r, col_cursor)] = main
                else:
                    cell = OTSLCell(
                        row=r,
                        col=col_cursor,
                        content=content,
                        is_header=(tag in self.HEADER_TAGS),
                        is_section_row=(tag == "srow"),
                        is_empty=(tag == "ecel"),
                        tag=tag,
                    )
                    self._cells.append(cell)
                    occupied[(r, col_cursor)] = cell

                col_cursor += 1

        # 重建 grid 列表（按行排序）
        if occupied:
            max_row = max(k[0] for k in occupied) + 1
            max_col = max(k[1] for k in occupied) + 1
            self._grid = [
                [occupied.get((r, c)) for c in range(max_col)]
                for r in range(max_row)
            ]

    def _find_main_cell(
        self,
        row: int,
        col: int,
        occupied: dict,
        direction: str
    ) -> Optional[OTSLCell]:
        """查找 span 占位格对应的主单元格"""
        if direction == "left":
            for c in range(col - 1, -1, -1):
                cell = occupied.get((row, c))
                if cell and cell.tag not in self.SPAN_TAGS:
                    return cell
        elif direction == "up":
            for r in range(row - 1, -1, -1):
                cell = occupied.get((r, col))
                if cell and cell.tag not in self.SPAN_TAGS:
                    return cell
        return None