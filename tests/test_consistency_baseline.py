"""一致性基线固化守卫测试（M7-CONSISTENCY 轮 A 项）。

- 基线文件 eval/consistency_baseline.json 存在且结构齐全；
- 数值对照源产物 eval/student_teacher_consistency.json（gitignored 运行产物，
  缺失时 skip 不 fail）——基线与源产物数值漂移即红；
- regression_flag 段 5 例与 M4-REPORT §3.4 实测逐字一致。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO / "eval" / "consistency_baseline.json"
SOURCE_PATH = REPO / "eval" / "student_teacher_consistency.json"

EXPECTED_PER_CLASS = {
    "adrenal_gland_left": 0.7552,
    "adrenal_gland_right": 0.7847,
    "aorta": 0.9327,
    "duodenum": 0.713,
    "esophagus": 0.8619,
    "gallbladder": 0.7684,
    "inferior_vena_cava": 0.8706,
    "kidney_left": 0.9285,
    "kidney_right": 0.938,
    "liver": 0.9525,
    "pancreas": 0.8215,
    "prostate": 0.743,
    "spleen": 0.9433,
    "stomach": 0.889,
    "urinary_bladder": 0.8791,
}

EXPECTED_BELOW_080 = [
    "adrenal_gland_left",
    "adrenal_gland_right",
    "duodenum",
    "gallbladder",
    "prostate",
]

EXPECTED_FLAGGED = {
    "s0209": 0.1181,
    "amos_0005": 0.6592,
    "s0338": 0.7081,
    "amos_0006": 0.7567,
    "amos_0016": 0.7862,
}


def _load_baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def test_baseline_file_exists() -> None:
    assert BASELINE_PATH.exists(), "eval/consistency_baseline.json 未建"


def test_baseline_overall() -> None:
    b = _load_baseline()
    assert b["baseline"]["mean"] == 0.856
    assert b["baseline"]["p10"] == 0.6986
    assert b["baseline"]["n_pairs"] == 419
    # 对照统计量（median/p25/min 供对照，存在即可）
    assert b["baseline"]["median"] == 0.922
    assert b["baseline"]["p25"] == 0.8521
    assert b["baseline"]["min"] == 0.0


def test_baseline_per_class_15_classes() -> None:
    b = _load_baseline()
    pc = b["per_class"]
    assert len(pc) == 15, f"per_class 应 15 类，实 {len(pc)}"
    for cls, mean in EXPECTED_PER_CLASS.items():
        assert cls in pc, f"缺类 {cls}"
        assert pc[cls]["mean"] == mean, f"{cls} mean 漂移：{pc[cls]['mean']}"
        assert pc[cls]["cases"] > 0


def test_baseline_below_080() -> None:
    b = _load_baseline()
    assert b["below_080_classes"] == EXPECTED_BELOW_080


def test_baseline_regression_flag() -> None:
    b = _load_baseline()
    rf = b["regression_flag"]
    assert rf["aggregation"] == "mean"
    assert rf["threshold"] == 0.80
    assert rf["flagged_cases"] == EXPECTED_FLAGGED


def test_baseline_raw_pairs_note() -> None:
    """原始 419 对未保留的结论必须写在元数据里，不编造原始数值。"""
    b = _load_baseline()
    assert b["meta"]["raw_pairs_retained"] is False
    assert "419 对" in b["meta"]["raw_pairs_note"]
    assert "充分统计量" in b["meta"]["raw_pairs_note"]


@pytest.mark.skipif(
    not SOURCE_PATH.exists(),
    reason="eval/student_teacher_consistency.json 源产物缺失（gitignored 运行产物）",
)
def test_baseline_matches_source_product() -> None:
    """基线数值对照源产物——漂移即红。"""
    b = _load_baseline()
    s = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    assert b["baseline"]["n_pairs"] == s["overall"]["n_pairs"]
    for key in ("mean", "median", "p10", "p25", "min"):
        assert b["baseline"][key] == s["overall"][key], f"overall.{key} 漂移"
    assert b["per_class"] == s["per_class"]
    assert b["below_080_classes"] == s["below_080_classes"]
