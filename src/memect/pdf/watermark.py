import logging
from pathlib import Path
import re

import pymupdf

from .base import KDocument


"""
PDF 水印清除工具 —— PyMuPDF 递归处理嵌套 Form XObject
"""


class PdfWatermarkRemover:
    DEFAULT_TEXT_KEYWORDS = [
        "watermark",
        "confidential",
        "draft",
        "sample",
        "do not copy",
        "proprietary",
        "机密",
        "草稿",
        "样本",
        "水印",
        "保密",
        "内部",
    ]

    def __init__(self, input_path: str|Path):
        self.input_path = Path(input_path)
        self.doc: pymupdf.Document = pymupdf.open(input_path)
        self._log: list[str] = []
        # 已处理过的 xref，避免重复递归（循环引用保护）
        self._visited_xrefs: set[int] = set()
        self._removed = False

    # ──────────────────────────────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────────────────────────────

    @property
    def removed(self) -> bool:
        """是否清除了水印"""
        return self._removed

    def remove_watermarks(
        self,
        remove_annotation_watermark: bool = True,
        remove_ocg_watermark: bool = True,
        remove_content_watermark: bool = True,
        remove_xobject_watermark: bool = True,
        custom_text_patterns: list[str] | None = None,
    ) -> "PdfWatermarkRemover":

        text_keywords = list(self.DEFAULT_TEXT_KEYWORDS)
        if custom_text_patterns:
            text_keywords.extend(custom_text_patterns)

        if remove_ocg_watermark:
            self._remove_ocg_layers()

        for page_num in range(len(self.doc)):
            self._visited_xrefs.clear()  # 每页重置访问记录
            page: pymupdf.Page = self.doc[page_num]

            if remove_annotation_watermark:
                self._remove_watermark_annotations(page, page_num)

            if remove_content_watermark or remove_xobject_watermark:
                self._process_page(
                    page,
                    page_num,
                    remove_bdc=remove_content_watermark,
                    remove_xobj=remove_xobject_watermark,
                    text_keywords=text_keywords,
                )

        self._print_log()
        return self

    def save(self, output_path: str|Path, garbage: int = 0, deflate: bool = True) -> None:
        # garbage=4 深度清理，回收被置空的 XObject 流
        self.doc.save(output_path, garbage=garbage, deflate=deflate)
        print(f"[✓] 已保存到: {output_path}")

    def close(self) -> None:
        self.doc.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ──────────────────────────────────────────────────────────────────
    # 页面入口：处理页面内容流 + 递归处理所有 XObject
    # ──────────────────────────────────────────────────────────────────

    def _process_page(
        self,
        page: pymupdf.Page,
        page_num: int,
        remove_bdc: bool,
        remove_xobj: bool,
        text_keywords: list[str],
    ) -> None:
        """处理页面内容流，并递归进入所有引用的 Form XObject。"""

        # 1. 收集页面资源中的 XObject 映射 {name: xref}
        xobj_map = self._get_xobject_map(page.xref)

        # 2. 判断哪些是水印 XObject
        watermark_xobj_names = self._identify_watermark_xobjects(xobj_map)

        # 3. 清理页面自身内容流
        content_xrefs = page.get_contents()
        if content_xrefs:
            raw = b"\n".join(self.doc.xref_stream(x) or b"" for x in content_xrefs)
            cleaned, changed = self._process_stream(
                raw,
                f"页{page_num + 1}",
                remove_bdc=remove_bdc,
                remove_xobj=remove_xobj,
                text_keywords=text_keywords,
                watermark_xobj_names=watermark_xobj_names,
            )
            if changed:
                self.doc.update_stream(content_xrefs[0], cleaned)
                for x in content_xrefs[1:]:
                    self.doc.update_stream(x, b"")
                self._removed = True

        # 4. 递归处理所有 Form XObject（包括非水印的，因为里面可能嵌套水印）
        for name, xref in xobj_map.items():
            self._process_form_xobject(
                xref,
                name,
                remove_bdc=remove_bdc,
                remove_xobj=remove_xobj,
                text_keywords=text_keywords,
                parent_label=f"页{page_num + 1}",
            )

    # ──────────────────────────────────────────────────────────────────
    # 核心递归：处理单个 Form XObject 内容流
    # ──────────────────────────────────────────────────────────────────

    def _process_form_xobject(
        self,
        xref: int,
        name: str,
        remove_bdc: bool,
        remove_xobj: bool,
        text_keywords: list[str],
        parent_label: str,
    ) -> None:
        """
        递归处理 Form XObject：
        1. 清理自身内容流中的水印操作符
        2. 找出内部引用的子 XObject，递归处理
        """
        if xref in self._visited_xrefs:
            return  # 防止循环引用
        self._visited_xrefs.add(xref)

        label = f"{parent_label}>{name}(xref={xref})"

        # 读取 XObject 字典（判断类型）
        try:
            xobj_dict = self.doc.xref_object(xref, compressed=False)
        except Exception:
            return

        # 只处理 Form 类型（/Subtype /Form）
        if "/Form" not in xobj_dict:
            return

        # 读取流内容
        try:
            raw = self.doc.xref_stream(xref)
        except Exception:
            return
        if not raw:
            return

        # 收集这个 Form XObject 自身的子 XObject 资源
        child_xobj_map = self._get_xobject_map(xref)
        watermark_names = self._identify_watermark_xobjects(child_xobj_map)

        # 清理内容流
        cleaned, changed = self._process_stream(
            raw,
            label,
            remove_bdc=remove_bdc,
            remove_xobj=remove_xobj,
            text_keywords=text_keywords,
            watermark_xobj_names=watermark_names,
        )
        if changed:
            self.doc.update_stream(xref, cleaned)
            self._log.append(f"  {label}: 内容流已清理")
            self._removed = True

        # 递归处理子 XObject
        for child_name, child_xref in child_xobj_map.items():
            self._process_form_xobject(
                child_xref,
                child_name,
                remove_bdc=remove_bdc,
                remove_xobj=remove_xobj,
                text_keywords=text_keywords,
                parent_label=label,
            )

    # ──────────────────────────────────────────────────────────────────
    # XObject 资源解析
    # ──────────────────────────────────────────────────────────────────

    def _get_xobject_map(self, xref: int) -> dict[str, int]:
        """
        从对象字典中提取 /Resources /XObject 的 {名称: xref} 映射。
        同时处理 /Resources 是直接嵌入还是间接引用的情况。
        """
        result: dict[str, int] = {}
        try:
            raw = self.doc.xref_object(xref, compressed=False)
        except Exception:
            return result

        # /Resources 可能是直接字典或间接引用
        res_match = re.search(r"/Resources\s+(\d+)\s+0\s+R", raw)
        if res_match:
            # 间接引用：读取资源对象
            res_xref = int(res_match.group(1))
            try:
                raw = self.doc.xref_object(res_xref, compressed=False)
            except Exception:
                return result

        # 提取 /XObject << ... >>
        xobj_match = re.search(r"/XObject\s*<<([^>]*)>>", raw, re.DOTALL)
        if not xobj_match:
            return result

        for name, ref in re.findall(r"(/\w+)\s+(\d+)\s+0\s+R", xobj_match.group(1)):
            result[name] = int(ref)

        return result

    def _identify_watermark_xobjects(self, xobj_map: dict[str, int]) -> set[str]:
        """
        判断哪些 XObject 是水印：
        - 字典含 /Subtype /Watermark
        - 含 /OC（OCG 控制）
        - 名称含水印关键词
        - 内容流本身只有水印操作（空流或纯变换矩阵）
        """
        wm_names: set[str] = set()
        wm_keywords = ["watermark", "水印", "wm"]

        for name, xref in xobj_map.items():
            try:
                xobj_raw = self.doc.xref_object(xref, compressed=False)
            except Exception:
                continue

            name_lower = name.lower()
            is_wm = (
                "/Watermark" in xobj_raw
                or "/OC " in xobj_raw  # OCG 控制
                or "/OC\n" in xobj_raw
                or any(k in name_lower for k in wm_keywords)
            )

            # 额外：如果 XObject 内嵌套的所有子对象都是水印，也视为水印
            if not is_wm:
                child_map = self._get_xobject_map(xref)
                if child_map:
                    child_wm = self._identify_watermark_xobjects(child_map)
                    if child_wm == set(child_map.keys()):  # 子对象全是水印
                        is_wm = True

            if is_wm:
                wm_names.add(name)
                self._log.append(f"  识别水印 XObject: {name} (xref={xref})")

        return wm_names

    # ──────────────────────────────────────────────────────────────────
    # 内容流状态机（与之前版本一致，略有增强）
    # ──────────────────────────────────────────────────────────────────

    def _process_stream(
        self,
        data: bytes,
        label: str,
        remove_bdc: bool,
        remove_xobj: bool,
        text_keywords: list[str],
        watermark_xobj_names: set[str],
    ) -> tuple[bytes, bool]:

        try:
            text = data.decode("latin-1")
        except Exception:
            return data, False

        lines = text.splitlines(keepends=True)
        result: list[str] = []
        changed = False

        skip_depth = 0
        in_text_block = False
        text_buf: list[str] = []

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # BDC 水印块
            if "BDC" in stripped:
                if remove_bdc and skip_depth == 0 and self._is_watermark_bdc(stripped):
                    skip_depth = 1
                    changed = True
                    self._log.append(f"  {label}: 跳过 BDC → {stripped[:60]}")
                    i += 1
                    continue
                elif skip_depth > 0:
                    skip_depth += 1

            if stripped == "EMC" and skip_depth > 0:
                skip_depth -= 1
                i += 1
                continue

            if skip_depth > 0:
                i += 1
                continue

            # 文字块 BT...ET
            if stripped == "BT":
                in_text_block = True
                text_buf = [line]
                i += 1
                continue

            if in_text_block:
                text_buf.append(line)
                if stripped == "ET":
                    in_text_block = False
                    block = "".join(text_buf)
                    if self._block_has_watermark_text(block, text_keywords):
                        changed = True
                        self._log.append(f"  {label}: 删除文字水印块")
                    else:
                        result.append(block)
                    text_buf = []
                i += 1
                continue

            # XObject Do 调用
            if remove_xobj and re.match(r"/\w+\s+Do\s*$", stripped):
                name = stripped.split()[0]
                if name in watermark_xobj_names:
                    changed = True
                    self._log.append(f"  {label}: 移除 {name} Do")
                    i += 1
                    continue

            result.append(line)
            i += 1

        if text_buf:
            result.extend(text_buf)

        return "".join(result).encode("latin-1"), changed

    # ──────────────────────────────────────────────────────────────────
    # 注释型水印 / OCG / 辅助方法
    # ──────────────────────────────────────────────────────────────────

    def _remove_watermark_annotations(self, page: pymupdf.Page, page_num: int) -> None:
        to_delete = []
        for annot in page.annots():
            raw = self.doc.xref_object(annot.xref, compressed=False)
            if "/Watermark" in raw or "/watermark" in raw.lower():
                to_delete.append(annot)
        for annot in to_delete:
            page.delete_annot(annot)
            self._log.append(f"  页{page_num + 1}: 删除 Watermark 注释")
            self._removed = True

    def _remove_ocg_layers(self) -> None:
        layers = self.doc.get_layers()
        if not layers:
            return
        wm_kw = ["watermark", "水印", "pagination", "pagina", "artifact"]
        for layer in layers:
            if any(k in layer.get("name", "").lower() for k in wm_kw):
                self.doc.set_layer(-1, layer["number"], on=False)
                self._log.append(f"  OCG 图层关闭: {layer['name']}")
                self._removed = True

    @staticmethod
    def _is_watermark_bdc(line: str) -> bool:
        low = line.lower()
        return any(
            re.search(p, low)
            for p in [
                r"/artifact\s*<<[^>]*/subtype\s*/watermark",
                r"/subtype\s*/watermark",
                r"<<[^>]*/watermark",
                r"/artifact.*pagination",
            ]
        )

    @staticmethod
    def _block_has_watermark_text(block: str, keywords: list[str]) -> bool:
        low = block.lower()
        return any(k.lower() in low for k in keywords)

    def _print_log(self) -> None:
        if self._log:
            print("[水印清除日志]")
            for e in self._log:
                print(e)
        else:
            print("[水印清除] 未检测到水印")

class Watermark:
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')
    def __init__(self):
        super().__init__()

    def clean(self,doc:KDocument):
        assert doc.is_pdf()
        with PdfWatermarkRemover(doc.file) as remover:
            #如果改变了，才需要保存为一个新的文件
            out_file = doc.out_dir/'a-fixed.pdf'
            if out_file.is_file():# and doc.is_dev():
                #因为去水印的代码很少修改，为了避免每次都需要"--dev"参数，就不判断了
                #如果修改了去水印的代码，删除之前的文件即可
                self._logger.info('清除水印后的文件已经存在')
                doc.file=out_file
            else:
                #清除文本这个实现不够严谨
                remover.remove_watermarks(remove_content_watermark=False)
                if remover.removed:
                    remover.save(out_file)
                    doc.file=out_file
                    self._logger.info('清除了水印')

def main():
    import argparse

    parser = argparse.ArgumentParser(description="PDF 水印清除 (递归 Form XObject)")
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--keywords", nargs="*", default=[])
    args = parser.parse_args()

    with PdfWatermarkRemover(args.input) as r:
        r.remove_watermarks(custom_text_patterns=args.keywords)
        r.save(args.output)


if __name__ == "__main__":
    main()