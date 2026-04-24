from typing import TypedDict

import cv2
import numpy as np


class _Lines(TypedDict):
    horizontal: list[tuple[float, float, float, float]]
    vertical:   list[tuple[float, float, float, float]]


class TableLineParser:
    """用投影法从表格图片中提取水平线和垂直线，对文字干扰免疫。

    算法：
      1. 二值化（暗像素=线条/文字）
      2. 行投影（每行暗像素数）找水平线，列投影找垂直线
      3. 峰值行/列 = 线条位置，连续峰值合并为一条线
      4. 按线的跨度过滤短线（文字行）
    """

    def __init__(
        self,
        *,
        cell_shrink: int = 2,
        line_min_length_ratio: float = 0.1,
        merge_tol: float = 3.0,
        density_threshold: float = 0.5,
        edge_threshold: int = 30,
        thick_line_gap: int = 2,
    ):
        self._cell_shrink = cell_shrink
        self._line_min_length_ratio = line_min_length_ratio
        self._merge_tol = merge_tol
        self._density_threshold = density_threshold
        self._edge_threshold = edge_threshold
        self._thick_line_gap = thick_line_gap

    def parse(
        self,
        image: np.ndarray,
        *,
        content_bboxes: list[tuple[float, float, float, float]] | None = None,
        debug: bool = False,
    ) -> _Lines:
        img = image if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

        # 先用背景色填充文字区域（向内缩进），消除文字梯度
        if content_bboxes:
            img = img.copy()
            s = self._cell_shrink
            for x0, y0, x1, y1 in content_bboxes:
                ix0, iy0 = int(x0) + s, int(y0) + s
                ix1, iy1 = int(x1) - s, int(y1) - s
                if ix1 > ix0 and iy1 > iy0:
                    fill = self._local_bg(img, ix0, iy0, ix1, iy1)
                    cv2.rectangle(img, (ix0, iy0), (ix1, iy1), fill, -1)

        # 高斯模糊消除轻微颜色变化，只保留明显线条边缘
        img = cv2.GaussianBlur(img, (5, 5), 0)

        # 彩色 Sobel：每通道分别计算，取最大值，捕获低对比度和彩色线条
        h_edges = np.zeros(img.shape[:2], dtype=np.float32)
        v_edges = np.zeros(img.shape[:2], dtype=np.float32)
        for c in cv2.split(img):
            sy = cv2.Sobel(c, cv2.CV_32F, 0, 1, ksize=3)
            sx = cv2.Sobel(c, cv2.CV_32F, 1, 0, ksize=3)
            np.maximum(h_edges, np.abs(sy), out=h_edges)
            np.maximum(v_edges, np.abs(sx), out=v_edges)

        t = float(self._edge_threshold)
        h_binary = (h_edges >= t).astype(np.uint8) * 255
        v_binary = (v_edges >= t).astype(np.uint8) * 255

        # 合并粗线的双边缘：垂直方向 dilate 合并水平线两侧边缘，水平方向 dilate 合并垂直线
        if self._thick_line_gap > 0:
            g = self._thick_line_gap
            h_binary = cv2.dilate(h_binary, cv2.getStructuringElement(cv2.MORPH_RECT, (1, g)))
            v_binary = cv2.dilate(v_binary, cv2.getStructuringElement(cv2.MORPH_RECT, (g, 1)))

        h_segs = self._project(h_binary, horizontal=True)
        v_segs = self._project(v_binary, horizontal=False)

        result: _Lines = {
            "horizontal": self._merge(h_segs, horizontal=True),
            "vertical":   self._merge(v_segs, horizontal=False),
        }

        if debug:
            self._show_debug(image, img, h_binary, v_binary, h_segs, v_segs, result, content_bboxes or [])
        return result

    def _binarize(self, gray: np.ndarray) -> np.ndarray:
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        return binary

    def _project(
        self, binary: np.ndarray, *, horizontal: bool
    ) -> list[tuple[float, float, float, float]]:
        h, w = binary.shape
        min_len = (w if horizontal else h) * self._line_min_length_ratio

        if horizontal:
            density = binary.sum(axis=1) / (255.0 * w)
        else:
            density = binary.sum(axis=0) / (255.0 * h)

        segs: list[tuple[float, float, float, float]] = []
        i = 0
        n = len(density)
        while i < n:
            if density[i] >= self._density_threshold:
                j = i
                while j < n and density[j] >= self._density_threshold:
                    j += 1
                coord = (i + j - 1) / 2.0
                if horizontal:
                    row_slice = binary[i:j, :].max(axis=0)
                else:
                    row_slice = binary[:, i:j].max(axis=1)
                # 在该行/列内找连续暗像素段，避免把断开的线连起来
                for start, end in self._continuous_spans(row_slice, min_len):
                    if horizontal:
                        segs.append((float(start), coord, float(end), coord))
                    else:
                        segs.append((coord, float(start), coord, float(end)))
                i = j
            else:
                i += 1
        return segs

    def _continuous_spans(
        self, arr: np.ndarray, min_len: float
    ) -> list[tuple[int, int]]:
        """返回连续非零段的 (start, end) 列表，过滤短于 min_len 的段。"""
        spans: list[tuple[int, int]] = []
        start = None
        for k, v in enumerate(arr):
            if v > 0 and start is None:
                start = k
            elif v == 0 and start is not None:
                if k - start >= min_len:
                    spans.append((start, k - 1))
                start = None
        if start is not None and len(arr) - start >= min_len:
            spans.append((start, len(arr) - 1))
        return spans

    def _fill_cells(
        self,
        image: np.ndarray,
        cell_bboxes: list[tuple[float, float, float, float]],
    ) -> np.ndarray:
        if not cell_bboxes:
            return image
        out = image.copy()
        s = self._cell_shrink
        for x0, y0, x1, y1 in cell_bboxes:
            ix0, iy0 = int(x0) + s, int(y0) + s
            ix1, iy1 = int(x1) - s, int(y1) - s
            if ix1 > ix0 and iy1 > iy0:
                fill = self._local_bg(image, ix0, iy0, ix1, iy1)
                cv2.rectangle(out, (ix0, iy0), (ix1, iy1), fill, -1)
        return out

    @staticmethod
    def _local_bg(image: np.ndarray, x0: int, y0: int, x1: int, y1: int, border: int = 3) -> tuple[int, ...] | int:
        h, w = image.shape[:2]
        bx0, by0 = max(0, x0 - border), max(0, y0 - border)
        bx1, by1 = min(w, x1 + border), min(h, y1 + border)
        region = image[by0:by1, bx0:bx1]
        mask = np.ones(region.shape[:2], dtype=bool)
        mask[y0 - by0:y1 - by0, x0 - bx0:x1 - bx0] = False
        pixels = region[mask]
        if len(pixels) == 0:
            return (255,) * (image.shape[2] if image.ndim == 3 else 1)
        median = np.median(pixels, axis=0).astype(int)
        return int(median) if image.ndim == 2 else tuple(int(v) for v in median)

    def _merge(
        self,
        segs: list[tuple[float, float, float, float]],
        *,
        horizontal: bool,
    ) -> list[tuple[float, float, float, float]]:
        if not segs:
            return []
        norm: list[tuple[float, float, float]] = []
        for x0, y0, x1, y1 in segs:
            if horizontal:
                norm.append(((y0 + y1) / 2, *sorted((x0, x1))))
            else:
                norm.append(((x0 + x1) / 2, *sorted((y0, y1))))
        norm.sort(key=lambda s: (round(s[0] / self._merge_tol), s[1]))
        merged: list[tuple[float, float, float]] = []
        for coord, a, b in norm:
            if merged and abs(merged[-1][0] - coord) <= self._merge_tol \
                    and a <= merged[-1][2] + self._merge_tol:
                pc, pa, pb = merged[-1]
                merged[-1] = ((pc + coord) / 2, min(pa, a), max(pb, b))
            else:
                merged.append((coord, a, b))
        result: list[tuple[float, float, float, float]] = []
        for coord, a, b in merged:
            result.append((a, coord, b, coord) if horizontal else (coord, a, coord, b))
        return result

    def _show_debug(
        self,
        original: np.ndarray,
        filled: np.ndarray,
        h_binary: np.ndarray,
        v_binary: np.ndarray,
        h_segs: list[tuple[float, float, float, float]],
        v_segs: list[tuple[float, float, float, float]],
        result: _Lines,
        content_bboxes: list[tuple[float, float, float, float]] | None = None,
    ) -> None:
        def to_bgr(img: np.ndarray) -> np.ndarray:
            return img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

        def draw(canvas: np.ndarray, segs, color):
            out = canvas.copy()
            for x0, y0, x1, y1 in segs:
                cv2.line(out, (int(x0), int(y0)), (int(x1), int(y1)), color, 2)
            return out

        base = to_bgr(original)
        content_vis = base.copy()
        for x0, y0, x1, y1 in (content_bboxes or []):
            cv2.rectangle(content_vis, (int(x0), int(y0)), (int(x1), int(y1)), (0, 165, 255), 1)
        raw_vis = draw(draw(base, h_segs, (0, 255, 0)), v_segs, (0, 0, 255))
        final_vis = draw(draw(base, result["horizontal"], (0, 255, 0)), result["vertical"], (0, 0, 255))
        for title, img in [("1.original", base),
                           ("2.content_bboxes", content_vis),
                           ("3.filled", to_bgr(filled)),
                           ("4.h_edges", to_bgr(h_binary)),
                           ("5.v_edges", to_bgr(v_binary)),
                           ("6.detected", raw_vis),
                           ("7.final", final_vis)]:
            cv2.imshow(title, img)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
