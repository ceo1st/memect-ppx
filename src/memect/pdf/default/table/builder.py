from memect.base.bbox import BBox, Point, Quad
from memect.pdf.base import KCell, KPage, KTable


def _cluster_edges(values: list[float], tol: float) -> list[float]:
    """Cluster values within `tol` and return cluster means, sorted."""
    if not values:
        return []
    values = sorted(values)
    clusters: list[list[float]] = [[values[0]]]
    for v in values[1:]:
        if v - clusters[-1][-1] <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [sum(c) / len(c) for c in clusters]


def _nearest(value: float, centers: list[float]) -> int:
    return min(range(len(centers)), key=lambda i: abs(centers[i] - value))


def _bbox_to_quad(x0: float, y0: float, x1: float, y1: float) -> Quad:
    return Quad(Point(x0, y0), Point(x1, y0), Point(x1, y1), Point(x0, y1))


class TableCellBuilder:
    def build(
        self,
        page: KPage,
        table_bbox: tuple[float, float, float, float]|BBox,
        cells: list[tuple[float, float, float, float]]|list[BBox],
    ) -> KTable:
        tq = _bbox_to_quad(*table_bbox)
        if not cells:
            return KTable(page, tq, row_num=1, col_num=1)

        # 1. derive row/col grid lines from cell edges, with tolerance ~ 30% of smallest cell
        heights = [y1 - y0 for _, y0, _, y1 in cells]
        widths  = [x1 - x0 for x0, _, x1, _ in cells]
        tol_y = max(1.0, min(heights) * 0.3)
        tol_x = max(1.0, min(widths)  * 0.3)

        y_lines = _cluster_edges([y for _, y0, _, y1 in cells for y in (y0, y1)], tol_y)
        x_lines = _cluster_edges([x for x0, _, x1, _ in cells for x in (x0, x1)], tol_x)

        # ensure table bbox edges are part of the grid
        tx0, ty0, tx1, ty1 = table_bbox
        for v, lines, tol in ((tx0, x_lines, tol_x), (tx1, x_lines, tol_x),
                              (ty0, y_lines, tol_y), (ty1, y_lines, tol_y)):
            if not lines or min(abs(v - line) for line in lines) > tol:
                lines.append(v)
        x_lines.sort()
        y_lines.sort()

        col_num = len(x_lines) - 1  # cells between adjacent lines
        row_num = len(y_lines) - 1
        # row centers/col centers = midpoints between grid lines
        # origin is bottom-left, row 0 is top row → sort y_lines descending for row indexing
        y_lines_desc = list(reversed(y_lines))

        # 2. snap each input cell to grid indices and spans
        occupied: dict[tuple[int, int], KCell] = {}
        for x0, y0, x1, y1 in cells:
            # col: smallest i such that x_lines[i] ≈ x0; span until x_lines[i+span] ≈ x1
            ci_start = _nearest(x0, x_lines)
            ci_end   = _nearest(x1, x_lines)
            if ci_end <= ci_start:
                ci_end = ci_start + 1
            # row: in descending y, row 0 is top. y1 is top edge of cell.
            ri_start = _nearest(y1, y_lines_desc)
            ri_end   = _nearest(y0, y_lines_desc)
            if ri_end <= ri_start:
                ri_end = ri_start + 1

            col_span = max(1, ci_end - ci_start)
            row_span = max(1, ri_end - ri_start)
            ci_start = max(0, min(ci_start, col_num - 1))
            ri_start = max(0, min(ri_start, row_num - 1))
            col_span = min(col_span, col_num - ci_start)
            row_span = min(row_span, row_num - ri_start)

            # snapped bbox from grid
            sx0 = x_lines[ci_start]
            sx1 = x_lines[ci_start + col_span]
            sy1 = y_lines_desc[ri_start]            # top
            sy0 = y_lines_desc[ri_start + row_span] # bottom

            quad = _bbox_to_quad(sx0, sy0, sx1, sy1)
            kcell = KCell(
                page, quad,
                row_index=ri_start, col_index=ci_start,
                row_span=row_span, col_span=col_span,
            )
            # mark every grid slot this cell covers
            for r in range(ri_start, ri_start + row_span):
                for c in range(ci_start, ci_start + col_span):
                    occupied.setdefault((r, c), kcell)

        # 3. fill missing grid slots with synthetic cells
        built_cells: list[KCell] = []
        seen: set[int] = set()
        for r in range(row_num):
            for c in range(col_num):
                kc = occupied.get((r, c))
                if kc is None:
                    sx0 = x_lines[c]
                    sx1 = x_lines[c + 1]
                    sy1 = y_lines_desc[r]
                    sy0 = y_lines_desc[r + 1]
                    kc = KCell(
                        page, _bbox_to_quad(sx0, sy0, sx1, sy1),
                        row_index=r, col_index=c,
                    )
                    built_cells.append(kc)
                elif id(kc) not in seen:
                    seen.add(id(kc))
                    built_cells.append(kc)

        table = KTable(page, tq, row_num=row_num, col_num=col_num)
        table.cells.extend(built_cells)
        return table
