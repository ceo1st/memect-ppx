# OTSL tag constants
import itertools
import re
from typing import Any


OTSL_NL = "<nl>"
OTSL_FCEL = "<fcel>"
OTSL_ECEL = "<ecel>"
OTSL_LCEL = "<lcel>"
OTSL_UCEL = "<ucel>"
OTSL_XCEL = "<xcel>"

NON_CAPTURING_TAG_GROUP = "(?:<fcel>|<ecel>|<nl>|<lcel>|<ucel>|<xcel>)"
OTSL_FIND_PATTERN = re.compile(
    f"{NON_CAPTURING_TAG_GROUP}.*?(?={NON_CAPTURING_TAG_GROUP}|$)", flags=re.DOTALL
)


def otsl_extract_tokens_and_text(s: str):
    """
    Extract OTSL tags and text parts from the input string.

    Args:
        s (str): OTSL string.

    Returns:
        Tuple[List[str], List[str]]: (tokens, text_parts)
    """
    pattern = (
        r"("
        + r"|".join([OTSL_NL, OTSL_FCEL, OTSL_ECEL, OTSL_LCEL, OTSL_UCEL, OTSL_XCEL])
        + r")"
    )
    tokens = re.findall(pattern, s)
    text_parts = re.split(pattern, s)
    text_parts = [token for token in text_parts if token.strip()]
    return tokens, text_parts


def otsl_parse_texts(texts, tokens):
    """
    Parse OTSL text and tags into TableCell objects and tag structure.

    Args:
        texts (List[str]): List of tokens and text.
        tokens (List[str]): List of OTSL tags.

    Returns:
        Tuple[List[TableCell], List[List[str]]]: (table_cells, split_row_tokens)
    """
    split_word = OTSL_NL
    split_row_tokens = [
        list(y)
        for x, y in itertools.groupby(tokens, lambda z: z == split_word)
        if not x
    ]
    table_cells = []
    r_idx = 0
    c_idx = 0

    # Ensure matrix completeness
    if split_row_tokens:
        max_cols = max(len(row) for row in split_row_tokens)
        for row in split_row_tokens:
            while len(row) < max_cols:
                row.append(OTSL_ECEL)
        new_texts = []
        text_idx = 0
        for row in split_row_tokens:
            for token in row:
                new_texts.append(token)
                if text_idx < len(texts) and texts[text_idx] == token:
                    text_idx += 1
                    if text_idx < len(texts) and texts[text_idx] not in [
                        OTSL_NL,
                        OTSL_FCEL,
                        OTSL_ECEL,
                        OTSL_LCEL,
                        OTSL_UCEL,
                        OTSL_XCEL,
                    ]:
                        new_texts.append(texts[text_idx])
                        text_idx += 1
            new_texts.append(OTSL_NL)
            if text_idx < len(texts) and texts[text_idx] == OTSL_NL:
                text_idx += 1
        texts = new_texts

    def count_right(tokens, c_idx, r_idx, which_tokens):
        span = 0
        c_idx_iter = c_idx
        while tokens[r_idx][c_idx_iter] in which_tokens:
            c_idx_iter += 1
            span += 1
            if c_idx_iter >= len(tokens[r_idx]):
                return span
        return span

    def count_down(tokens, c_idx, r_idx, which_tokens):
        span = 0
        r_idx_iter = r_idx
        while tokens[r_idx_iter][c_idx] in which_tokens:
            r_idx_iter += 1
            span += 1
            if r_idx_iter >= len(tokens):
                return span
        return span

    for i, text in enumerate(texts):
        cell_text = ""
        if text in [OTSL_FCEL, OTSL_ECEL]:
            row_span = 1
            col_span = 1
            right_offset = 1
            if text != OTSL_ECEL:
                cell_text = texts[i + 1]
                right_offset = 2

            next_right_cell = (
                texts[i + right_offset] if i + right_offset < len(texts) else ""
            )
            next_bottom_cell = ""
            if r_idx + 1 < len(split_row_tokens):
                if c_idx < len(split_row_tokens[r_idx + 1]):
                    next_bottom_cell = split_row_tokens[r_idx + 1][c_idx]

            if next_right_cell in [OTSL_LCEL, OTSL_XCEL]:
                col_span += count_right(
                    split_row_tokens, c_idx + 1, r_idx, [OTSL_LCEL, OTSL_XCEL]
                )
            if next_bottom_cell in [OTSL_UCEL, OTSL_XCEL]:
                row_span += count_down(
                    split_row_tokens, c_idx, r_idx + 1, [OTSL_UCEL, OTSL_XCEL]
                )

            
            table_cells.append(
               (r_idx,c_idx,row_span,col_span,cell_text.strip())
            )
        if text in [OTSL_FCEL, OTSL_ECEL, OTSL_LCEL, OTSL_UCEL, OTSL_XCEL]:
            c_idx += 1
        if text == OTSL_NL:
            r_idx += 1
            c_idx = 0
    return table_cells, split_row_tokens

def otsl_pad_to_sqr_v2(otsl_str: str) -> str:
    """
    Pad OTSL string to a square (rectangular) format, ensuring each row has equal number of cells.

    Args:
        otsl_str (str): OTSL string.

    Returns:
        str: Padded OTSL string.
    """
    assert isinstance(otsl_str, str)
    otsl_str = otsl_str.strip()
    if OTSL_NL not in otsl_str:
        return otsl_str + OTSL_NL
    lines = otsl_str.split(OTSL_NL)
    row_data = []
    for line in lines:
        if not line:
            continue
        raw_cells = OTSL_FIND_PATTERN.findall(line)
        if not raw_cells:
            continue
        total_len = len(raw_cells)
        min_len = 0
        for i, cell_str in enumerate(raw_cells):
            if cell_str.startswith(OTSL_FCEL):
                min_len = i + 1
        row_data.append(
            {"raw_cells": raw_cells, "total_len": total_len, "min_len": min_len}
        )
    if not row_data:
        return OTSL_NL
    global_min_width = max(row["min_len"] for row in row_data) if row_data else 0
    max_total_len = max(row["total_len"] for row in row_data) if row_data else 0
    search_start = global_min_width
    search_end = max(global_min_width, max_total_len)
    min_total_cost = float("inf")
    optimal_width = search_end

    for width in range(search_start, search_end + 1):
        current_total_cost = sum(abs(row["total_len"] - width) for row in row_data)
        if current_total_cost < min_total_cost:
            min_total_cost = current_total_cost
            optimal_width = width

    repaired_lines = []
    for row in row_data:
        cells = row["raw_cells"]
        current_len = len(cells)
        if current_len > optimal_width:
            new_cells = cells[:optimal_width]
        else:
            padding = [OTSL_ECEL] * (optimal_width - current_len)
            new_cells = cells + padding
        repaired_lines.append("".join(new_cells))
    return OTSL_NL.join(repaired_lines) + OTSL_NL


def otsl_parse(otsl_content: str)->dict[str,Any]:
    """
    Convert OTSL-v1.0 string to HTML. Only 6 tags allowed: <fcel>, <ecel>, <nl>, <lcel>, <ucel>, <xcel>.

    Args:
        otsl_content (str): OTSL string.

    Returns:
        str: HTML table.
    """
    otsl_content = otsl_pad_to_sqr_v2(otsl_content)
    tokens, mixed_texts = otsl_extract_tokens_and_text(otsl_content)
    table_cells, split_row_tokens = otsl_parse_texts(mixed_texts, tokens)
    return {
        'row_num':len(split_row_tokens),
        'col_num':(max(len(row) for row in split_row_tokens) if split_row_tokens else 0),
        'cells':table_cells
    }
