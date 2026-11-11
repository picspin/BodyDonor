#!/usr/bin/env python
"""M6-a 验收回放（任务 7b）：真实教师 mask + 脚本化提示序列构造草稿序列，
验证 interactionMetrics.perStepDeltaDice 与手工计算的草稿间 Dice 一致。

回放源（~/M6-PLAN.md M6-a 验收）：DB 无 provenance 行、真实 sidecar 只有 1 条
单 point 提示（无草稿序列）、浏览器测试记录未保留——无可回放的真实会话，
按任务 7b 用真实教师 mask + 脚本化提示序列构造回放。

回放设计：
- 真实教师 mask：amos_0004 aorta（ts_out/aorta.nii.gz，TS 标准值，14468 体素）。
- 脚本化提示序列模拟 M5-d 推理调度产出草稿：3 步草稿 = 教师 aorta 的
  逐级扩展（50% → 90% → 100% 体素），模拟医生逐步提示把漏检/欠分割区域
  补全的真实交互过程。
- 指标：InteractionTracker.record_draft 采集（与生产路径同一入口）；
  手工对照：numpy 逐对 Dice（draft_{i-1} vs draft_i，独立计算）。
- 断言：tracker.perStepDeltaDice == 手工 Dice 序列（逐位一致）；
  convergenceSteps/finalLocalDice/teacherReliability/abandonedAttempts
  符合 M6-PLAN 定义。
- 教师伪真值检查：perStepDeltaDice 绝不涉及教师 mask（教师只用于
  finalLocalDice 的结果级参照）。

用法：.venv/bin/python scripts/replay_m6a_metrics.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import nibabel as nib
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bodydonor.interaction_metrics import (  # noqa: E402
    InteractionTracker,
    dice_binary,
    load_teacher_eval,
    teacher_reliability,
)

TEACHER_PATH = Path.home() / "data/BodyDonor/downloads/amos22_subset/amos_0004/ts_out/aorta.nii.gz"


def main() -> int:
    print("[replay] 回放源：真实教师 mask + 脚本化提示序列（无真实会话记录可回放，任务 7b）")
    assert TEACHER_PATH.exists(), f"教师 mask 缺失：{TEACHER_PATH}"

    # ---- 真实教师 mask（amos_0004 aorta）----
    teacher_img = nib.load(str(TEACHER_PATH))
    teacher = np.asarray(teacher_img.dataobj) > 0
    n_teacher = int(teacher.sum())
    print(f"[replay] 教师 aorta 体素：{n_teacher}")

    # ---- 脚本化提示序列 → 3 步草稿（模拟 M5-d 推理调度逐步产出）----
    # 提示 1（正向点）→ 草稿 1：教师区域 50%（欠分割起点）
    # 提示 2（正向点）→ 草稿 2：教师区域 90%
    # 提示 3（正向点）→ 草稿 3：教师区域 100%（补全到教师水平）
    coords = np.argwhere(teacher)
    rng = np.random.default_rng(42)
    order = rng.permutation(len(coords))
    d1 = np.zeros_like(teacher)
    d1[tuple(coords[order[: len(order) // 2]].T)] = True
    d2 = np.zeros_like(teacher)
    d2[tuple(coords[order[: (len(order) * 9) // 10]].T)] = True
    d3 = teacher.copy()
    drafts = [("aorta", d1, 1), ("aorta", d2, 2), ("aorta", d3, 3)]
    print(
        f"[replay] 草稿体素：draft1={int(d1.sum())} draft2={int(d2.sum())} draft3={int(d3.sum())}"
    )

    # ---- 手工对照：草稿间 Dice（独立 numpy 计算）----
    manual = [
        round(dice_binary(d1, d2), 4),
        round(dice_binary(d2, d3), 4),
    ]
    print(f"[replay] 手工草稿间 Dice：{manual}")

    # ---- InteractionTracker 采集（与生产路径同一入口）----
    tracker = InteractionTracker()
    step_deltas = []
    for cls, arr, n_prompts in drafts:
        delta = tracker.record_draft(cls, arr, n_prompts)
        if delta is not None:
            step_deltas.append(round(delta, 4))
    print(f"[replay] tracker perStepDeltaDice：{step_deltas}")

    # ---- 一致性断言 ----
    assert step_deltas == manual, f"不一致：tracker={step_deltas} 手工={manual}"
    print("[replay] 一致性：perStepDeltaDice == 手工草稿间 Dice（逐位一致）")

    # ---- finalLocalDice vs 教师（结果级，草稿 3 = 教师 → 1.0）----
    final_local = round(dice_binary(d3, teacher), 4)
    counts = load_teacher_eval()
    metrics = tracker.build_metrics(
        "aorta", final_local_dice=final_local, per_class_case_count=counts
    )
    print(f"[replay] interactionMetrics：{json.dumps(metrics, ensure_ascii=False)}")

    # ---- M6-PLAN 定义断言 ----
    assert metrics["numPrompts"] == 3
    assert metrics["perStepDeltaDice"] == manual
    assert metrics["finalLocalDice"] == final_local
    # aorta 30 cases（非低频）→ high；草稿逐级扩展无 <τ 收敛 → None
    assert metrics["teacherReliability"] == "high"
    assert metrics["convergenceSteps"] is None
    assert metrics["abandonedAttempts"] == 0
    assert counts is not None and counts["aorta"] == 30
    assert teacher_reliability("aorta", counts) == "high"

    # ---- 教师伪真值检查：perStepDeltaDice 与教师无关 ----
    # 反证：若用教师当伪真值算 ΔDice，草稿 1 vs 教师 = 2*0.5/(1.5) ≈ 0.667、
    # 草稿 2 vs 教师 = 2*0.9/1.9 ≈ 0.947——序列不同，证明 tracker 用的是草稿间。
    vs_teacher = [round(dice_binary(d1, teacher), 4), round(dice_binary(d2, teacher), 4)]
    assert vs_teacher != step_deltas, "tracker 序列意外等于 vs 教师——伪真值混入"
    print(f"[replay] 伪真值检查：vs 教师 {vs_teacher} != tracker {step_deltas}（草稿间语义成立）")

    # ---- 放弃路径回放：放弃 → abandonedAttempts +1，序列清空 ----
    tracker.record_abandoned("aorta")
    m2 = tracker.build_metrics("aorta")
    assert m2["abandonedAttempts"] == 1 and m2["perStepDeltaDice"] == []
    print("[replay] 放弃路径：abandonedAttempts=1，草稿序列已清空")

    print("[replay] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
