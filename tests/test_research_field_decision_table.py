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
                blocked_tasks=["brain_aneurysm"],
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


def test_research_distill_is_single_decision_source() -> None:
    """M-2 合并后单一来源：research_use 字段名在注册表清零，判定读
    research_distill（本断言随合并提交落地；合并前 research_use 并存，红）。"""
    for e in TEACHER_REGISTRY:
        assert "research_use" not in e, f"{e['teacher_id']}/{e['task']} 仍有 research_use"
        assert e["research_distill"] in ("allowed", "conditional", "blocked", "unverified")
