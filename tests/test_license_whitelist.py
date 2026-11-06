"""许可白名单负测试（M0 CI）。

推理 worker 的任务分发层拒绝执行白名单外任务：
- brain_aneurysm：禁用任务（CC BY-NC 4.0 且无商业授权渠道）——一票否决
- heartchambers_highres：licensed 任务，未显式加白——拒绝
- 任意未知任务名——拒绝
只有 teacher.allowed_tasks 里的任务放行。
"""

from __future__ import annotations

import pytest

from bodydonor.config import Settings, TeacherConfig, load_settings
from bodydonor.license_registry import LicenseRegistry


@pytest.fixture
def registry() -> LicenseRegistry:
    """项目 config/settings.yaml 的白名单（核查结论的落地处）。"""
    return LicenseRegistry(load_settings())


def test_blocked_task_brain_aneurysm_rejected(registry: LicenseRegistry) -> None:
    """CC BY-NC 4.0 且无商业授权渠道的任务必须被一票否决，即使有人误加白。"""
    ok, msg = registry.check_task("brain_aneurysm")
    assert not ok
    assert "BLOCKED" in msg


def test_blocked_overrides_whitelist() -> None:
    """即使 blocked 任务被错误写进 allowed_tasks，blocked 仍一票否决。"""
    bad = Settings(
        teacher=TeacherConfig(
            allowed_tasks=["brain_aneurysm"],
            blocked_tasks=["brain_aneurysm"],
        )
    )
    ok, msg = LicenseRegistry(bad).check_task("brain_aneurysm")
    assert not ok
    assert "BLOCKED" in msg


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
        assert "not in teacher.allowed_tasks" in msg


def test_unknown_task_rejected(registry: LicenseRegistry) -> None:
    ok, msg = registry.check_task("totally_unknown_task")
    assert not ok
    assert "not in teacher.allowed_tasks" in msg


def test_total_task_allowed(registry: LicenseRegistry) -> None:
    """total 是商用蒸馏唯一安全教师 mask 来源，必须在白名单。"""
    ok, msg = registry.check_task("total")
    assert ok, msg


def test_commercial_distill_whitelist_only_total() -> None:
    """商用蒸馏白名单只允许 total（LICENSES-THIRD-PARTY.md §7）。"""
    t = load_settings().teacher
    assert t.commercial_distill_tasks == ["total"]
    assert t.is_commercial_distill_allowed("total")
    assert not t.is_commercial_distill_allowed("lung_vessels")
    assert not t.is_commercial_distill_allowed("heartchambers_highres")


def test_bare_settings_fail_closed() -> None:
    """无配置时白名单为空——fail-closed，任何任务都拒绝。"""
    t = Settings().teacher
    assert t.allowed_tasks == []
    ok, _ = LicenseRegistry(Settings()).check_task("total")
    assert not ok


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
    # 核查确认禁用
    assert "brain_aneurysm" in t.blocked_tasks
