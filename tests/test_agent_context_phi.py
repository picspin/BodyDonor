"""AgentContext PHI 负测试（M0 CI）。

构造 AgentContext 后对其序列化产物跑 PHI 关键词与二进制检测，命中即 fail。
黑名单（硬边界）：像素数据、原始 DICOM 头（含患者字段）、文件路径中的真实姓名/ID。
"""

from __future__ import annotations

import pytest

from bodydonor.agent_context import (
    AgentContext,
    build_series_context,
    scan_for_phi,
)


def test_whitelist_fields_accepted() -> None:
    ctx = AgentContext(
        case_id="case-001",
        series_id="series-001",
        series_metadata={"modality": "CT", "slice_thickness_mm": 1.0, "num_slices": 300},
    )
    prompt = ctx.to_prompt()
    assert "case-001" in prompt
    assert scan_for_phi(prompt) == []


def test_unknown_top_level_field_rejected() -> None:
    with pytest.raises(ValueError, match="白名单"):
        AgentContext(patient_name="ZHANG San")  # type: ignore[arg-type]


def test_bytes_pixel_data_rejected_at_type_level() -> None:
    """像素/原始 DICOM 在类型上就不进入构造器。"""
    with pytest.raises(TypeError, match="二进制"):
        AgentContext(case_id="c1", series_metadata={"pixels": b"\x00\x01\x02" * 32})


def test_phi_in_nested_dict_rejected_at_construction() -> None:
    """嵌套 dict 里的 bytes 也进不来。"""
    with pytest.raises(TypeError, match="二进制"):
        AgentContext(case_id="c1", series_metadata={"raw_dicom": bytearray(64)})


def test_serialized_output_with_patient_name_fails() -> None:
    """序列化产物含 patient_name 字段名 -> PHI 检测命中。"""
    # 直接构造含 PHI 的序列化文本（模拟字段值泄漏；AgentContext 本身拒收该字段）
    text = '{"case_id": "c1", "patient_name": "ZHANG San"}'
    violations = scan_for_phi(text)
    assert any("patient name" in v for v in violations)


def test_scan_for_phi_detects_dicom_patient_fields() -> None:
    for text in (
        '{"patient_id": "12345"}',
        '{"patient_birth_date": "1980-01-01"}',
        '{"referring_physician": "Dr. Wang"}',
        '{"mrn": "8888888"}',
        '{"accession_number": "A100"}',
    ):
        assert scan_for_phi(text), f"应检出: {text}"


def test_scan_for_phi_detects_binary_blob() -> None:
    import base64

    blob = base64.b64encode(b"\x00" * 1024).decode()
    assert scan_for_phi(f'{{"pixels": "{blob}"}}')  # base64 大块命中


def test_build_series_context_filters_non_technical_fields() -> None:
    """build_series_context 只保留技术元数据白名单键，DICOM 头患者字段进不来。"""
    ctx = build_series_context(
        case_id="case-001",
        series_id="series-001",
        series_metadata={
            "modality": "CT",
            "num_slices": 300,
            # 以下是 DICOM 头患者字段——必须被过滤
            "patient_name": "ZHANG San",
            "patient_id": "P123",
            "institution_name": "Some Hospital",
        },
    )
    prompt = ctx.to_prompt()
    assert "ZHANG San" not in prompt
    assert "P123" not in prompt
    assert "Some Hospital" not in prompt
    assert "CT" in prompt
    assert scan_for_phi(prompt) == []


def test_clean_context_passes_phi_scan() -> None:
    ctx = AgentContext(
        case_id="case-001",
        series_id="series-001",
        series_metadata={
            "modality": "CT",
            "slice_thickness_mm": 1.0,
            "orientation": "axial",
            "num_slices": 300,
            "hu_range": [-1024, 3071],
        },
        qc_scores={"dice": 0.92, "flag": None},
        task_structure={"pipeline_stage": 3},
    )
    assert scan_for_phi(ctx.to_prompt()) == []
