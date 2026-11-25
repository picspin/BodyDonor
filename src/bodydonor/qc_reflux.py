"""抽检决策回流（M3，proposal §7 在线校准）：决策标签 -> 阈值配置更新。

- 每条抽检决策（接受/修正/拒绝）作为在线校准标签记录进 qc_thresholds.yaml：
  calibrated_on 计数 + calibrated_at 日期 + decisions 计数器（按决策类型）。
- **阈值本体**（min_frac/max_frac 等数值）由 `bodydonor qc-calibrate` 重算——
  决策回流只是累计标签，等标签足够（默认 ≥ 20 例）重算才有意义。
- rules.*.calibrated_on/at 是**反馈计数**（回流累计数/最近反馈日期），与本条
  决策数同步更新；开发 provenance（development_provenance 节）**绝不碰**——
  source 注记也不改写（P2：正常回流曾把 holdout_30_dev/30 覆盖成 1）。

M3 单教师：多教师一致性规则的回流标签 M4 起才记录（学生 mask 出现后）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import yaml

from .config import Settings
from .qc import QC_THRESHOLDS_PATH, load_qc_thresholds

VALID_DECISIONS = ("accepted", "corrected", "rejected")


def record_review_decision(
    settings: Settings, decision: str, path: Any = None
) -> dict[str, Any]:
    """一条抽检决策回流：累计进 qc_thresholds.yaml（calibrated_on/at + decisions）。

    - decisions.{decision} 计数 +1
    - calibrated_on = 总决策数（N cases）
    - calibrated_at = 今天（ISO 日期）
    - rules.*.calibrated_on/at 同步更新（反馈计数语义）
    - development_provenance 节不碰（开发依据与反馈计数解耦，P2）
    """
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision 必须是 {VALID_DECISIONS}，收到 {decision!r}")
    p = path or QC_THRESHOLDS_PATH
    th = load_qc_thresholds(p)
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    overall = th.setdefault("overall", {})
    decisions = overall.setdefault("decisions", {})
    decisions[decision] = int(decisions.get(decision, 0)) + 1
    total = sum(int(v) for v in decisions.values())
    overall["calibrated_on"] = total
    overall["calibrated_at"] = today

    for rule_cfg in th.get("rules", {}).values():
        if isinstance(rule_cfg, dict):
            rule_cfg["calibrated_on"] = total
            rule_cfg["calibrated_at"] = today

    # sampling_priority 同为反馈统计（note：M6-f 决策回流后重算权重，
    # calibrated_on 更新）；gating 分档比例未经决策回流校准，不碰
    sp = th.get("sampling_priority")
    if isinstance(sp, dict):
        sp["calibrated_on"] = total
        sp["calibrated_at"] = today

    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(th, f, allow_unicode=True, sort_keys=False)
    return {
        "decision": decision, "calibrated_on": total, "calibrated_at": today,
        "decisions": dict(decisions),
    }


def record_review_batch(
    settings: Settings, decisions: list[str], path: Any = None
) -> dict[str, Any]:
    """多条决策批量回流（抽检台一轮的决策列表；逐条累计，只写盘一次）。"""
    p = path or QC_THRESHOLDS_PATH
    last = {}
    for d in decisions:
        last = record_review_decision(settings, d, path=p)
    return last


__all__ = ["VALID_DECISIONS", "record_review_batch", "record_review_decision"]
