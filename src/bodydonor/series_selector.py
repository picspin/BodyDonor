"""Series 选择器：规则过滤 + 用户勾选（M1，proposal §5、GRILL-REVIEW R3）。

规则过滤（规则 ≠ 硬删，全部可由用户勾回）：

- **排除 ImageType[0] == 'LOCALIZER'**（定位像）
- **DERIVED 不一刀切排除**：排序置后 + 报告标注（MPR 重建、多数后处理序列恰恰是 DERIVED）
- **层数 < min_slices（默认 30）排除**
- **非轴位排除**但允许用户勾回

交付形态：CLI/API（Web UI 勾选在 M2）。用户勾选 = 把 excluded 的 series 加回
`selected` 列表；规则只给推荐顺序与默认排除理由。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MIN_SLICES = 30  # 层数 < 30 排除（proposal §5）


@dataclass
class SeriesCandidate:
    """选择器的一条 Series 候选。"""

    series_id: str
    modality: str | None = None
    image_type: list[str] = field(default_factory=list)  # DICOM ImageType
    num_slices: int = 0
    orientation: str = "unknown"  # axial/sagittal/coronal/oblique/unknown
    slice_thickness_mm: float | None = None
    is_derived: bool = False
    excluded: bool = False
    exclude_reason: str | None = None
    flags: list[str] = field(default_factory=list)

    def to_report(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "modality": self.modality,
            "image_type": list(self.image_type),
            "num_slices": self.num_slices,
            "orientation": self.orientation,
            "slice_thickness_mm": self.slice_thickness_mm,
            "is_derived": self.is_derived,
            "excluded": self.excluded,
            "exclude_reason": self.exclude_reason,
            "flags": list(self.flags),
        }


def apply_rules(
    candidates: list[SeriesCandidate], min_slices: int = MIN_SLICES
) -> list[SeriesCandidate]:
    """就地应用规则过滤并返回排序后的列表。

    排序：非排除在前；DERIVED 置后（同为非排除时）。
    """
    for c in candidates:
        if c.image_type and c.image_type[0].upper() == "LOCALIZER":
            c.excluded = True
            c.exclude_reason = "LOCALIZER（定位像）"
        elif c.num_slices < min_slices:
            c.excluded = True
            c.exclude_reason = f"层数 {c.num_slices} < {min_slices}"
        elif c.orientation not in ("axial", "unknown"):
            c.excluded = True
            c.exclude_reason = f"非轴位（{c.orientation}），可勾回"

        # DERIVED 不排除：置后排序 + 标注
        if c.image_type and c.image_type[0].upper() == "DERIVED":
            c.is_derived = True
            c.flags.append("DERIVED（后处理/重建序列，排序置后，非排除）")

    # 非排除在前，DERIVED 置后；组内按层数降序
    candidates.sort(
        key=lambda c: (c.excluded, c.is_derived, -c.num_slices),
    )
    return candidates


def select(
    candidates: list[SeriesCandidate], user_overrides: list[str] | None = None
) -> list[str]:
    """最终选择：默认 = 未被排除的 series_id；用户可勾回被排除的。

    user_overrides：用户显式勾选的 series_id 列表（勾回排除项 / 主动只选部分）。
    """
    if user_overrides is not None:
        known = {c.series_id for c in candidates}
        unknown = set(user_overrides) - known
        if unknown:
            raise KeyError(f"unknown series_id in selection: {sorted(unknown)}")
        return list(user_overrides)
    return [c.series_id for c in candidates if not c.excluded]
