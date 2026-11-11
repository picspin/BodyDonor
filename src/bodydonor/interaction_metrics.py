"""M6-a：交互轨迹结构化——provenance.interactionMetrics（过程级指标）。

M5-e 的 provenance {sessionId, baselineTeacherVersion, prompts[],
acceptedClasses[]} 只存档不计算；本模块把 M5-d 推理调度已经产出的中间变量
（每步草稿、放弃事件、提示数）累积成 per-class 过程指标，不改变 provenance
的合规语义（nnInteractive 产物仍不进蒸馏训练集，消费方只读本指标）：

    {"<class>": {
        "numPrompts": int,            # 该类推理输入的提示数（最后一次全量）
        "convergenceSteps": int|null, # 连续 2 步 perStepDeltaDice < τ 时的步数
        "perStepDeltaDice": [float],  # Dice(draft_{i-1}, draft_i)，草稿间变化
        "finalLocalDice": float|null, # 最终 mask vs 教师基线（结果级，带标记）
        "teacherReliability": "high"|"low",
        "abandonedAttempts": int,     # M5-b 放弃本类次数
    }}

诚实边界（~/M6-PLAN.md M6-a v2 修正）：
- perStepDeltaDice 是草稿间差异（draft_{i-1} vs draft_i），**绝不用教师
  mask 当伪真值算 ΔDice**——M4 实测教师在 prostate 0.5914、AMOS 5/15
  整类漏检、39/117 低频类不可靠；教师作为真值参照会系统性误导。第 1 步
  没有前驱草稿，不产生 delta（perStepDeltaDice 长度 = 推理步数 − 1）。
- finalLocalDice 保留 vs 教师的结果级语义，但必须带 teacherReliability
  标记：高风险类（prostate + 39/117 低频名单）为 "low"（M6-b 对这些类
  的 finalLocalDice 降权或不使用）。
- tracker 是采集期内存缓冲（进程内按 series 实例化）：重启后草稿历史
  丢失，指标退化为空 dict——不造假重建（任务 2：不从事后落盘产物重建
  草稿序列）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

# 收敛判定：连续 2 步草稿间 ΔDice < τ（M6-PLAN M6-a，明确可计算定义）
TAU_CONVERGENCE = 0.01
CONVERGENCE_CONSECUTIVE = 2

# 教师低频类阈值（与 holdout.MIN_PER_CLASS 同口径：<10 cases 伪真值不可靠）
MIN_PER_CLASS = 10

# 显式高风险类（M4 实测：教师 prostate Dice 0.5914，学生略高于教师——
# 教师伪真值在该类不可靠，与低频无关，显式点名）
HIGH_RISK_TEACHER_CLASSES = frozenset({"prostate"})

# 教师评估产物（M4 阶段 4：117 类 per_class_cases，低频名单来源）
_TEACHER_EVAL_PATH = Path(__file__).resolve().parents[2] / "eval" / "holdout_results.json"


def dice_binary(a: np.ndarray, b: np.ndarray) -> float:
    """二值前景 Dice（>0 为前景）。两个空集定义为 1.0（无差异）。"""
    a = np.asarray(a) > 0
    b = np.asarray(b) > 0
    denom = int(a.sum()) + int(b.sum())
    if denom == 0:
        return 1.0
    return 2.0 * int((a & b).sum()) / denom


def convergence_steps(
    per_step_delta_dice: list[float],
    tau: float = TAU_CONVERGENCE,
    consecutive: int = CONVERGENCE_CONSECUTIVE,
) -> int | None:
    """连续 consecutive 步 ΔDice < τ 时返回触发步数（1-based）；未收敛 None。

    例：[0.12, 0.008, 0.005] → 第 3 步触发（第 2、3 步连续 < 0.01）→ 3。
    [0.12, 0.008] → 第 2 步触发 → 2。[0.12, 0.02] → 未收敛 → None。
    """
    run = 0
    for i, d in enumerate(per_step_delta_dice, start=1):
        if d < tau:
            run += 1
            if run >= consecutive:
                return i
        else:
            run = 0
    return None


def teacher_reliability(
    cls: str,
    per_class_case_count: dict[str, int] | None = None,
) -> str:
    """教师伪真值在该类是否可靠："low"（高风险）/ "high"。

    - 显式高风险类（prostate，M4 实测 0.5914）→ "low"。
    - 低频类（出现 case 数 < MIN_PER_CLASS，M4 阶段 4 的
      low_frequency_classes() 口径）→ "low"。
    - 计数数据缺（per_class_case_count=None 或无该类）时只按显式名单判，
      不猜数字。
    """
    if cls in HIGH_RISK_TEACHER_CLASSES:
        return "low"
    if per_class_case_count and per_class_case_count.get(cls, 0) < MIN_PER_CLASS:
        return "low"
    return "high"


def load_teacher_eval(
    path: Path | None = None,
) -> dict[str, int] | None:
    """教师评估的 per_class_case_count（117 类）；文件缺失/损坏返回 None。"""
    p = path or _TEACHER_EVAL_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        cases = data["gate"]["per_class_cases"]
        if isinstance(cases, dict):
            return {str(k): int(v) for k, v in cases.items()}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return None


@dataclass
class InteractionTracker:
    """per-series 采集缓冲：草稿序列 + 放弃计数 + 提示数（内存，不落盘）。

    - record_draft：每步推理产出新草稿时调用；上一版草稿仍在内存时直接算
      该步 delta（不重跑推理、不从事后落盘产物重建——M6-PLAN v2 实现要点）。
    - record_abandoned：M5-b「越改越差 → 放弃本类」事件（前端放弃 → 后端
      端点 → 本计数；DraftStore.discard 同语义）。
    - build_metrics：submit 时产出 provenance.interactionMetrics 的该类条目。
    """

    drafts: dict[str, list[np.ndarray]] = field(default_factory=dict)
    abandoned: dict[str, int] = field(default_factory=dict)
    prompt_counts: dict[str, int] = field(default_factory=dict)

    def record_draft(
        self, cls: str, draft: np.ndarray, n_prompts: int
    ) -> float | None:
        """记录一步新草稿；有前驱草稿时返回该步 ΔDice（无前驱返回 None）。"""
        seq = self.drafts.setdefault(cls, [])
        delta: float | None = None
        if seq:
            delta = dice_binary(seq[-1], draft)
        seq.append(draft)
        self.prompt_counts[cls] = int(n_prompts)
        return delta

    def per_step_delta_dice(self, cls: str) -> list[float]:
        """草稿间 ΔDice 序列（长度 = 推理步数 − 1；第 1 步无前驱）。"""
        seq = self.drafts.get(cls, [])
        return [dice_binary(seq[i - 1], seq[i]) for i in range(1, len(seq))]

    def record_abandoned(self, cls: str) -> int:
        """放弃本类 +1（返回累计次数）。放弃后草稿序列清空（回 teacher）。"""
        self.abandoned[cls] = self.abandoned.get(cls, 0) + 1
        self.drafts.pop(cls, None)
        self.prompt_counts.pop(cls, None)
        return self.abandoned[cls]

    def build_metrics(
        self,
        cls: str,
        final_local_dice: float | None = None,
        per_class_case_count: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """该类的 interactionMetrics 条目（M6-PLAN M6-a JSON 结构）。

        final_local_dice：调用方（submit 端点）用教师基线算出的 vs 教师
        结果级 Dice；tracker 不持教师 mask，None 时如实写 null。
        """
        deltas = [round(d, 4) for d in self.per_step_delta_dice(cls)]
        return {
            "numPrompts": self.prompt_counts.get(cls, 0),
            "convergenceSteps": convergence_steps(deltas),
            "perStepDeltaDice": deltas,
            "finalLocalDice": (
                round(float(final_local_dice), 4) if final_local_dice is not None else None
            ),
            "teacherReliability": teacher_reliability(cls, per_class_case_count),
            "abandonedAttempts": self.abandoned.get(cls, 0),
        }


__all__ = [
    "CONVERGENCE_CONSECUTIVE",
    "HIGH_RISK_TEACHER_CLASSES",
    "MIN_PER_CLASS",
    "TAU_CONVERGENCE",
    "InteractionTracker",
    "convergence_steps",
    "dice_binary",
    "load_teacher_eval",
    "teacher_reliability",
]
