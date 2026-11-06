"""De-ID：DICOM 头 PS3.15 去标识 + 像素烧录检测（M1，proposal §4.2、§5）。

Ingest 第一步：每个 DICOM 文件先过本模块——

- **PS3.15 基本去标识**（Basic Confidentiality Profile 的 head 字段子集）：
  PatientName / PatientID / PatientBirthDate / PatientSex / OtherPatientIDs /
  AccessionNumber / ReferringPhysicianName / 系列描述等患者/机构字段。
  cloud_demo 收「已去标识数据」：这些字段不落库、不进存储（proposal §4.2）。
- **像素烧录检测（burned-in annotation）**：DICOM 标签 (0028,0301)
  BurnedInAnnotation == "YES" → **拒收**（不是隔离留存，proposal §4.2）。
  标签缺失按 UNKNOWN 处理：字符串启发式（像素内出现的患者名等）无法可靠判定，
  M1 拒绝策略只对显式 YES 生效；UNKNOWN 在导入报告中标注为警告。

模块边界：本模块不读像素数据本身（不做 OCR），只读头字段——
PS3.15 基本档的像素烧录声明 + 头内可见的患者标识泄漏检测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# PS3.15 Basic Confidentiality Profile 的 head 字段子集（M1：只做头部去标识，
# UID 保留关系用 Cosmetic/UID 变体策略，不破坏 series 内引用）。
DEID_TAG_KEYWORDS: dict[str, str] = {
    "PatientName": "patient_name",
    "PatientID": "patient_id",
    "PatientBirthDate": "patient_birth_date",
    "PatientSex": "patient_sex",
    "PatientAge": "patient_age",
    "PatientAddress": "patient_address",
    "PatientTelephoneNumbers": "patient_telephone",
    "OtherPatientIDs": "other_patient_ids",
    "OtherPatientNames": "other_patient_names",
    "MotherBirthName": "mother_birth_name",
    "AccessionNumber": "accession_number",
    "ReferringPhysicianName": "referring_physician_name",
    "PerformingPhysicianName": "performing_physician_name",
    "NameOfPhysiciansReadingStudy": "reading_physicians",
    "OperatorsName": "operators_name",
    "InstitutionName": "institution_name",
    "InstitutionAddress": "institution_address",
    "StudyID": "study_id",
    "StationName": "station_name",
    "DeviceSerialNumber": "device_serial_number",
}

# 像素烧录时常见的头字段泄漏（有值即视为烧录风险证据）
BURNED_ANNOTATION_TAG = "BurnedInAnnotation"


@dataclass
class DeidResult:
    """单个 DICOM 文件的 De-ID 检查结果。"""

    accepted: bool
    burned_in_annotation: str = "UNKNOWN"  # YES | NO | UNKNOWN
    removed_fields: dict[str, str] = field(default_factory=dict)  # keyword -> 原值
    warnings: list[str] = field(default_factory=list)

    def to_report(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "burned_in_annotation": self.burned_in_annotation,
            "removed_field_count": len(self.removed_fields),
            "warnings": list(self.warnings),
        }


def inspect_dicom(dataset: Any) -> DeidResult:
    """检查 DICOM Dataset：像素烧录声明 + 头字段 PHI 收集。不修改原 Dataset。

    像素烧录 (0028,0301) == 'YES' → 拒收（proposal §4.2：不通过则拒收，不是隔离留存）。
    """
    # 1. 像素烧录检测
    bia = str(getattr(dataset, BURNED_ANNOTATION_TAG, "UNKNOWN")).strip().upper()
    if bia not in ("YES", "NO"):
        bia = "UNKNOWN"
    if bia == "YES":
        return DeidResult(accepted=False, burned_in_annotation=bia)

    # 2. 头字段 PHI 收集（有值的才记录）
    removed: dict[str, str] = {}
    for keyword, norm in DEID_TAG_KEYWORDS.items():
        value = getattr(dataset, keyword, None)
        if value is None:
            continue
        s = str(value).strip()
        if s and s not in ("", "NONE", "ANONYMOUS", "ANON", "UNKNOWN", "REMOVED"):
            removed[norm] = s

    warnings: list[str] = []
    if bia == "UNKNOWN":
        warnings.append(
            "BurnedInAnnotation 标签缺失（UNKNOWN）：无法确认像素内无烧录注释，"
            "上传页声明勾选为唯一防线"
        )

    return DeidResult(
        accepted=True, burned_in_annotation=bia, removed_fields=removed, warnings=warnings
    )


def deid_dataset(dataset: Any) -> DeidResult:
    """对 DICOM Dataset 原地执行去标识（先 inspect 再改写）。

    返回 DeidResult；不通过（烧录 YES）时原样返回、不改写。
    """
    result = inspect_dicom(dataset)
    if not result.accepted:
        return result
    for keyword in DEID_TAG_KEYWORDS:
        if hasattr(dataset, keyword):
            setattr(dataset, keyword, "")
    return result
