"""教师注册表与路由拒绝测试（M7-S3 做法 5 号项）。

- 注册表字段完整性：mask 教师缺 class_map_ref / data_contract_version 拒绝登记；
  feature 教师缺 feature_schema 拒绝登记。
- check_task 读注册表：total 套到 MR 被路由拒绝并带原因；未就绪任务拒绝；
  blocked 仍一票否决；白名单回退已退役（M7-S3F ②）——注册表无条目即拒绝。
- 回退语义：planned/actual、跳过写原因、结构未覆盖 vs 输出缺失区分。

纯合成 fixture，不依赖本地产物，CI 可跑。
"""

from __future__ import annotations

import pytest

from bodydonor.config import Settings, TeacherConfig
from bodydonor.license_registry import (
    TEACHER_REGISTRY,
    FallbackRecord,
    LicenseRegistry,
    classify_missing_output,
    validate_entry,
)
from bodydonor.segmentation import SegmentWorkerError, check_worker_task


def _settings(allowed: list[str] | None = None, blocked: list[str] | None = None) -> Settings:
    return Settings(
        teacher=TeacherConfig(
            allowed_tasks=allowed if allowed is not None else ["total", "total_mr"],
            blocked_tasks=blocked or ["brain_aneurysm"],
        )
    )


# --- 注册表条目自校验（模块加载时已跑一遍；这里显式断言全部合法）---


def test_all_entries_pass_validation() -> None:
    assert len(TEACHER_REGISTRY) == 36
    seen = {(e["teacher_id"], e["task"]) for e in TEACHER_REGISTRY}
    assert len(seen) == 36  # (teacher_id, task) 唯一
    for e in TEACHER_REGISTRY:
        validate_entry(e)  # 不 raise 即通过


def test_mask_entry_missing_class_map_ref_rejected() -> None:
    with pytest.raises(ValueError, match="class_map_ref"):
        validate_entry(
            {
                "teacher_id": "x",
                "task": "t",
                "checkpoint_id": "c",
                "modalities": ("CT",),
                "output_type": "mask",
                "research_use": "allowed",
                "known_restrictions": [],
                "provenance": {"source": "s", "verified": "v"},
                "ready": True,
                "weight_integrity": {},
            }
        )


def test_mask_entry_missing_data_contract_version_rejected() -> None:
    e = {
        "teacher_id": "x",
        "task": "t",
        "checkpoint_id": "c",
        "modalities": ("CT",),
        "output_type": "mask",
        "research_use": "allowed",
        "known_restrictions": [],
        "provenance": {"source": "s", "verified": "v"},
        "ready": True,
        "weight_integrity": {},
        "class_map_ref": "m",
    }
    with pytest.raises(ValueError, match="data_contract_version"):
        validate_entry(e)


def test_feature_entry_missing_feature_schema_rejected() -> None:
    with pytest.raises(ValueError, match="feature_schema"):
        validate_entry(
            {
                "teacher_id": "x",
                "task": "t",
                "checkpoint_id": "c",
                "modalities": ("MR",),
                "output_type": "feature",
                "research_use": "allowed",
                "known_restrictions": [],
                "provenance": {"source": "s", "verified": "v"},
                "ready": True,
                "weight_integrity": {},
            }
        )


def test_missing_required_field_rejected() -> None:
    e = dict(TEACHER_REGISTRY[0])
    del e["provenance"]
    with pytest.raises(ValueError, match="provenance"):
        validate_entry(e)


def test_ready_with_placeholder_class_map_ref_rejected_both_wordings() -> None:
    """W16：ready=True × 占位 class_map_ref 拒绝——「待建」与「待落地」都触发守卫
    （永远不能在类映射落地前 ready，M7-S3F2-REPORT §3.3 陈述成立）。"""
    base = {
        "teacher_id": "x",
        "task": "t",
        "checkpoint_id": "c",
        "modalities": ("CT",),
        "output_type": "mask",
        "research_use": "allowed",
        "known_restrictions": [],
        "provenance": {"source": "s", "verified": "v"},
        "ready": True,
        "weight_integrity": {},
        "class_map_ref": "ts-classes/v1 (待建)",
        "data_contract_version": "v1",
    }
    with pytest.raises(ValueError, match="占位"):
        validate_entry(base)
    e2 = dict(base)
    e2["class_map_ref"] = "ts-classes/v1 (待落地：2.10.0 class_map 不含，需按 README 定义建)"
    with pytest.raises(ValueError, match="占位"):
        validate_entry(e2)


# --- check_task 读注册表 ---


def test_total_on_mr_routed_rejected_with_reason() -> None:
    """total 是 CT-only；套到 MR 须被路由拒绝并带模态原因（M7-S3 测试项 1）。"""
    ok, msg = LicenseRegistry(_settings()).check_task("total", modality="MR")
    assert not ok
    assert "modality 'MR' not supported" in msg


def test_worker_task_mr_rejected_via_check_worker_task() -> None:
    """S3 B1 失败测试先行：worker 分发路径（check_worker_task）带 modality
    时 CT-only 任务派 MR 被拒（生产派发链路的门控，不只函数层）。"""
    with pytest.raises(SegmentWorkerError, match="modality 'MR' not supported"):
        check_worker_task("total", _settings(), modality="MR")


def test_ready_follows_modality_filter() -> None:
    """S3 W1 失败测试先行：ready 判定跟随模态过滤——同 task 双模态条目
    （CT ready=False、MR ready=True）下 CT 拒绝、MR 放行。"""
    entry_ct = dict(TEACHER_REGISTRY[0])
    entry_ct["modalities"] = ("CT",)
    entry_ct["ready"] = False
    entry_mr = dict(TEACHER_REGISTRY[0])
    entry_mr["modalities"] = ("MR",)
    entry_mr["ready"] = True
    reg = LicenseRegistry(_settings())
    from bodydonor import license_registry as lr

    saved = lr.TEACHER_REGISTRY
    lr.TEACHER_REGISTRY = [entry_ct, entry_mr]
    try:
        ok, _ = reg.check_task("total", modality="CT")
        assert not ok  # CT 条目 ready=False → 拒绝（不落到 MR 条目）
        ok, msg = reg.check_task("total", modality="MR")
        assert ok, msg
    finally:
        lr.TEACHER_REGISTRY = saved


def test_modality_normalization_case_and_whitespace() -> None:
    """S3 W8 失败测试先行：模态比较归一化——小写/带空白按大写去空白处理。"""
    ok, msg = LicenseRegistry(_settings()).check_task("total", modality=" ct ")
    assert ok, msg
    ok, msg = LicenseRegistry(_settings()).check_task("total", modality="ct")
    assert ok, msg
    # 归一化后仍不匹配的模态照拒
    ok, msg = LicenseRegistry(_settings()).check_task("total", modality="PT")
    assert not ok
    assert "modality 'PT' not supported" in msg


def test_total_on_ct_allowed() -> None:
    ok, _ = LicenseRegistry(_settings()).check_task("total", modality="CT")
    assert ok


def test_mr_task_not_ready_rejected() -> None:
    """注册表 MR 任务 ready=False → 拒绝带原因（TS MR 权重未下载）。"""
    ok, msg = LicenseRegistry(_settings()).check_task("body_mr", modality="MR")
    assert not ok
    assert "not ready" in msg


def test_blocked_task_still_vetoed() -> None:
    ok, msg = LicenseRegistry(_settings()).check_task("brain_aneurysm")
    assert not ok
    assert "BLOCKED" in msg


def test_unknown_task_rejected() -> None:
    ok, msg = LicenseRegistry(_settings(allowed=[])).check_task("no_such_task")
    assert not ok
    assert "registry" in msg


def test_whitelist_fallback_retired() -> None:
    """M7-S3F ② 失败测试先行：白名单回退已退役——注册表无条目即拒绝带原因，
    改 allowed_tasks 内容不再影响路由结果。"""
    ok, msg = LicenseRegistry(_settings(allowed=["legacy_task"])).check_task("legacy_task")
    assert not ok
    assert "not in teacher registry" in msg
    # blocked/ready 语义不受白名单影响：blocked 一票否决保持
    ok, msg = LicenseRegistry(_settings(allowed=["brain_aneurysm"])).check_task("brain_aneurysm")
    assert not ok
    assert "BLOCKED" in msg


def test_allowed_tasks_registered_tasks_route_via_registry() -> None:
    """M7-S3F ② 失败测试先行：allowed_tasks 的分割任务已登记进注册表——
    路由走注册表（ready=False 拒绝、unverified 标记、空配置可拒绝/可放行）。"""
    from bodydonor.license_registry import TEACHER_REGISTRY as R

    by_task = {e["task"]: e for e in R}
    # 24 个 TS 任务（组一 18 + 组二 5 + 组三 vertebrae_body 单列；LEGACY 不登记）
    for t in (
        "lung_vessels", "body", "cerebral_bleed", "hip_implant",
        "pleural_pericard_effusion", "head_glands_cavities", "head_muscles",
        "headneck_bones_vessels", "headneck_muscles", "liver_vessels",
        "oculomotor_muscles", "lung_nodules", "kidney_cysts", "breasts",
        "liver_segments", "craniofacial_structures", "abdominal_muscles",
        "ventricle_parts", "teeth", "trunk_cavities", "liver_lesions",
        "vertebrae_pp", "vertebrae_pp_refined", "vertebrae_body",
    ):
        e = by_task.get(t)
        assert e is not None, f"{t} 应已登记进注册表"
        assert e["teacher_id"] == "totalsegmentator"
        assert e["ready"] is False  # 权重未部署，一律不造假 ready
    # open 清单双证据齐全组 → allowed；仅 README 单证据组 → unverified
    assert by_task["lung_vessels"]["research_use"] == "allowed"
    assert by_task["teeth"]["research_use"] == "unverified"
    assert by_task["vertebrae_body"]["research_use"] == "unverified"
    assert any("commercial_models" in r for r in by_task["vertebrae_body"]["known_restrictions"])
    # 空配置（allowed_tasks=[]）下已登记任务走注册表：ready=False 拒绝带原因
    ok, msg = LicenseRegistry(_settings(allowed=[])).check_task("lung_vessels", modality="CT")
    assert not ok
    assert "not ready" in msg
    # ready 条目（total）不依赖白名单
    ok, _ = LicenseRegistry(_settings(allowed=[])).check_task("total", modality="CT")
    assert ok


# --- 回退语义（§2.2.3）---


def test_fallback_record_planned_then_skipped_with_reason() -> None:
    rec = FallbackRecord(planned={"teacher_id": "totalsegmentator", "task": "total"})
    assert rec.status == "planned"
    rec.status = "skipped"
    rec.skip_reason = "modality MR not supported"
    assert rec.actual is None
    assert rec.skip_reason == "modality MR not supported"


def test_fallback_record_planned_then_executed() -> None:
    rec = FallbackRecord(planned={"teacher_id": "totalsegmentator", "task": "total"})
    rec.status = "executed"
    rec.actual = {"teacher_id": "totalsegmentator", "task": "total"}
    assert rec.planned == {"teacher_id": "totalsegmentator", "task": "total"}
    assert rec.actual == {"teacher_id": "totalsegmentator", "task": "total"}
    assert rec.status == "executed"


def test_structure_not_covered_vs_output_missing() -> None:
    """执行成功没返回目标 mask：结构未覆盖 ≠ 输出缺失，不计成 Dice 0。"""
    assert (
        classify_missing_output(["prostate"], [], ["aorta", "liver"])
        == "structure_not_covered"
    )
    assert classify_missing_output(["liver"], [], ["aorta", "liver"]) == "output_missing"
    assert classify_missing_output([], [], ["liver"]) == "output_missing"
    assert classify_missing_output(["liver"], ["liver"], ["liver"]) == "executed"


def test_structure_not_covered_partial_overlap() -> None:
    """请求多结构、教师只覆盖其一：算覆盖（至少一个目标在教师范围内）。"""
    assert (
        classify_missing_output(["prostate", "liver"], [], ["liver"])
        == "output_missing"
    )
