
import math
from typing import NamedTuple


class Matrix(NamedTuple):
    #
    # PDF 仿射变换矩阵 [a b c d e f]
    #
    #   ┌          ┐
    #   │  a  b  0 │
    #   │  c  d  0 │
    #   │  e  f  1 │
    #   └          ┘
    #
    # 变换公式（行向量 × 矩阵）：
    #   x' = a·x + c·y + e
    #   y' = b·x + d·y + f
    #
    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    @classmethod
    def lb_to_lt(cls,src:tuple[float,float],dest:tuple[float,float]|None=None):
        """左下角坐标转换为左上角坐标，如果指定了目标，还会自动缩放"""
        if dest is None:
            sw,sh=1,1
        else:
            sw=dest[0]/src[0]
            sh=dest[1]/src[1]
        return Matrix(1,0,0,-1,0,src[1]).scale(sw,sh)

    @classmethod
    def lt_to_lb(cls,src:tuple[float,float],dest:tuple[float,float]|None=None):
        """左上角坐标转换为左下角坐标，如果指定了目标，还会自动缩放"""
        if dest is None:
            sw,sh=1,1
        else:
            sw=dest[0]/src[0]
            sh=dest[1]/src[1]
        return Matrix(1,0,0,-1,0,src[1]).scale(sw,sh)
    
    @classmethod
    def identity(cls) -> 'Matrix':
        #
        # 单位矩阵（无变换）：
        #
        #   ┌       ┐
        #   │ 1 0 0 │
        #   │ 0 1 0 │
        #   │ 0 0 1 │
        #   └       ┘
        #
        return cls(1, 0, 0, 1, 0, 0)

    @classmethod
    def from_scale(cls, sx: float, sy: float | None = None) -> 'Matrix':
        #
        # 缩放矩阵：
        #
        #   ┌          ┐
        #   │ sx  0  0 │
        #   │  0 sy  0 │
        #   │  0  0  1 │
        #   └          ┘
        #
        #   x' = sx·x
        #   y' = sy·y
        #
        sy = sy if sy is not None else sx
        return cls(sx, 0, 0, sy, 0, 0)

    @classmethod
    def from_rotate(cls, angle_deg: float) -> 'Matrix':
        #
        # 旋转矩阵（顺时针，PDF 坐标系 y 轴向上）：
        #
        #   顺时针 θ = 逆时针 -θ，所以取 -angle_deg
        #
        #   ┌                    ┐
        #   │  cos θ  -sin θ  0  │
        #   │  sin θ   cos θ  0  │
        #   │    0       0    1  │
        #   └                    ┘
        #
        #   x' =  cos θ · x + sin θ · y
        #   y' = -sin θ · x + cos θ · y
        #
        θ = math.radians(-angle_deg)
        cos_t = round(math.cos(θ), 10)
        sin_t = round(math.sin(θ), 10)
        return cls(cos_t, sin_t, -sin_t, cos_t, 0, 0)

    @classmethod
    def from_translate(cls, tx: float, ty: float) -> 'Matrix':
        #
        # 平移矩阵：
        #
        #   ┌          ┐
        #   │  1  0  0 │
        #   │  0  1  0 │
        #   │ tx ty  1 │
        #   └          ┘
        #
        #   x' = x + tx
        #   y' = y + ty
        #
        return cls(1, 0, 0, 1, tx, ty)

    @classmethod
    def from_skew(cls, kx: float, ky: float = 0.0) -> 'Matrix':
        #
        # 剪切矩阵：
        #
        #   ┌          ┐
        #   │  1  ky 0 │
        #   │  kx  1 0 │
        #   │  0   0 1 │
        #   └          ┘
        #
        #   x' = x + kx·y
        #   y' = ky·x + y
        #
        return cls(1, ky, kx, 1, 0, 0)

    def __matmul__(self, other: 'Matrix') -> 'Matrix':
        #
        # 矩阵相乘（self 先应用，other 后应用）：
        #
        #   ┌            ┐   ┌            ┐
        #   │ a1  b1  0  │   │ a2  b2  0  │
        #   │ c1  d1  0  │ × │ c2  d2  0  │
        #   │ e1  f1  1  │   │ e2  f2  1  │
        #   └            ┘   └            ┘
        #
        #   a = a1·a2 + b1·c2
        #   b = a1·b2 + b1·d2
        #   c = c1·a2 + d1·c2
        #   d = c1·b2 + d1·d2
        #   e = e1·a2 + f1·c2 + e2
        #   f = e1·b2 + f1·d2 + f2
        #
        a1, b1, c1, d1, e1, f1 = self
        a2, b2, c2, d2, e2, f2 = other
        return Matrix(
            a1 * a2 + b1 * c2,
            a1 * b2 + b1 * d2,
            c1 * a2 + d1 * c2,
            c1 * b2 + d1 * d2,
            e1 * a2 + f1 * c2 + e2,
            e1 * b2 + f1 * d2 + f2,
        )

    def then(self, other: 'Matrix') -> 'Matrix':
        #
        # 先应用 self，再应用 other：
        #   result = self @ other
        #
        # 顺序示例：
        #   Matrix.from_scale(2).then(Matrix.from_rotate(45))
        #   等价于：先缩放 2x，再旋转 45°
        #
        return self @ other

    def scale(self, sx: float, sy: float | None = None) -> 'Matrix':
        #
        # 在当前变换基础上追加缩放：
        #   result = self @ Scale(sx, sy)
        #
        # 示例：
        #   Matrix.from_translate(10, 20).scale(2)
        #   等价于：先平移，再缩放
        #
        return self @ Matrix.from_scale(sx, sy)

    def rotate(self, angle_deg: float) -> 'Matrix':
        #
        # 在当前变换基础上追加顺时针旋转：
        #   result = self @ Rotate(angle_deg)
        #
        # 示例：
        #   Matrix.from_scale(2).rotate(90)
        #   等价于：先缩放 2x，再顺时针旋转 90°
        #
        return self @ Matrix.from_rotate(angle_deg)

    def translate(self, tx: float, ty: float) -> 'Matrix':
        #
        # 在当前变换基础上追加平移：
        #   result = self @ Translate(tx, ty)
        #
        # 示例：
        #   Matrix.from_rotate(45).translate(100, 0)
        #   等价于：先旋转 45°，再平移 (100, 0)
        #
        return self @ Matrix.from_translate(tx, ty)

    def skew(self, kx: float, ky: float = 0.0) -> 'Matrix':
        #
        # 在当前变换基础上追加剪切：
        #   result = self @ Skew(kx, ky)
        #
        return self @ Matrix.from_skew(kx, ky)

    def apply(self, x: float, y: float) -> tuple[float, float]:
        #
        # 对坐标点 (x, y) 应用变换：
        #
        #   [x'  y'  1] = [x  y  1] × M
        #
        #   x' = a·x + c·y + e
        #   y' = b·x + d·y + f
        #
        return (
            round(self.a * x + self.c * y + self.e, 6),
            round(self.b * x + self.d * y + self.f, 6),
        )

    def inverse(self) -> 'Matrix':
        #
        # 求逆矩阵：
        #
        #   2×2 部分行列式：
        #     det = a·d - b·c
        #
        #   逆矩阵 2×2 部分：
        #     ┌           ┐         ┌            ┐
        #     │  a  b  0  │  -1     │  d/det  -b/det  0 │
        #     │  c  d  0  │     =   │ -c/det   a/det  0 │
        #     │  e  f  1  │         │  e'      f'     1 │
        #     └           ┘         └                   ┘
        #
        #   逆矩阵平移部分：
        #     e' = (c·f - d·e) / det
        #     f' = (b·e - a·f) / det
        #
        det = self.a * self.d - self.b * self.c
        if det == 0:
            raise ValueError("矩阵不可逆（det=0）")
        return Matrix(
             self.d / det,
            -self.b / det,
            -self.c / det,
             self.a / det,
            (self.c * self.f - self.d * self.e) / det,
            (self.b * self.e - self.a * self.f) / det,
        )

    def to_pil(self) -> tuple[float, float, float, float, float, float]:
        #
        # 转换为 PIL AFFINE 逆变换参数：
        #
        #   PDF:  x' = a·x + c·y + e      参数顺序 [a b c d e f]
        #         y' = b·x + d·y + f
        #
        #   PIL:  x' = a·x + b·y + c      参数顺序 (a, b, c, d, e, f)
        #         y' = d·x + e·y + f
        #
        #   步骤 1：PDF 参数 → PIL 参数顺序
        #     pdf [a  b  c  d  e  f]
        #          ↓  ↓  ↓  ↓  ↓  ↓
        #     pil (a  c  e  b  d  f)
        #
        #   步骤 2：PIL 需要逆变换（src ← dst），对步骤1结果求逆
        #
        inv = self.inverse()
        return (inv.a, inv.c, inv.e, inv.b, inv.d, inv.f)


    def to_angle(self) -> float:
            #
            # 从矩阵中提取旋转角度（顺时针为正，单位：度）：
            #
            #   from_rotate 存储的是顺时针 θ，内部取 -θ 作为数学角度：
            #
            #     a =  cos(-θ) =  cos θ
            #     b =  sin(-θ) = -sin θ
            #     c = -sin(-θ) =  sin θ
            #     d =  cos(-θ) =  cos θ
            #
            #   所以：
            #     cos θ = a
            #     sin θ = c
            #
            #   还原顺时针角度：
            #     θ = atan2(sin θ, cos θ)
            #       = atan2(c, a)
            #
            #   atan2 返回范围：(-180°, 180°]
            #   正值 = 顺时针，负值 = 逆时针
            #
            angle_rad = math.atan2(self.c, self.a)
            return round(math.degrees(angle_rad), 6)

    def __repr__(self) -> str:
        a, b, c, d, e, f = self
        return (
            f"Matrix(\n"
            f"  ┌                      ┐\n"
            f"  │ {a:>8.3f} {b:>8.3f}  0 │\n"
            f"  │ {c:>8.3f} {d:>8.3f}  0 │\n"
            f"  │ {e:>8.3f} {f:>8.3f}  1 │\n"
            f"  └                      ┘\n"
            f")"
        )