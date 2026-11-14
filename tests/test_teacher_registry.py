"""教师注册表与路由拒绝测试（M7-S3 做法 5 号项）。

- 注册表字段完整性：mask 教师缺 class_map_ref / data_contract_version 拒绝登记；
  feature 教师缺 feature_schema 拒绝登记。
- check_task 读注册表：total 套到 MR 被路由拒绝并带原因；未就绪任务拒绝；
  blocked 仍一票否决；allowed_tasks 白名单仍兜底。
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
    assert len(TEACHER_REGISTRY) == 12
    seen = {(e["teacher_id"], e["task"]) for e in TEACHER_REGISTRY}
    assert len(seen) == 12  # (teacher_id, task) 唯一
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


def test_allowed_tasks_whitelist_fallback() -> None:
    """注册表无条目、但 settings 白名单有 → 兜底放行（接口不变）。"""
    ok, _ = LicenseRegistry(_settings(allowed=["legacy_task"])).check_task("legacy_task")
    assert ok


def test_registered_task_works_without_whitelist() -> None:
    """注册表条目路由不依赖 allowed_tasks（替换数据来源，接口保留）。"""
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
