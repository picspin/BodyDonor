"""M6-f：DecisionTrace——决策审计痕迹（决策在哪发生就在哪记一条）。

M6 主题「结果信任 → 过程信任」：M4 hold-out 门禁是结果信任；M6-a/b/c/d 把
过程信号采集成规则、priority、三档 gating，本模块把「一次决策用到什么输入、
什么过程信号、触发什么规则、落到什么档位、人工如何处置」记录成可回溯痕迹
（DecisionTrace 实体，models.py）。痕迹的价值在 M7 校准：决策回流标签足够后
重算 gating 比例 / priority 权重时，trace.calibration 是「当时用什么参数、
样本是什么过程信号分布」的对照基准。

形态决策（任务书 1）：轻量实体而非 JSON 列。理由：一条决策一个阶段一行
（qc_scoring / gate_decision），M7 校准要按 stage / tier / 信号分布查询
（SQL where），JSON 列挂 QCReport 需要先 join 再解 JSON，查询性差；churn
可控——一个实体 + 一个迁移，不改既有实体任何列。

Append/update 语义（任务书 2）：决策痕迹 append-only——signals/rules/tier
等决策内容不回写；唯一的事后变更是 human_outcome 补填（决策回流端点：
status/decision 写进 ReviewTask 的同一事务里同步补填 trace.human_outcome，
updated_at 随 onupdate 走）。历史不改写：已补填的 human_outcome 不覆盖
（重放决策时幂等，见 update_human_outcome docstring）。

降级语义（任务书 5，不造假）：
- 无 interactionMetrics（纯自动 case）→ interaction_coverage=False，M6-b
  规则信号记 skipped 原文（"no interaction metrics"），不硬造信号。
- multi_teacher skipped / 无学生 mask → w3_available=False，w3 置零
  （priority 采样同语义）。
- human_outcome 恒 null 直至人工决策——null = "尚无人工决策"，空缺本身是
  信号（M4 报告决策回流 0/20，M6 大概率仍空，M7 决策回流后补填）。
"""

from __future__ import annotations

from typing import Any

from .models import DecisionTrace

# 校准参数快照（M7 对照基准）：gating 三比例 + priority 权重 + source 注记。
# 从 thresholds dict 提取，缺省与冷启动声明一致（qc_priority 同默认）。
_CAL_DEFAULTS = {
    "weights": {k: 1.0 for k in ("w1", "w2", "w3", "w4")},
    "ratios": {"forced_ratio": 0.20, "suggested_ratio": 0.60, "auto_ratio": 0.20},
}


def snapshot_calibration(thresholds: dict[str, Any]) -> dict[str, Any]:
    """thresholds -> 校准参数快照（gating 比例 + priority 权重 + source）。

    M7 校准重算的对照基准：trace 记录「决策发生时生效的比例/权重/来源」，
    重算前后可 diff（改参数 = config diff，trace 可见何时生效）。
    """
    sp = thresholds.get("sampling_priority")
    sp = sp if isinstance(sp, dict) else {}
    g = thresholds.get("gating")
    g = g if isinstance(g, dict) else {}
    w = sp.get("weights")
    w = w if isinstance(w, dict) else {}
    return {
        "priority_weights": {
            k: float(w.get(k, _CAL_DEFAULTS["weights"][k])) for k in ("w1", "w2", "w3", "w4")
        },
        "gating_ratios": {
            "forced_ratio": float(g.get("forced_ratio", _CAL_DEFAULTS["ratios"]["forced_ratio"])),
            "suggested_ratio": float(
                g.get("suggested_ratio", _CAL_DEFAULTS["ratios"]["suggested_ratio"])
            ),
            "auto_ratio": float(g.get("auto_ratio", _CAL_DEFAULTS["ratios"]["auto_ratio"])),
        },
        "source": {
            "sampling_priority": str(sp.get("source", "cold_start")),
            "gating": str(g.get("source", "cold_start")),
        },
    }


def _interaction_signals(
    interaction_metrics: dict[str, Any] | None,
    rule_scores: dict[str, Any],
    flags: list[str],
) -> dict[str, Any]:
    """M6-b 过程规则信号 + 交互覆盖（小样本本身是要记录的事实）。

    - coverage：该 case 是否有过 nnInteractive 交互（interactionMetrics 非空）。
      纯自动 case coverage=False——「无交互样本少」是 M7 校准要看到的分布事实，
      不硬造信号。
    - 规则信号：rule_scores 里两条规则的原文（skipped 时含原文 reason，
      flagged 时含逐类 flagged_classes）。
    """
    has_interaction = bool(interaction_metrics)
    out: dict[str, Any] = {"interaction_coverage": has_interaction}
    for rule in ("interaction_convergence", "interaction_abandonment"):
        rs = rule_scores.get(rule) if isinstance(rule_scores.get(rule), dict) else None
        if rs is not None:
            out[rule] = {
                "flagged": rule in flags,
                "detail": rs,
            }
    return out


def _priority_signals(
    ranked_row: Any | None,
    component_availability: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """M6-c priority 信号（gate_decision 阶段）：w1–w4 + availability + w3_available。

    ranked_row：PriorityRow（qc_priority）。无 priority 路径（旧 70/30 采样）
    返回 None——不硬造分量值。
    """
    if ranked_row is None:
        return None
    d = ranked_row.to_dict()
    signals: dict[str, Any] = {
        "priority": d["priority"],
        "components": d["components"],
        "availability": d["availability"],
        "gating_tier": d["gating_tier"],
    }
    if component_availability:
        w3a = component_availability.get("w3_available")
        signals["w3_available"] = bool(w3a)
        signals["note"] = component_availability.get("note", "")
    return signals


def record_qc_trace(
    *,
    case_id: str | None,
    series_id: str,
    mask_version_id: str,
    qc_report_id: str,
    flags: list[str],
    rule_scores: dict[str, Any],
    interaction_metrics: dict[str, Any] | None,
    student_source: str | None,
    thresholds: dict[str, Any],
) -> DecisionTrace:
    """构造 qc_scoring 阶段 trace（qc_series 产 QCReport 的同一事务里 add）。

    rules_triggered = flags（同一来源变量，验收 b 的逐项一致由构造保证）；
    gate_decision 尚未发生 → null（gate_decision 阶段另记一条）。
    """
    return DecisionTrace(
        case_id=case_id,
        series_id=series_id[:60],
        mask_version_id=mask_version_id,
        pipeline_stage="qc_scoring",
        input_refs={
            "mask_version_id": mask_version_id,
            "qc_report_id": qc_report_id,
            "student_source": student_source,  # ModelVersion id / "no case_id" / "unavailable: …"
        },
        process_signals={
            **_interaction_signals(interaction_metrics, rule_scores, flags),
        },
        rules_triggered=list(flags),
        gate_decision=None,
        human_outcome=None,  # 尚无人工决策，不造假
        calibration=snapshot_calibration(thresholds),
    )


def record_gate_trace(
    *,
    case_id: str | None,
    series_id: str,
    mask_version_id: str,
    qc_report_id: str,
    flags: list[str],
    rule_scores: dict[str, Any],
    interaction_metrics: dict[str, Any] | None,
    ranked_row: Any | None,
    component_availability: dict[str, Any] | None,
    tier: str,
    review_task_id: str | None,
    sample_group: str | None,
    thresholds: dict[str, Any],
) -> DecisionTrace:
    """构造 gate_decision 阶段 trace（generate_review_tasks 分档+建任务同一事务）。

    gate_decision：{tier, review_task_generated, review_task_id?, sample_group?}；
    tier=auto → 不生成 ReviewTask（M6-d 语义）→ review_task_generated=False。
    """
    return DecisionTrace(
        case_id=case_id,
        series_id=series_id[:60],
        mask_version_id=mask_version_id,
        pipeline_stage="gate_decision",
        input_refs={
            "mask_version_id": mask_version_id,
            "qc_report_id": qc_report_id,
        },
        process_signals={
            **_interaction_signals(interaction_metrics, rule_scores, flags),
            **(_priority_signals(ranked_row, component_availability) or {}),
        },
        rules_triggered=list(flags),
        gate_decision={
            "tier": tier,
            "review_task_generated": review_task_id is not None,
            **({"review_task_id": review_task_id} if review_task_id else {}),
            **({"sample_group": sample_group} if sample_group else {}),
        },
        human_outcome=None,
        calibration=snapshot_calibration(thresholds),
        review_task_id=review_task_id,
    )


def update_human_outcome(
    trace: DecisionTrace,
    decision: str,
    notes: str | None = None,
    decided_at: str | None = None,
) -> None:
    """决策回流补填 human_outcome（唯一的事后变更；其余列不回写）。

    - 幂等不覆盖：已补填的 trace 不改写（决策历史保留——重放回流端点时不
      覆盖首次决策；重决策产生新 ReviewTask 的场景由新 trace 承接）。
    - null 语义：human_outcome 为 null = 尚无人工决策（不造假）；补填后
      {"decision": accepted|corrected|rejected, "notes": …, "decided_at": …}。
    """
    if trace.human_outcome is not None:
        return
    from datetime import UTC, datetime

    ts = decided_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry: dict[str, Any] = {"decision": decision}
    if notes:
        entry["notes"] = notes
    entry["decided_at"] = ts
    trace.human_outcome = entry


__all__ = [
    "record_gate_trace",
    "record_qc_trace",
    "snapshot_calibration",
    "update_human_outcome",
]
