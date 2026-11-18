"""M-2 统一决策表测试：合并前后 36 条目实际判定逐条一致。

合并前先跑一遍本测试（research_use 与 research_distill 并存、判定读
research_distill + research_use 只挡 unverified），快照 36 条目的两项实际
判定——研究路由（LicenseRegistry.check_task）与研究蒸馏
（ontology.mask_allowed_in_distill_training）的 (allowed, reason-class)。
合并后同一测试断言判定不变；快照值以合并前实测为准，不手填期望值。

测试本身不随合并改口径：判定函数用默认入参、reason 只断言类
（键集合）不断言逐字文本，文本措辞允许随迁移微调。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bodydonor.config import Settings, TeacherConfig
from bodydonor.license_registry import TEACHER_REGISTRY, LicenseRegistry
from bodydonor.ontology import mask_allowed_in_distill_training

SNAPSHOT_PATH = Path(__file__).resolve().parent / "data" / "m2_decision_table.json"


def _reg() -> LicenseRegistry:
    # 与 test_teacher_registry._settings 同口径：blocked 一票否决清单不变
    return LicenseRegistry(
        Settings(
            teacher=TeacherConfig(
                allowed_tasks=["total", "total_mr"],
                blocked_tasks=["registry_blocked_task"],
            )
        )
    )


def _reason_key(ok: bool, reason: str) -> str:
    """reason 分类（不锁逐字文本）：放行时 conditional 条款写出与 "ok" 分开。"""
    if ok:
        return "ok" if reason == "ok" else "ok+license_inherits"
    if "无注册表条目" in reason:
        return "blocked:no-entry"
    if "not in teacher registry" in reason:
        return "blocked:no-entry"
    if "not supported by registry" in reason:
        return "blocked:modality"
    if "not ready" in reason:
        return "blocked:not-ready"
    if "BLOCKED" in reason:
        return "blocked:hard-block"
    if "unverified" in reason:
        return "blocked:unverified"
    if "修正产物不进蒸馏" in reason:
        return "blocked:nni-correction"
    return "blocked:other"


@pytest.fixture(scope="module")
def table() -> dict:
    reg = _reg()
    out = {}
    for e in TEACHER_REGISTRY:
        ok_route, r_route = reg.check_task(e["task"], modality=e["modalities"][0])
        ok_d, r_d = mask_allowed_in_distill_training(
            {"experimental": False, "source": "teacher", "task": e["task"]}
        )
        out[f"{e['teacher_id']}/{e['task']}"] = {
            "route": [ok_route, _reason_key(ok_route, r_route)],
            "distill": [ok_d, _reason_key(ok_d, r_d)],
        }
    return out


def test_snapshot_exists_and_covers_36() -> None:
    assert SNAPSHOT_PATH.exists(), "快照缺失（合并前必须先落盘）"
    snap = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert len(TEACHER_REGISTRY) == 36
    assert len(snap) == 36


def test_route_decisions_unchanged(table: dict) -> None:
    """合并前后研究路由判定逐条一致（route 不读 research_use，回归守卫）。"""
    snap = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert set(table) == set(snap)
    for key in sorted(snap):
        assert table[key]["route"] == snap[key]["route"], f"{key} route 判定变化"


def test_distill_decisions_unchanged(table: dict) -> None:
    """合并前后研究蒸馏判定逐条一致（铁律：行为保持）。"""
    snap = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    for key in sorted(snap):
        assert table[key]["distill"] == snap[key]["distill"], f"{key} distill 判定变化"


def test_entry_count_and_unique_keys_unchanged() -> None:
    assert len(TEACHER_REGISTRY) == 36
    keys = {(e["teacher_id"], e["task"]) for e in TEACHER_REGISTRY}
    assert len(keys) == 36


def test_two_axis_fields_single_decision_source() -> None:
    """M7-S3P2 ⑤：research_use 与 research_distill 字段名在注册表清零，判定读
    两轴 + license_verified（本断言随迁移提交落地；迁移前 research_distill
    并存，红）。两轴逐条有值且取值合法。"""
    for e in TEACHER_REGISTRY:
        assert "research_use" not in e, f"{e['teacher_id']}/{e['task']} 仍有 research_use"
        assert "research_distill" not in e, (
            f"{e['teacher_id']}/{e['task']} 仍有 research_distill（M7-S3P2 ⑤ 退役）"
        )
        assert e["attribution_required"] is True
        assert isinstance(e["share_alike"], bool)
        assert isinstance(e["license_verified"], bool)


def test_no_fallback_reason_class_in_snapshot(table: dict) -> None:
    """R4 审查 W：blocked:other 是 _reason_key 兜底类——快照里出现即说明某
    拒绝分支缺可识别关键词，措辞变化会被兜底吞掉。兜底类必须恒空。"""
    for key, item in table.items():
        assert item["distill"][1] != "blocked:other", f"{key} distill 落兜底类"
        assert item["route"][1] != "blocked:other", f"{key} route 落兜底类"


def test_share_alike_semantics_nninteractive_vs_synthseg() -> None:
    """M7-S3P2 ⑤：SA 轴语义两案例——nnInteractive（CC BY-NC-SA 4.0）是本仓
    唯一 ShareAlike 来源，派生物须以 BY-NC-SA 分发；SynthSeg（FreeSurfer SLA）
    是署名+修改标识条款，SLA 非 ShareAlike 许可（用户 2026-11-18 确认），
    share_alike=False（用户 2026-11-18 确认）。"""
    by_task = {(e["teacher_id"], e["task"]): e for e in TEACHER_REGISTRY}
    nni = by_task[("nninteractive", "interactive_correction")]
    assert nni["share_alike"] is True
    assert any("CC-BY-NC-SA" in lic for lic in nni["output_license_inherits"])
    synthseg = by_task[("synthseg", "brain_mri_qc")]
    assert synthseg["share_alike"] is False
    assert any("FreeSurfer Software License" in lic and "1(c)/(d)" in lic
               for lic in synthseg["output_license_inherits"])
    # 其余 34 条目全无 SA（flexray CC BY-NC 4.0 也不是 ShareAlike）
    others = [e for e in TEACHER_REGISTRY if e is not nni]
    assert all(e["share_alike"] is False for e in others)


def test_src_no_research_distill_residual() -> None:
    """M7-S3P2 ⑤：src 全树 research_distill 零残留（字段名退役，防线防回潮）。"""
    src_root = Path(__file__).resolve().parents[1] / "src"
    hits = [
        str(p) for p in src_root.rglob("*.py")
        if "research_distill" in p.read_text(encoding="utf-8")
    ]
    assert hits == [], f"src 残留 research_distill：{hits}"
