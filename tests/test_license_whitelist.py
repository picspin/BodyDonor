"""许可白名单负测试（M0 CI）。

推理 worker 的任务分发层拒绝执行白名单外任务（M7-S3F ② 起注册表唯一来源）：
- blocked_tasks 机制：一票否决（R3 ④删 brain_aneurysm 商用理由后机制保留，
  blocked 列表现在为空——机制测试用内存条目验证）
- heartchambers_highres：licensed 任务，注册表无条目——拒绝
- 任意未知任务名——拒绝
放行路径：注册表就绪条目（M7-S3P2 ⑥ 就绪三分，(task, modality) 门控）；
白名单回退已退役（M7-S3F ②）——注册表无条目即拒绝带原因。
"""

from __future__ import annotations

import pytest

from bodydonor.config import Settings, TeacherConfig, load_settings
from bodydonor.license_registry import LicenseRegistry


@pytest.fixture
def registry() -> LicenseRegistry:
    """项目 config/settings.yaml 的白名单（核查结论的落地处）。"""
    return LicenseRegistry(load_settings())


def test_blocked_task_vetoed_in_memory_entry() -> None:
    """blocked_tasks 机制：一票否决（内存条目验证——R3 ④删 brain_aneurysm
    商用理由后项目 config 的 blocked 列表为空，机制本身保留）。"""
    bad = Settings(
        teacher=TeacherConfig(
            allowed_tasks=["some_task"],
            blocked_tasks=["some_task"],
        )
    )
    ok, msg = LicenseRegistry(bad).check_task("some_task")
    assert not ok
    assert "BLOCKED" in msg


def test_project_config_blocked_list_empty() -> None:
    """R3 ④：项目 config/settings.yaml 的 blocked 列表为空（brain_aneurysm
    商用理由已删，无其他任务被阻断）。"""
    t = load_settings().teacher
    assert t.blocked_tasks == []


def test_licensed_task_not_whitelisted_by_default(registry: LicenseRegistry) -> None:
    """licensed 任务（heartchambers_highres 等）默认不在白名单，须显式加白 + 登记。"""
    for task in (
        "heartchambers_highres",
        "appendicular_bones",
        "tissue_types",
        "coronary_arteries",
        "aortic_sinuses",
        "renal_arteries",
    ):
        ok, msg = registry.check_task(task)
        assert not ok, f"{task} 不应默认放行"
        assert "not in teacher registry" in msg


def test_unknown_task_rejected(registry: LicenseRegistry) -> None:
    ok, msg = registry.check_task("totally_unknown_task")
    assert not ok
    assert "not in teacher registry" in msg


def test_total_task_allowed(registry: LicenseRegistry) -> None:
    """total 是商用蒸馏唯一安全教师 mask 来源，必须在白名单。"""
    ok, msg = registry.check_task("total")
    assert ok, msg


def test_commercial_distill_config_retired() -> None:
    """R3 ④：commercial_distill_tasks 字段已删（TeacherConfig 无此字段）。"""
    t = load_settings().teacher
    assert not hasattr(t, "commercial_distill_tasks")
    assert not hasattr(t, "is_commercial_distill_allowed")


def test_bare_settings_registry_ready_task_allowed() -> None:
    """空配置：注册表就绪条目仍放行（契约变化，M7-S3 记录在案；M7-S3P2 ⑥
    ready 布尔退役——就绪读三分 weights_present/class_map_present/share_alike）。

    M7-S3F ②：check_task 数据来源改读注册表（代码级静态条目，Apache-2.0 已核查），
    注册表内就绪条目放行——配置不再能撤销注册表内任务，仅 blocked_tasks
    能否决；注册表外未注册任务仍 fail-closed（白名单回退已退役）。
    """
    t = Settings().teacher
    assert t.allowed_tasks == []
    ok, _ = LicenseRegistry(Settings()).check_task("total")
    assert ok  # 注册表代码级条目，非配置
    ok, _ = LicenseRegistry(Settings()).check_task("totally_unregistered_task")
    assert not ok
    # 白名单回退已退役：配置里加白注册表外任务也不放行
    ok, msg = LicenseRegistry(
        Settings(teacher=TeacherConfig(allowed_tasks=["never_registered"]))
    ).check_task("never_registered")
    assert not ok
    assert "not in teacher registry" in msg


def test_settings_yaml_matches_verified_license_facts() -> None:
    """config/settings.yaml 的白名单与 LICENSES-THIRD-PARTY.md 核查结论一致。"""
    from bodydonor.config import load_settings

    t = load_settings().teacher
    # 核查确认可用的 open 任务必须在白名单
    for task in ("total", "total_mr", "lung_vessels", "body", "vertebrae_pp"):
        assert task in t.allowed_tasks, f"{task} 应在白名单（open 任务）"
    # 核查确认需 license 的任务默认不加白
    for task in ("heartchambers_highres", "appendicular_bones", "tissue_types", "face"):
        assert task not in t.allowed_tasks, f"{task} 是 licensed 任务，默认不加白"
    # R3 ④：brain_aneurysm 商用理由已删，无任务被 blocked（列表为空）
    assert t.blocked_tasks == []
