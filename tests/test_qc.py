"""QC 引擎单测（M3，proposal §7）：规则判定 + 阈值集中 + 两级校准。"""

from __future__ import annotations

import numpy as np
import pytest

from bodydonor.qc import (
    QC_THRESHOLDS_PATH,
    RULE_MULTI_TEACHER,
    SamplingPlan,
    load_qc_thresholds,
    rule_boundary_smoothness,
    rule_connected_component,
    rule_holes,
    rule_hu_distribution,
    rule_left_right_symmetry,
    rule_volume_prior,
    run_qc,
    sample_review_tasks,
)


@pytest.fixture
def th() -> dict:
    return load_qc_thresholds()


def test_thresholds_file_exists():
    assert QC_THRESHOLDS_PATH.exists(), (
        "config/qc_thresholds.yaml 必须存在（阈值集中，proposal §7）"
    )


def test_thresholds_all_have_source(th):
    """每个阈值带 source 注记（proposal §7：报告里可见）。"""
    for rule, cfg in th["rules"].items():
        assert "source" in cfg, f"rule {rule} 缺 source"
        assert cfg["source"] in ("cold_start", "calibrated_on_N")


def test_multi_teacher_disabled_in_m3(th):
    """多教师一致性 M3 不启用（proposal §7；M4 学生模型出现后启用）。"""
    cfg = th["rules"][RULE_MULTI_TEACHER]
    assert cfg["enabled"] is False
    assert "M4" in cfg["note"]
    # run_qc 不对该规则产生 flagged 记录
    shape = (32, 32, 16)
    arr = np.zeros(shape, dtype=np.uint8)
    arr[5:20, 5:20, 2:10] = 1
    r = run_qc(arr, shape, "mv-x", "sid-x", thresholds=th)
    assert RULE_MULTI_TEACHER not in r.flags
    assert r.rule_scores[RULE_MULTI_TEACHER].get("skipped")


def test_volume_prior_flags_tiny_mask():
    """体积先验：< min_frac 残片 flagged。"""
    shape = (128, 128, 64)
    arr = np.zeros(shape, dtype=np.uint8)
    arr[50:52, 50:52, 30:32] = 1  # 8 体素 / 1048576 ≈ 0.00076%
    cfg = {"enabled": True, "min_frac": 0.0001, "max_frac": 0.65}
    flagged, _score, detail = rule_volume_prior(arr, shape, cfg)
    assert flagged
    assert detail["volume_frac"] < 0.0001


def test_volume_prior_flags_whole_mask():
    """体积先验：全 mask flagged。"""
    shape = (32, 32, 16)
    arr = np.ones(shape, dtype=np.uint8)
    cfg = {"enabled": True, "min_frac": 0.0001, "max_frac": 0.65}
    flagged, _score, _detail = rule_volume_prior(arr, shape, cfg)
    assert flagged


def test_connected_component_flags_scatter():
    """连通域：碎成多域 flagged（主域占比低）。"""
    shape = (64, 64, 32)
    arr = np.zeros(shape, dtype=np.uint8)
    rng = np.random.default_rng(3)
    for _ in range(40):
        p = rng.integers(5, 58, size=3)
        arr[p[0]:p[0]+3, p[1]:p[1]+3, p[2]:p[2]+3] = 1
    flagged, _score, detail = rule_connected_component(
        arr, {"enabled": True, "min_main_frac": 0.8, "max_components": 20}
    )
    assert flagged
    assert detail["worst_n_components"] > 20 or detail["worst_main_frac"] < 0.8


def test_connected_component_passes_solid():
    """连通域：实心块通过。"""
    shape = (64, 64, 32)
    arr = np.zeros(shape, dtype=np.uint8)
    arr[10:30, 10:30, 5:20] = 1
    flagged, _score, _detail = rule_connected_component(
        arr, {"enabled": True, "min_main_frac": 0.8, "max_components": 20}
    )
    assert not flagged


def test_holes_flags_hollow():
    """空洞：大空腔 flagged。"""
    shape = (32, 32, 32)
    arr = np.zeros(shape, dtype=np.uint8)
    arr[2:30, 2:30, 2:30] = 1
    arr[6:26, 6:26, 6:26] = 0  # 内部大空腔（封闭 -> fill_holes 可填充）
    flagged, _score, detail = rule_holes(arr, {"enabled": True, "max_hole_frac": 0.2})
    # 空腔占比 ~57% -> flagged
    assert flagged
    assert detail["worst_hole_frac"] > 0.2


def test_boundary_smoothness_flags_spiky():
    """边界平滑度：锯齿边界形状因子大。"""
    shape = (48, 48, 48)
    arr = np.zeros(shape, dtype=np.uint8)
    # 棋盘锯齿块
    for x in range(8, 40, 2):
        for y in range(8, 40, 2):
            for z in range(8, 40, 2):
                arr[x:x+1, y:y+1, z:z+1] = 1
    flagged, _score, detail = rule_boundary_smoothness(
        arr, {"enabled": True, "max_boundary_ratio": 1.2}
    )
    assert flagged
    assert detail["worst_sphericity"] > 1.2


def test_hu_distribution_skips_bone_classes():
    """HU 分布：骨类跳过软组织窗（§7.2 本院化主体）。"""
    shape = (32, 32, 8)
    arr = np.zeros(shape, dtype=np.uint8)
    arr[5:25, 5:25, 2:6] = 1
    hu = np.full(shape, 800, dtype=np.int16)  # 骨密度值
    # label-1 = vertebrae_L1（骨）-> 跳过
    flagged, _score, detail = rule_hu_distribution(
        arr, hu, {1: "vertebrae_L1"},
        {"enabled": True, "min_hu": -200, "max_hu": 400, "skip_bone_classes": True},
    )
    assert not flagged
    assert detail.get("skip_bone_classes")


def test_hu_distribution_flags_out_of_window():
    """HU 分布：软组织类 HU 出窗 flagged。"""
    shape = (32, 32, 8)
    arr = np.zeros(shape, dtype=np.uint8)
    arr[5:25, 5:25, 2:6] = 1
    hu = np.full(shape, 900, dtype=np.int16)  # 非软组织密度
    flagged, _score, detail = rule_hu_distribution(
        arr, hu, {1: "liver"},
        {"enabled": True, "min_hu": -200, "max_hu": 400, "skip_bone_classes": True},
    )
    assert flagged
    assert detail["worst_label"] == 1


def test_left_right_symmetry_flags_asymmetric(th):
    """左右对称性：成对类体积差 2.7 倍以上 flagged。"""
    shape = (64, 64, 16)
    arr = np.zeros(shape, dtype=np.uint8)
    arr[5:25, 5:25, 2:12] = 1      # kidney_left ~4000
    arr[5:10, 5:10, 2:5] = 2       # kidney_right ~75（比 ~53x）
    flagged, _score, detail = rule_left_right_symmetry(
        arr, {1: "kidney_left", 2: "kidney_right"},
        {"enabled": True, "max_log_ratio": 1.0, "pairs": [[["kidney_left", "kidney_right"]][0]]},
    )
    assert flagged
    assert detail["worst_pair"] == "kidney_left|kidney_right"


def test_left_right_symmetry_skips_missing_side():
    """左右对称性：一侧缺失不比对（TS 单侧输出合法）。"""
    shape = (32, 32, 8)
    arr = np.zeros(shape, dtype=np.uint8)
    arr[5:25, 5:25, 2:6] = 1  # 只有 left
    flagged, _score, detail = rule_left_right_symmetry(
        arr, {1: "kidney_left"},
        {"enabled": True, "max_log_ratio": 1.0, "pairs": [["kidney_left", "kidney_right"]]},
    )
    assert not flagged


def test_run_qc_empty_mask_one_vote_veto(th):
    """空 mask：一票否决（passed=false，恒开检查）。"""
    shape = (32, 32, 16)
    r = run_qc(np.zeros(shape, dtype=np.uint8), shape, "mv-e", "sid-e", thresholds=th)
    assert not r.passed
    assert "empty_mask" in r.flags
    assert r.overall_score == 0.0


def test_run_qc_full_mask_one_vote_veto(th):
    """全 mask：一票否决。"""
    shape = (32, 32, 16)
    r = run_qc(np.ones(shape, dtype=np.uint8), shape, "mv-f", "sid-f", thresholds=th)
    assert not r.passed
    assert "full_mask" in r.flags


def test_run_qc_volume_prior_hard_flag(th):
    """volume_prior 在 hard_flag_rules：一票否决（不进蒸馏）。"""
    shape = (128, 128, 64)
    arr = np.zeros(shape, dtype=np.uint8)
    arr[50:52, 50:52, 30:32] = 1
    r = run_qc(arr, shape, "mv-h", "sid-h", thresholds=th)
    assert not r.passed
    assert "volume_prior" in r.flags


def test_run_qc_solid_mask_passes(th):
    """实心合理 mask：passed=true（score 1.0 或接近）。"""
    shape = (64, 64, 32)
    arr = np.zeros(shape, dtype=np.uint8)
    arr[10:30, 10:30, 5:20] = 1
    hu = np.full(shape, 50, dtype=np.int16)  # 软组织 HU
    r = run_qc(
        arr, shape, "mv-ok", "sid-ok", thresholds=th, hu_values=hu,
        totalseg_classes={1: "liver"},
    )
    assert r.passed, f"flags={r.flags} score={r.overall_score}"


def test_sample_review_tasks_mixed_70_30(th):
    """混合采样：flagged 70% / clean 30%（proposal §6 第 5 阶段硬约束）。"""
    import random as _random

    rows = [
        {"mask_version_id": f"mv-{i}", "passed": i % 4 != 0, "overall_score": 0.1 * (i % 4)}
        for i in range(40)
    ]
    plan = sample_review_tasks(rows, thresholds=th, rng=_random.Random(1))
    assert isinstance(plan, SamplingPlan)
    batch = th["review_sampling"]["batch_size"]
    expected_flagged = round(batch * th["review_sampling"]["flagged_ratio"])
    assert len(plan.flagged_ids) == min(len([r for r in rows if not r["passed"]]), expected_flagged)
    assert len(plan.clean_ids) >= 1  # clean 至少 1 条（确认「没 flag 的真好」）
    assert not (set(plan.flagged_ids) & set(plan.clean_ids))


def test_sample_review_tasks_flagged_worst_first(th):
    """flagged 组：overall_score 升序（最差优先）。"""
    import random as _random

    flagged = [
        {"mask_version_id": f"mv-{i}", "passed": False, "overall_score": s}
        for i, s in enumerate([0.6, 0.1, 0.9, 0.3])
    ]
    clean = [{"mask_version_id": "mv-c", "passed": True, "overall_score": 1.0}]
    plan = sample_review_tasks(flagged + clean, thresholds=th, rng=_random.Random(0))
    scores = [r["overall_score"] for r in flagged]
    score_map = {r["mask_version_id"]: r["overall_score"] for r in flagged}
    got = [score_map[i] for i in plan.flagged_ids]
    assert got == sorted(got)
    assert scores != sorted(scores)


def test_sample_review_tasks_empty_pools(th):
    """空池/单边池：不 crash，尽量生成。"""
    plan = sample_review_tasks([], thresholds=th)
    assert plan.n_total == 0
    only_flagged = [{"mask_version_id": "mv-1", "passed": False, "overall_score": 0.2}]
    plan2 = sample_review_tasks(only_flagged, thresholds=th)
    assert plan2.flagged_ids == ["mv-1"]
    assert plan2.n_total == 1


# ---------------------------------------------------------------------------
# 在线校准（proposal §7.2：决策回流标签 -> 阈值本体重算，bodydonor qc-calibrate）
# ---------------------------------------------------------------------------

def test_calibrate_thresholds_discriminative_rule():
    """有判别力的规则：source 变 calibrated_on_N + discrimination 落注记。"""
    from bodydonor.qc import calibrate_thresholds, load_qc_thresholds

    th = load_qc_thresholds()
    before_frac = th["rules"]["connected_component"]["min_main_frac"]
    labels = [
        {"decision": "rejected", "flags": ["connected_component"]},
        {"decision": "rejected", "flags": ["connected_component"]},
        {"decision": "accepted", "flags": []},
    ]
    out = calibrate_thresholds(labels, th)
    cc = out["rules"]["connected_component"]
    assert cc["source"] == "calibrated_on_3"
    assert cc["calibrated_on"] == 3
    assert cc["discrimination"] == 1.0  # flag 组全拒(0)，clean 组全收(1) -> 差 1.0
    assert cc["min_main_frac"] == before_frac  # 数值不动（M3 标签量不足以重算）


def test_calibrate_thresholds_non_discriminative_rule():
    """无判别力的规则：source 注记不变（cold_start），discrimination 落注记。"""
    from bodydonor.qc import calibrate_thresholds, load_qc_thresholds

    th = load_qc_thresholds()
    labels = [
        {"decision": "rejected", "flags": ["volume_prior"]},   # clean 侧也被拒
        {"decision": "rejected", "flags": []},
        {"decision": "accepted", "flags": ["volume_prior"]},   # flagged 侧也有收
    ]
    out = calibrate_thresholds(labels, th)
    vp = out["rules"]["volume_prior"]
    # flag 组均值 (0+1)/2=0.5，clean 组均值 0.0 -> sep = -0.5 < 0.1 -> 无判别力
    assert vp["source"] == "cold_start"
    assert vp["discrimination"] == -0.5
    assert vp["calibrated_on"] == 3


def test_calibrate_thresholds_empty_labels():
    """空标签：不 crash，原样返回。"""
    from bodydonor.qc import calibrate_thresholds, load_qc_thresholds

    th = load_qc_thresholds()
    out = calibrate_thresholds([], th)
    assert out["rules"]["connected_component"]["source"] == "cold_start"


def test_calibrate_thresholds_updates_overall():
    """overall.calibrated_on/at 同步更新。"""
    from bodydonor.qc import calibrate_thresholds, load_qc_thresholds

    th = load_qc_thresholds()
    labels = [{"decision": "accepted", "flags": []} for _ in range(2)]
    out = calibrate_thresholds(labels, th)
    assert out["overall"]["calibrated_on"] == 2
    assert out["overall"]["calibrated_at"]
