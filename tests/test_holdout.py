"""hold-out 门禁单测（M3，proposal §8）：泄漏检查 + 三源构成 + Dice 门禁。"""

from __future__ import annotations

import numpy as np
import pytest

from bodydonor.holdout import (
    MIN_CASES,
    GateResult,
    HoldoutEntry,
    LeakError,
    check_holdout_composition,
    check_leak,
    check_per_class_coverage,
    class_case_counts,
    dice_per_class,
    evaluate_gate,
    load_holdout,
    low_frequency_classes,
    persist_gate_result,
    write_holdout,
)


def test_write_load_roundtrip(tmp_path):
    entries = [
        HoldoutEntry("s0338", "ts_public"),
        HoldoutEntry("amos_0004", "amos"),
        HoldoutEntry("nifti-ab12cd34", "local_correction"),
    ]
    p = write_holdout(entries, path=tmp_path / "holdout.txt")
    loaded = load_holdout(p)
    assert [e.case_id for e in loaded] == ["s0338", "amos_0004", "nifti-ab12cd34"]
    assert [e.source for e in loaded] == ["ts_public", "amos", "local_correction"]


def test_leak_check_passes_disjoint():
    overlap = check_leak(["s0001", "s0002"], ["s0338", "amos_0004"])
    assert overlap == []


def test_leak_check_aborts_on_overlap():
    """泄漏：训练集与 hold-out 交集非空 -> LeakError（非空即中止，proposal §8）。"""
    with pytest.raises(LeakError) as exc:
        check_leak(["s0338", "s0002"], ["s0338", "amos_0004"])
    assert "泄漏" in str(exc.value)


def test_leak_check_loads_holdout_file(tmp_path):
    write_holdout([HoldoutEntry("amos_0004", "amos")], path=tmp_path / "holdout.txt")
    with pytest.raises(LeakError):
        check_leak(["amos_0004"], path=tmp_path / "holdout.txt")


def test_composition_three_sources():
    entries = (
        [HoldoutEntry(f"s{i:04d}", "ts_public") for i in range(15)]
        + [HoldoutEntry(f"amos_{i:04d}", "amos") for i in range(15)]
        + [HoldoutEntry(f"nifti-{i:08x}", "local_correction") for i in range(5)]
    )
    assert check_holdout_composition(entries) == []


def test_composition_too_few_cases():
    entries = [HoldoutEntry(f"s{i:04d}", "ts_public") for i in range(10)]
    problems = check_holdout_composition(entries)
    assert any("规模不足" in p for p in problems)
    assert any("独立来源" in p for p in problems)


def test_composition_no_independent_source():
    """全部同源（TS 公开集）——防同源偏乐观必须 fail（proposal §8）。"""
    entries = [HoldoutEntry(f"s{i:04d}", "ts_public") for i in range(35)]
    problems = check_holdout_composition(entries)
    assert any("独立来源" in p for p in problems)


def test_dice_per_class_real_voxels():
    """逐类 Dice：真实体素计算（不 mock）。手算对照。"""
    shape = (16, 16, 8)
    gt = np.zeros(shape, dtype=np.uint8)
    pred = np.zeros(shape, dtype=np.uint8)
    gt[2:8, 2:8, 2:6] = 1   # 288 体素
    pred[2:8, 2:8, 2:4] = 1  # 144 体素（完全在 gt 内）
    # Dice = 2*144 / (144+288) = 0.6667
    mean, per_class, per_n = dice_per_class(pred, gt, {1: "liver"})
    assert abs(per_class["liver"] - 2 * 144 / 432) < 1e-9
    assert abs(mean - 2 * 144 / 432) < 1e-9
    assert per_n["liver"] == 1


def test_dice_per_class_whole_miss_detected():
    """整类漏检：gt 有 label-2 但 pred 全漏 -> label-2 dice=0（§8 空漏检）。"""
    shape = (16, 16, 8)
    gt = np.zeros(shape, dtype=np.uint8)
    pred = np.zeros(shape, dtype=np.uint8)
    gt[2:8, 2:8, 2:6] = 1
    gt[9:14, 9:14, 2:6] = 2
    mean, per_class, _n = dice_per_class(pred, gt, {1: "liver", 2: "spleen"})
    assert per_class["spleen"] == 0.0
    assert mean < 0.5


def test_evaluate_gate_passes():
    # 单 case 4 类 dice 全 1.0 -> 但 n_cases=1 低于 MIN_CASES
    result = evaluate_gate(
        {"liver": 1.0, "spleen": 1.0}, {"liver": 1, "spleen": 1}, n_cases=1
    )
    assert isinstance(result, GateResult)
    assert not result.passed  # 规模不足
    assert any("规模" in f for f in result.failures)


def test_evaluate_gate_per_class_floor():
    """每类 Dice 下限：liver 0.4 < 0.5 且出现 ≥10 cases -> fail（§8 成对判据）。"""
    per_class_sum = {"liver": 4.0, "spleen": 9.5}
    per_class_n = {"liver": 10, "spleen": 10}
    result = evaluate_gate(per_class_sum, per_class_n, n_cases=30)
    assert not result.passed
    assert any("liver" in f and "下限" in f for f in result.failures)
    assert not any("spleen" in f and "下限" in f for f in result.failures)


def test_evaluate_gate_whole_class_missed():
    """整类漏检：某类存在但学生整类漏掉 -> fail（整体均值会掩盖整类失败）。"""
    per_class_sum = {"liver": 9.0, "spleen": 0.0}
    per_class_n = {"liver": 10, "spleen": 10}
    result = evaluate_gate(
        per_class_sum, per_class_n, n_cases=30, whole_class_missed=["spleen"]
    )
    assert not result.passed
    assert any("整类漏检" in f for f in result.failures)
    # 即便 mean 过线也 fail（成对判据）
    assert result.mean_dice == 0.45


def test_per_class_coverage_minimum():
    """每类至少 10 cases（低于此 Dice 均值方差大到没有门禁意义）。"""
    gt_labels = {f"case{i}": {1} for i in range(9)}  # 9 例 + case10 = liver 10 例（OK）
    gt_labels["case10"] = {1, 2}
    problems = check_per_class_coverage(gt_labels, {1: "liver", 2: "spleen"})
    assert not any("liver" in p for p in problems)  # liver 恰好 10 例（达标）
    assert any("spleen" in p for p in problems)  # spleen 1 例 < 10


def test_min_cases_constant():
    """规模下限 30（proposal §8）。"""
    assert MIN_CASES == 30


# ---------------------------------------------------------------------------
# 门禁结果落库（ModelVersion.gate_metrics 来源，proposal §8）
# ---------------------------------------------------------------------------

@pytest.fixture
def settings_db(tmp_path):
    """Settings + init_db 的独立 SQLite（落库测试不碰真实 bodydonor.db）。"""
    from bodydonor.config import Settings
    from bodydonor.db import init_db

    url = f"sqlite:///{tmp_path / 'gate.db'}"
    init_db(url)
    s = Settings()
    s.database.url = url
    return s


def _seed_model_version(settings, model_version_id="mv-student-r1"):
    from sqlalchemy.orm import Session

    from bodydonor import db
    from bodydonor.models import ModelVersion

    engine = db._engine(settings.database.url)
    try:
        with engine.connect() as c:
            s = Session(c)
            s.add(ModelVersion(model_version_id=model_version_id, name="student-r1"))
            s.commit()
            s.close()
    finally:
        engine.dispose()


def test_persist_gate_result_passed(settings_db):
    """门禁通过：gate_status=passed + gate_metrics 成对判据落库。"""
    from sqlalchemy.orm import Session

    from bodydonor import db
    from bodydonor.models import ModelVersion

    _seed_model_version(settings_db)
    gate = evaluate_gate(
        {"liver": 30.0, "spleen": 29.0},
        {"liver": 30, "spleen": 30},
        n_cases=30,
    )
    out = persist_gate_result(settings_db, "mv-student-r1", gate)
    assert out["gate_status"] == "passed"

    engine = db._engine(settings_db.database.url)
    try:
        with engine.connect() as c:
            s = Session(c)
            mv = s.get(ModelVersion, "mv-student-r1")
            assert mv.gate_status == "passed"
            assert mv.gate_metrics["passed"] is True
            assert mv.gate_metrics["n_cases"] == 30
            assert mv.gate_holdout_file == "eval/holdout.txt"
            s.close()
    finally:
        engine.dispose()


def test_persist_gate_result_failed(settings_db):
    """门禁失败（整类漏检）：gate_status=failed + failures 落库。"""
    from sqlalchemy.orm import Session

    from bodydonor import db
    from bodydonor.models import ModelVersion

    _seed_model_version(settings_db)
    gate = evaluate_gate(
        {"liver": 30.0, "spleen": 0.0},
        {"liver": 30, "spleen": 30},
        n_cases=30,
        whole_class_missed=["spleen"],
    )
    assert not gate.passed
    persist_gate_result(settings_db, "mv-student-r1", gate)

    engine = db._engine(settings_db.database.url)
    try:
        with engine.connect() as c:
            s = Session(c)
            mv = s.get(ModelVersion, "mv-student-r1")
            assert mv.gate_status == "failed"
            assert any("整类漏检" in f for f in mv.gate_metrics["failures"])
            s.close()
    finally:
        engine.dispose()


def test_persist_gate_result_unknown_model_version(settings_db):
    """unknown model version：ModelVersionError（不静默建行）。"""
    from bodydonor.holdout import ModelVersionError

    gate = evaluate_gate({"liver": 30.0}, {"liver": 30}, n_cases=30)
    with pytest.raises(ModelVersionError, match="unknown model version"):
        persist_gate_result(settings_db, "mv-nonexistent", gate)


# ---------------------------------------------------------------------------
# M4 阶段 4：低频类显式清单（关 M3-REPORT §3.2 偏差）
# ---------------------------------------------------------------------------

def test_low_frequency_classes_lists_name_and_count():
    """低频类清单：类名 + case 数，按 (case 数, 类名) 排序。"""
    counts = {"brain": 1, "liver": 30, "skull": 5, "prostate": 18}
    low = low_frequency_classes(counts)
    assert low == [
        {"class": "brain", "cases": 1},
        {"class": "skull", "cases": 5},
    ]


def test_class_case_counts_accepts_name_sets():
    """评估脚本按类名对齐后可直接传类名集合（str labels 不走编码反查）。"""
    gt = {"c1": {"liver", "prostate"}, "c2": {"liver"}}
    assert class_case_counts(gt) == {"liver": 2, "prostate": 1}


def test_check_per_class_coverage_name_sets():
    """check_per_class_coverage 接受类名集合（评估路径接线用）。"""
    gt = {f"case{i}": {"liver"} for i in range(10)}
    gt["case10"] = {"liver", "spleen"}
    problems = check_per_class_coverage(gt)
    assert not any("liver" in p for p in problems)
    assert any("spleen" in p for p in problems)


def test_evaluate_gate_reports_low_frequency_without_failing():
    """默认语义：低频类只显式告警 + 列清单，不改 pass/fail（M4 阶段 4 决定）。

    冻结 30 例 hold-out 中 39/117 类天然 <10 cases——硬 fail 会使门禁在结构上
    不可通过（hold-out 规模/解剖覆盖局限，非模型质量信号）。
    """
    dice_sum = {"liver": 27.0, "brain": 0.98, "prostate": 10.6452}
    counts = {"liver": 30, "brain": 1, "prostate": 18}
    gate = evaluate_gate(dice_sum, counts, n_cases=30)
    assert gate.passed
    assert gate.low_frequency_classes == [{"class": "brain", "cases": 1}]
    assert "低频类 1 个" in gate.coverage_warning
    d = gate.to_dict()
    assert d["low_frequency_classes"] == [{"class": "brain", "cases": 1}]
    assert d["coverage_warning"] == gate.coverage_warning


def test_evaluate_gate_strict_coverage_fails():
    """fail_on_low_coverage=True 时覆盖不足即门禁失败（供 hold-out 扩容后收紧）。"""
    dice_sum = {"liver": 27.0, "brain": 0.98}
    counts = {"liver": 30, "brain": 1}
    gate = evaluate_gate(dice_sum, counts, n_cases=30, fail_on_low_coverage=True)
    assert not gate.passed
    assert any("覆盖不足" in f for f in gate.failures)
    assert any("brain" in f for f in gate.failures)


def test_gate_result_locks_prostate_entry():
    """prostate 逐类明细锁定：M3 最弱类 0.5914 / 18 cases 必须在门禁结果里。"""
    dice_sum = {"liver": 27.0, "prostate": 0.5914 * 18}
    counts = {"liver": 30, "prostate": 18}
    gate = evaluate_gate(dice_sum, counts, n_cases=30)
    assert gate.per_class_dice["prostate"] == pytest.approx(0.5914)
    assert gate.per_class_cases["prostate"] == 18
    assert gate.to_dict()["per_class_dice"]["prostate"] == 0.5914
