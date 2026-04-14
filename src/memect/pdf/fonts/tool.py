from pathlib import Path
from typing import Sequence

import freetype
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont

def get_all_chars(font_path: str | Path,key:Sequence[tuple[int,int]]|None=None,range_:tuple[int,int]|None=None) -> list[dict[str, int | str]]:
    font = TTFont(font_path)

    #如果是wingdings
    #(3,0)
    if key:
        cmap = font.getBestCmap(key)
    else:
        cmap = font.getBestCmap()
    assert cmap is not None
    results: list[dict[str, int | str]] = []
    for codepoint, glyph_name in cmap.items():
        if not range_ or range_[0]<=codepoint<=range_[1]:
            results.append({
                "codepoint": codepoint,
                "unicode":   f"U+{codepoint:04X}",
                "char":      chr(codepoint),
                "glyph":     glyph_name,
            })

    return sorted(results, key=lambda x: x["codepoint"])


def render_all_glyphs(font_path: str | Path, output_path: str | Path | None = None,cmap_key:tuple[int,int]|None=None,range_:tuple[int,int]|None=None, size: int = 64) -> None:
    # 获取有序字符列表
    chars = get_all_chars(font_path,key=[cmap_key] if cmap_key else None,range_=range_)
    print(f"共 {len(chars)} 个字形")

    # FreeType 渲染
    face = freetype.Face(str(font_path))
    face.set_pixel_sizes(0, size - 16)

    # fontTools 用于字形名 → 索引映射
    tt          = TTFont(font_path)
    glyph_order: list[str] = tt.getGlyphOrder()
    name_to_idx: dict[str, int] = {name: idx for idx, name in enumerate(glyph_order)}

    # 画布参数
    cols       = 16
    cell_w     = size
    cell_h     = size + 24          # 额外空间放标注
    rows       = (len(chars) + cols - 1) // cols
    canvas     = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
    draw       = ImageDraw.Draw(canvas)
    small      = ImageFont.load_default()

    for i, entry in enumerate(chars):
        cp:          int = entry["codepoint"]  # type: ignore[assignment]
        glyph_name:  str = entry["glyph"]      # type: ignore[assignment]
        unicode_str: str = entry["unicode"]    # type: ignore[assignment]

        x = (i % cols) * cell_w
        y = (i // cols) * cell_h

        # 渲染字形
        glyph_idx: int | None = name_to_idx.get(glyph_name)
        if glyph_idx is not None:
            try:
                face.load_glyph(glyph_idx, freetype.FT_LOAD_RENDER)
                bm = face.glyph.bitmap
                if bm.rows > 0 and bm.width > 0:
                    arr       = np.array(bm.buffer, dtype=np.uint8).reshape(bm.rows, bm.width)
                    glyph_img = Image.fromarray(255 - arr).convert("RGB")
                    # 居中
                    ox = x + (cell_w - bm.width) // 2
                    oy = y + (size   - bm.rows)  // 2
                    canvas.paste(glyph_img, (ox, oy))
            except Exception as e:
                print(f"渲染失败 {glyph_name}: {e}")

        # 标注：上方显示 unicode，下方显示 glyph 名
        # Unicode 码位（蓝色区域）
        draw.rectangle([x, y + size, x + cell_w, y + cell_h], fill="#EEF2FF")
        draw.text((x + 2, y + size + 1),
                  unicode_str, font=small, fill="#3730A3")
        draw.text((x + 2, y + size + 11),
                  glyph_name[:12], font=small, fill="#6B7280")

        # 边框
        draw.rectangle([x, y, x + cell_w - 1, y + cell_h - 1],
                       outline="#E5E7EB")

    if output_path:
        canvas.save(output_path)
        print(f"已保存到 {output_path}")
    canvas.show()
    


if __name__=='__main__':
    dir_ = Path(__file__).parent
    for name in ['wingdings.ttf','wingdings2.ttf','wingdings3.ttf']:
        render_all_glyphs(dir_.joinpath(name),cmap_key=(3,0))
    
    render_all_glyphs(dir_.joinpath('NotoSansSymbols2-Regular.ttf'),range_=(0x2B00,0x2BFF))