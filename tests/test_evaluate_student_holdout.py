"""eval/evaluate_student_holdout.py 单测（M4 收尾第二轮）：门禁口径切分。

评估脚本经 importlib 按路径加载（与 test_evaluate_holdout.py 同模式）；不跑
GPU 推理——口径切分 + evaluate_gate 串联是纯函数小输入，不 mock 判据本体。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

EVAL_SCRIPT = (
    Path(__file__).resolve().parents[1] / "eval" / "evaluate_student_holdout.py"
)


@pytest.fixture(scope="module")
def esh():
    spec = importlib.util.spec_from_file_location("evaluate_student_holdout", EVAL_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_in_scope_gate_inputs_filters_to_student_classes(esh):
    """口径切分：dice_sum/case_count/whole_missed 全部过滤到学生类范围。"""
    dice_sum = {"liver": 28.3, "prostate": 11.0, "heart": 0.0, "colon": 0.0}
    case_count = {"liver": 30, "prostate": 18, "heart": 15, "colon": 15}
    whole_missed = ["heart", "colon"]
    ds, cc, wm = esh.in_scope_gate_inputs(
        dice_sum, case_count, whole_missed, {"liver", "prostate"}
    )
    assert ds == {"liver": 28.3, "prostate": 11.0}
    assert cc == {"liver": 30, "prostate": 18}
    assert wm == []


def test_in_scope_gate_inputs_drops_out_of_scope_misses(esh):
    """范围外整类漏检不进 in-scope 门禁（学生设计范围外，不是模型漏检）。"""
    dice_sum = {"liver": 30.0, "spleen": 28.0,
                "heart": 0.0, "spinal_cord": 0.0, "colon": 0.0}
    case_count = {"liver": 30, "spleen": 29, "heart": 15, "spinal_cord": 15, "colon": 15}
    whole_missed = ["heart", "spinal_cord", "colon"]
    ds, cc, wm = esh.in_scope_gate_inputs(
        dice_sum, case_count, whole_missed, {"liver", "spleen"}
    )
    assert set(ds) == {"liver", "spleen"}
    assert set(cc) == {"liver", "spleen"}
    assert wm == []


def test_scope_gate_semantics_in_scope_pass_naive_fail(esh):
    """端到端口径语义：15 类内全过 + 范围外全 0 → in-scope pass / naive fail。

    真实 evaluate_gate + 真实 in_scope_gate_inputs 串联（不 mock 判据）：
    范围外教师类学生天然整类 dice=0，naive 口径结构性 FAIL，in-scope 口径 PASS。
    """
    from bodydonor.holdout import evaluate_gate

    student_classes = {"liver", "spleen", "kidney_left", "kidney_right"}
    in_dice = {
        "liver": 0.95 * 30, "spleen": 0.93 * 29,
        "kidney_left": 0.92 * 28, "kidney_right": 0.93 * 29,
    }
    in_cc = {"liver": 30, "spleen": 29, "kidney_left": 28, "kidney_right": 29}
    out_classes = ("heart", "lung_upper_lobe_left", "colon")
    dice_sum = {**in_dice, **{c: 0.0 for c in out_classes}}
    case_count = {**in_cc, **{c: 15 for c in out_classes}}
    whole_missed = list(out_classes)

    ds, cc, wm = esh.in_scope_gate_inputs(
        dice_sum, case_count, whole_missed, student_classes
    )
    in_scope = evaluate_gate(ds, cc, n_cases=30, whole_class_missed=wm)
    naive = evaluate_gate(
        dice_sum, case_count, n_cases=30, whole_class_missed=whole_missed
    )
    assert in_scope.passed is True
    assert naive.passed is False
    assert naive.mean_dice < 0.75 <= in_scope.mean_dice
    assert naive.failures  # naive 侧有具体失败条目


def test_holdout_results_json_scope_numbers(esh):
    """results JSON 锁定实测口径数字（--skip-inference 重跑产物，非 mock）。"""
    results = (
        Path(__file__).resolve().parents[1] / "eval" / "student_holdout_results.json"
    )
    s = json.loads(results.read_text(encoding="utf-8"))["summary"]
    assert s["gate_scope"] == "student-15-classes"
    assert s["gate_passed"] is True
    assert s["mean_dice"] == 0.8014
    assert len(s["per_class_dice"]) == 15
    assert s["per_class_dice"]["prostate"] == 0.6099
    assert s["whole_class_missed"] == []
    naive = s["scope_naive"]
    assert naive["scope"] == "all-117-classes"
    assert naive["passed"] is False
    assert naive["mean_dice"] == 0.1027
    assert naive["n_classes"] == 117
