"""AgentContext 构造器：LLM 编排只见元数据，不见 PHI（proposal §4.5、GRILL-REVIEW 1.6）。

白名单字段：case_id/series_id、Series 技术元数据（模态、层厚、方向、层数、HU 范围）、
QC 分数与 flag、任务/项目结构、映射表。
黑名单（硬边界，序列化层强制）：像素数据、原始 DICOM 头（含患者字段）、
文件路径中的真实姓名/ID、De-ID 映射表本身。

CI 测试：构造 AgentContext 后对其序列化产物跑 PHI 关键词与二进制检测，命中即 fail。
"""

from __future__ import annotations

import json
import re
from typing import Any

# PHI 关键词：序列化产物中出现即视为泄漏（CI 负测试 + 运行时自检共用）
PHI_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"patient[_\s-]?name", "patient name field"),
    (r"patient[_\s-]?id(?!\w)", "patient id field"),
    (r"patient[_\s-]?birth", "patient birth date field"),
    (r"birth[_\s-]?date", "birth date"),
    (r"\bdob\b", "date of birth"),
    (r"\bmrn\b", "medical record number"),
    (r"accession[_\s-]?number", "accession number"),
    (r"study[_\s-]?id(?!\w)", "study id field"),
    (r"referring[_\s-]?physician", "referring physician"),
    (r"\bssn\b", "social security number"),
    (r"station[_\s-]?name", "station name"),
    (r"institution[_\s-]?name", "institution name"),
    (r"(?i)张三|李四|王五", "chinese placeholder personal names"),
)

# 二进制/像素痕迹：base64 大块或高比例非文本字符
_BINARY_SNIFF_LEN = 256


def _looks_binary(text: str) -> bool:
    sample = text[:_BINARY_SNIFF_LEN]
    if not sample:
        return False
    nul = sample.count("\x00")
    if nul > 0:
        return True
    # 大段 base64 疑似像素/文件内嵌
    b64 = re.findall(r"[A-Za-z0-9+/=]{512,}", text)
    return bool(b64)


def _enforce(value: Any, path: str) -> None:
    """黑名单字段在类型上就进不来：显式拒绝已知危险类型。"""
    # bytes（像素/原始 DICOM）不允许进入 AgentContext 字段
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(
            f"AgentContext 字段不允许二进制数据（像素/原始 DICOM）：{path}"
        )


class AgentContext:
    """LLM 编排上下文。只接受白名单字段；序列化层强制黑名单。"""

    ALLOWED_TOP_LEVEL = frozenset(
        {
            "case_id",
            "series_id",
            "series_metadata",
            "qc_scores",
            "task_structure",
            "ontology_map",
        }
    )

    def __init__(self, **fields: Any):
        unknown = set(fields) - self.ALLOWED_TOP_LEVEL
        if unknown:
            raise ValueError(
                f"AgentContext 只接受白名单字段，拒绝: {sorted(unknown)}"
            )
        for name, value in fields.items():
            _enforce(value, name)
            if isinstance(value, dict):
                _enforce_dict(value, name)
        self._fields = fields

    def to_prompt(self) -> str:
        """序列化为 prompt 文本，出口处跑 PHI/二进制自检，命中即 raise。"""
        text = json.dumps(self._fields, ensure_ascii=False, default=str, indent=2)
        violations = scan_for_phi(text)
        if violations:
            raise ValueError(f"AgentContext 序列化产物命中 PHI/二进制检测: {violations}")
        return text

    def to_dict(self) -> dict[str, Any]:
        return dict(self._fields)


def _enforce_dict(d: dict[str, Any], path: str) -> None:
    for k, v in d.items():
        _enforce(v, f"{path}.{k}")
        if isinstance(v, dict):
            _enforce_dict(v, f"{path}.{k}")


def scan_for_phi(text: str) -> list[str]:
    """对序列化产物跑 PHI 关键词与二进制检测，返回命中列表（空 = 通过）。"""
    violations: list[str] = []
    for pattern, label in PHI_PATTERNS:
        if re.search(pattern, text):
            violations.append(label)
    if _looks_binary(text):
        violations.append("binary/base64 payload suspected")
    return violations


def build_series_context(
    case_id: str,
    series_id: str,
    series_metadata: dict[str, Any],
) -> AgentContext:
    """从导入报告字段构造安全的编排上下文（只收技术元数据，不收 DICOM 头）。"""
    allowed_meta_keys = {
        "modality",
        "slice_thickness_mm",
        "orientation",
        "num_slices",
        "hu_range",
        "spacing_mm",
        "rows",
        "columns",
        "is_derived",
        "is_localizer",
    }
    safe_meta = {k: v for k, v in series_metadata.items() if k in allowed_meta_keys}
    return AgentContext(
        case_id=case_id,
        series_id=series_id,
        series_metadata=safe_meta,
    )
