"""M6-b：过程级 QC 规则——interaction_convergence / interaction_abandonment。

消费 MaskVersion.provenance.interactionMetrics（M6-a 采集，结构见
interaction_metrics.py 模块 docstring），接入 qc_pipeline 的规则框架
（rule_scores/flags/QCReport）。两条规则都是"结果之外的过程信号"：

- interaction_convergence：某类 numPrompts > n_max_prompts 或
  convergenceSteps > k_max_steps 即 flag（即使最终 Dice 不错）。语义：
  该类的自动结果不可信，需要人工介入。
- interaction_abandonment：某类 abandonedAttempts >= min_abandonments
  即 flag（高 flag）。语义：连交互式修正都无法收敛。

两层逻辑（任务书 3，规则各管一个维度）：
- 普通类（teacherReliability=="high"）：interaction_convergence 看
  numPrompts/n_max、convergenceSteps/k_max；finalLocalDice 只作辅助信息
  进 detail，不作判据。
- 高风险类（teacherReliability=="low"，prostate + 39 低频名单——M6-a
  build_metrics 已按 teacher_reliability() 打标记）：不看 finalLocalDice
  （教师伪真值不可靠）、不看 convergenceSteps（该维度对高风险类无独立
  校准依据）；interaction_convergence 对该类只看 numPrompts/n_max。
- abandonedAttempts 维度归 interaction_abandonment（全类同判据，
  abandonedAttempts >= min 即 flag）——同一放弃事件不双规则双 flag。

诚实边界（铁律）：
- 只读 provenance.interactionMetrics 标量字段，不读 nnInteractive 像素输出；
  nnInteractive 修正产物不进蒸馏训练集（不碰 distill_data.py 许可门禁）。
- 过程信号不替代结果门禁：不动 eval/holdout.txt 与 M4 门禁。
- 无 interactionMetrics（纯自动 case / tracker 空）→ 显式 skipped，不造假。

阈值初值 cold_start 未校准（任务书 5）：进 config/qc_thresholds.yaml，
source: cold_start、calibrated_on: 0；改阈值 = config diff。
"""

from __future__ import annotations

from typing import Any

# 规则名（config/qc_thresholds.yaml rules.* 同名）
RULE_INTERACTION_CONVERGENCE = "interaction_convergence"
RULE_INTERACTION_ABANDONMENT = "interaction_abandonment"

# provenance.interactionMetrics 条目的字段（M6-a JSON 结构，只读这些）
METRIC_FIELDS = (
    "numPrompts",
    "convergenceSteps",
    "perStepDeltaDice",
    "finalLocalDice",
    "teacherReliability",
    "abandonedAttempts",
)


def _int_field(entry: dict[str, Any], key: str) -> int:
    """条目标量字段取 int（缺省/类型异常记 0——不猜数字，0 = 无该信号）。"""
    val = entry.get(key)
    if isinstance(val, bool):  # bool 是 int 子类，显式排除
        return 0
    if isinstance(val, int):
        return val
    return 0


def is_high_risk(
    entry: dict[str, Any],
    high_risk_classes: frozenset[str] | None = None,
) -> bool:
    """该类是否高风险（M6-b 两层逻辑的分层依据）。

    provenance 条目里 teacherReliability 由 M6-a build_metrics 按
    teacher_reliability() 打标（prostate + 低频名单），直接复用——规则不重算，
    两端同一标记同一语义。条目缺该字段（旧数据）时回退显式名单；无名单且无
    标记 → 按普通类处理（不猜）。
    """
    reliability = entry.get("teacherReliability")
    if reliability == "low":
        return True
    if reliability == "high":
        return False
    # 旧数据无标记：显式名单兜底（prostate），否则按普通类
    return bool(high_risk_classes and entry.get("__class__") in high_risk_classes)


def rule_interaction_convergence(
    class_metrics: dict[str, dict[str, Any]],
    cfg: dict[str, Any],
    high_risk_classes: frozenset[str] | None = None,
) -> tuple[bool, float, dict[str, Any]]:
    """interaction_convergence 规则本体（M6-b 任务 1/3）。

    class_metrics：{类名: interactionMetrics 条目}（provenance.interactionMetrics）。
    返回 (flagged, score, detail)——qc.py rule_outputs 同形状。

    两层逻辑（任务书 3，规则各管一个维度，同一事件不双 flag）：
    - 普通类：numPrompts > n_max_prompts 或 convergenceSteps > k_max_steps。
    - 高风险类：只看 numPrompts > n_max_prompts——convergenceSteps 对该类
      无独立校准依据（教师伪真值不可靠），不看；abandonedAttempts 维度归
      interaction_abandonment 规则（全类同判据），本规则不重复查。
    - finalLocalDice 不进判据，只进 detail（辅助信息）。
    """
    n_max = int(cfg.get("n_max_prompts", 15))
    k_max = int(cfg.get("k_max_steps", 6))

    flagged_classes: list[str] = []
    worst_prompts = 0
    worst_steps = 0
    for cls, entry in class_metrics.items():
        if not isinstance(entry, dict):
            continue
        num_prompts = _int_field(entry, "numPrompts")
        conv_steps = entry.get("convergenceSteps")
        is_int = isinstance(conv_steps, int) and not isinstance(conv_steps, bool)
        steps = conv_steps if is_int else 0
        high_risk = is_high_risk(entry, high_risk_classes)
        hit = num_prompts > n_max or (not high_risk and steps > k_max)
        if hit:
            flagged_classes.append(cls)
        worst_prompts = max(worst_prompts, num_prompts)
        worst_steps = max(worst_steps, steps)

    flagged = bool(flagged_classes)
    detail: dict[str, Any] = {
        "n_max_prompts": n_max,
        "k_max_steps": k_max,
        "flagged_classes": flagged_classes,
        "worst_num_prompts": worst_prompts,
        "worst_convergence_steps": worst_steps,
    }
    return flagged, 0.0 if flagged else 1.0, detail


def rule_interaction_abandonment(
    class_metrics: dict[str, dict[str, Any]],
    cfg: dict[str, Any],
    high_risk_classes: frozenset[str] | None = None,
) -> tuple[bool, float, dict[str, Any]]:
    """interaction_abandonment 规则本体（M6-b 任务 2）。

    任一类 abandonedAttempts >= min_abandonments 即 flag（高 flag；默认 1）。
    两层逻辑对该规则同判据（放弃事件本身就是"无法收敛"的过程信号，普通/
    高风险类一致）；high_risk_classes 参数保留以对齐两规则同签名（is_high_risk
    用于 detail 的分层标注）。
    """
    min_abandon = int(cfg.get("min_abandonments", 1))
    flagged_classes: list[str] = []
    worst_abandoned = 0
    for cls, entry in class_metrics.items():
        if not isinstance(entry, dict):
            continue
        attempts = _int_field(entry, "abandonedAttempts")
        if attempts >= min_abandon:
            flagged_classes.append(cls)
        worst_abandoned = max(worst_abandoned, attempts)

    flagged = bool(flagged_classes)
    detail: dict[str, Any] = {
        "min_abandonments": min_abandon,
        "flagged_classes": flagged_classes,
        "worst_abandoned_attempts": worst_abandoned,
        "high_risk_flagged": [
            c for c in flagged_classes if is_high_risk(class_metrics[c], high_risk_classes)
        ],
    }
    return flagged, 0.0 if flagged else 1.0, detail


__all__ = [
    "METRIC_FIELDS",
    "RULE_INTERACTION_ABANDONMENT",
    "RULE_INTERACTION_CONVERGENCE",
    "is_high_risk",
    "rule_interaction_abandonment",
    "rule_interaction_convergence",
]
