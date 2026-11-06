"""NIfTI 几何校验（M1，proposal §5）+ RAS+ 标准方向重排 + affine 匹配守卫（M1-fix）。

三条校验全部是**警告**（不拒收）——几何异常仍可导入，导入报告可见：

- **affine 各向异性**：spacing（zooms）max/min 比值 > 阈值（默认 3×）
- **非正交方向**：affine 旋转矩阵列向量两两点积绝对值 > 阈值（默认 0.02）
- **非毫米 spacing**：xyzt_units 不是 mm（或无法判定）

M1-fix 强约束（用户评审意见）：

- **reorient_ras**：DICOM -> NIfTI 落盘前做 RAS+ 标准方向重排（nibabel
  as_closest_canonical），导入报告写明方向变化。
- **assert_affine_match**：mask 与原图 affine 不一致就 raise——所有 mask 读写
  路径必须过这个守卫，禁止静默重采样。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

MAX_ANISO_RATIO = 3.0  # spacing max/min > 3× 警告
ORTHO_TOLERANCE = 0.02  # 方向列向量两两点积绝对值 > 0.02（非正交）警告

# assert_affine_match 默认容差：affine 元素级 |diff| 上限（浮点写入误差）
AFFINE_MATCH_TOLERANCE = 1e-4


@dataclass
class GeomReport:
    """单文件 NIfTI 几何校验结果。"""

    shape: tuple[int, ...] | None = None
    spacing: tuple[float, ...] | None = None
    orientation: str = "unknown"  # axial/sagittal/coronal/oblique/unknown
    is_orthogonal: bool = True
    is_mm: bool | None = None
    aniso_ratio: float | None = None
    warnings: list[str] = field(default_factory=list)

    def to_report(self) -> dict[str, Any]:
        return {
            "shape": list(self.shape) if self.shape else None,
            "spacing_mm": list(self.spacing) if self.spacing else None,
            "orientation": self.orientation,
            "is_orthogonal": self.is_orthogonal,
            "is_mm_units": self.is_mm,
            "aniso_ratio": self.aniso_ratio,
            "warnings": list(self.warnings),
        }


def _axis_label(affine) -> str:  # noqa: ANN001 — nibabel affine 数组
    """从 affine 旋转部分判定主方向：体素 z 轴在世界坐标的主分量。"""
    # 第三列（k 方向）的世界向量
    k = affine[0:3, 2]
    abs_k = [abs(float(v)) for v in k]
    total = sum(abs_k)
    if total < 1e-9:
        return "unknown"
    if abs_k[2] / total > 0.9:
        return "axial"
    if abs_k[0] / total > 0.9:
        return "sagittal"
    if abs_k[1] / total > 0.9:
        return "coronal"
    return "oblique"


def check_nifti_geometry(img) -> GeomReport:  # noqa: ANN001 — nibabel SpatialImage
    """对 nibabel 图像做三条几何检查，产出警告列表（不拒收）。"""
    report = GeomReport()
    affine = img.affine
    report.shape = tuple(int(v) for v in img.shape)

    # 1. spacing（zooms）：各向异性检查
    zooms = tuple(float(v) for v in img.header.get_zooms()[:3])
    report.spacing = zooms
    positive = [z for z in zooms if z > 0]
    if len(positive) == 3:
        ratio = max(positive) / min(positive)
        report.aniso_ratio = round(ratio, 2)
        if ratio > MAX_ANISO_RATIO:
            report.warnings.append(
                f"affine 各向异性：spacing {zooms} max/min = {ratio:.1f}x "
                f"(> {MAX_ANISO_RATIO}x)，各向同性重采样可能需要"
            )

    # 2. 非正交方向：旋转矩阵列向量两两点积
    rot = affine[0:3, 0:3]
    cols = [[float(rot[i][j]) for i in range(3)] for j in range(3)]
    norms = [math.sqrt(sum(c * c for c in col)) for col in cols]
    if all(n > 1e-9 for n in norms):
        max_dot = 0.0
        for a in range(3):
            for b in range(a + 1, 3):
                dot = abs(
                    sum(cols[a][i] * cols[b][i] for i in range(3)) / (norms[a] * norms[b])
                )
                max_dot = max(max_dot, dot)
        report.is_orthogonal = max_dot <= ORTHO_TOLERANCE
        if not report.is_orthogonal:
            report.warnings.append(
                f"非正交方向：affine 列向量最大 |cos| = {max_dot:.3f} "
                f"(> {ORTHO_TOLERANCE})，重采样/训练前可能需要矫正"
            )
    else:
        report.warnings.append("affine 旋转部分含零列：退化仿射，方向不可判定")

    # 3. 非毫米 spacing：xyzt_units
    units = img.header.get_xyzt_units()
    spatial_unit = units[0] if units else "unknown"
    if spatial_unit == "mm":
        report.is_mm = True
    elif spatial_unit == "unknown":
        report.is_mm = None
        report.warnings.append(
            "xyzt_units 缺失（unknown）：无法确认 spacing 单位是毫米，请人工确认"
        )
    else:
        report.is_mm = False
        report.warnings.append(
            f"非毫米 spacing：xyzt_units = {spatial_unit!r}（期望 mm），"
            "几何与层厚换算会失真"
        )

    report.orientation = _axis_label(affine)
    return report


# ---------------------------------------------------------------------------
# M1-fix：RAS+ 重排 + affine 匹配守卫
# ---------------------------------------------------------------------------


class AffineMismatchError(Exception):
    """mask 与原图 affine 不一致（禁止静默重采样，mask 读写路径硬报错）。"""


def reorient_ras(img: Any) -> tuple[Any, str]:
    """RAS+ 标准方向重排（nibabel as_closest_canonical）。

    返回 (重排后图像, 方向变化描述)。方向已是 RAS+ 时返回 (原图像, "RAS+（无需重排）")；
    否则返回重排结果与变化描述（供导入报告写明）。
    """
    import nibabel as nib

    canonical = nib.as_closest_canonical(img)
    ornt = nib.aff2axcodes(canonical.affine)
    orig_ornt = nib.aff2axcodes(img.affine)
    desc = "".join(ornt)
    if ornt == orig_ornt:
        return canonical, f"{desc}（无需重排）"
    return canonical, f"{orig_ornt[0]}{orig_ornt[1]}{orig_ornt[2]} -> {desc}（RAS+ 重排）"


def assert_affine_match(
    img: Any,
    mask: Any,
    tolerance: float = AFFINE_MATCH_TOLERANCE,
    label: str = "",
) -> None:
    """mask 与原图 affine 一致性守卫：不一致硬报错（raise），禁止静默重采样。

    所有 mask 读写路径（nibabel 读写 mask 时）必须过这个守卫。比对 4x4 affine
    元素级 |diff| <= tolerance（默认 1e-4，浮点写入误差内视为一致）。

    label：报错时标注调用方上下文（如 series_id / 路径）。
    """
    img_aff = np.asarray(img.affine, dtype=float)
    mask_aff = np.asarray(mask.affine, dtype=float)
    if img_aff.shape != (4, 4) or mask_aff.shape != (4, 4):
        raise AffineMismatchError(
            f"affine 形状异常：img {img_aff.shape} / mask {mask_aff.shape}"
            + (f"（{label}）" if label else "")
        )
    diff = np.abs(img_aff - mask_aff)
    max_diff = float(diff.max())
    if max_diff > tolerance:
        raise AffineMismatchError(
            f"mask 与原图 affine 不一致（max |diff| = {max_diff:.2e} > {tolerance:.0e}），"
            f"禁止静默重采样；请先重排/重采样对齐再读写"
            + (f"（{label}）" if label else "")
            + f"\nimg affine:\n{img_aff}\nmask affine:\n{mask_aff}"
        )
