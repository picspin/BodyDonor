"""NIfTI 几何校验（M1，proposal §5）：affine 各向异性、非正交方向、非毫米 spacing 警告。

NIfTI 单文件没有 Series 概念（ingest() -> [Series] 单元素列表），但几何校验必须做，
M1 验收点名。三条检查全部是**警告**（不拒收）——几何异常仍可导入，导入报告可见：

- **affine 各向异性**：spacing（zooms）max/min 比值 > 阈值（默认 3×）
- **非正交方向**：affine 旋转矩阵列向量两两点积绝对值 > 阈值（默认 0.02）
- **非毫米 spacing**：xyzt_units 不是 mm（或无法判定）
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

MAX_ANISO_RATIO = 3.0  # spacing max/min > 3× 警告
ORTHO_TOLERANCE = 0.02  # 方向列向量两两点积绝对值 > 0.02（非正交）警告


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
