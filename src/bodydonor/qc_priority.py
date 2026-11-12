"""M6-c：过程风险驱动抽检采样——priority 排序替代 flagged 70% / clean 30%。

M4 已证明冷启动阶段 QC flag 区分度低（419 对一致性回归：均值聚合 flag
5/30，其余结果规则 flag 更少）；flagged/clean 二分抽检在 flag 少时退化成
随机抽检。本模块把抽检优先级改为过程风险连续分数，按 priority 降序取前
batch_size 名建 ReviewTask：

    priority = w1*convergence + w2*abandonment + w3*teacher_student_disagreement
               + w4*low_frequency_class_presence

四个分量（各归一化到 [0,1]，定义见各分量函数 docstring）：

- w1 convergence（源自 provenance.interactionMetrics）：普通类取
  max(min(numPrompts/N_max,1), min(convergenceSteps/K_max,1))；高风险类
  （teacherReliability=low，prostate + 39 低频名单）只用 numPrompts 一项
  （convergenceSteps 对高风险类无独立校准依据，M6-b 同口径）。case 级取
  各类最大值。N_max/K_max 读 M6-b config 的 interaction_convergence 阈值
  （n_max_prompts / k_max_steps），不硬编码。
- w2 abandonment（源自 interactionMetrics）：任一类 abandonedAttempts>=1
  则 1，否则 0——与 M6-b interaction_abandonment 判据一致（高 flag：放弃
  一次即记信号）。不用"总放弃次数/min_abandonments"变体：min_abandonments=1
  时两式等价，取与规则同形的判据。
- w3 teacher_student_disagreement（源自 M6-e 学生 mask vs 教师 mask）：
  QCReport.rule_scores 里 multi_teacher_consistency 的逐类 Dice
  （detail.per_class_dice，按类名对齐的真实体素比较，不是均值）。
  聚合方式 = **最差类**（1 - min 逐类 Dice）。理由：case 级 mean 会稀释
  单类漏检——实测 s0495（教师整类漏检 prostate，逐类 min 0.0）mean 0.8771、
  s0556（urinary_bladder 整类漏检）mean 0.8705，mean 聚合下这两个验收
  要求进前 20% 的 case 排不进（30 例 mean 降序第 10+/第 12+）；最差类
  聚合把整类漏检直接顶到 1.0。冷启动阶段抽检目的是「找出最坏的 case 交
  人审」而不是「估整体一致率」，宁可过审不可漏审。无学生 mask（规则
  skipped）→ w3 不可用，置零并记录。
- w4 low_frequency_class_presence（39/117 低频名单，复用
  interaction_metrics.load_teacher_eval 口径：eval/holdout_results.json
  117 类 per_class_cases，<10 cases 为低频；prostate 显式高风险但计数 18
  不在名单内）。定义：case 的参评类中低频类占比。参评类集 = w3 的
  per_class_dice 类集（教师/学生真实可比的类，最能代表该 case 实际涉及
  的器官）；w3 不可用时回退 interactionMetrics 的类键并集；两者都无则
  w4 置零并记录。不做封顶：占比本身已在 [0,1]（低频类数 <= 参评类数），直接用。

权重：初期等权 w1=w2=w3=w4=1，进 config/qc_thresholds.yaml 新增
sampling_priority 节（阈值集中单文件原则不变，改权重 = config diff），
带 source: cold_start 注记 + note 显式声明「权重是冷启动起点，未经校准」。

降级语义（任务书 3，不造假）：
- interactionMetrics 缺失/空 → w1=0、w2=0（无交互即无过程信号）。
- multi_teacher skipped / 无学生 mask → w3=0 并记录 w3_available=False。
- 三个来源都缺 → priority=0（排最后，不是排最前——无信号不冒充高风险）。
- DecisionTrace 实体是 M6-f：本轮不造实体，可用性记录在返回值
  （ranked 行级 availability / component_availability 汇总）与报告里。

铁律：
- priority 只决定抽检顺序，不改 QC pass/fail（不碰 hard_flag_rules、
  不动 eval/holdout.txt 与 M4 门禁结果）。
- 无 mock 数字冒充真实数据：真实 mask 回归用真实产物；合成数据标注
  synthetic。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .interaction_metrics import load_teacher_eval
from .qc import SamplingPlan

# QCReport.rule_scores 里的规则名（qc.py 同名常量；字符串复制避免循环导入）
RULE_MULTI_TEACHER = "multi_teacher_consistency"
RULE_INTERACTION_CONVERGENCE = "interaction_convergence"

# 低频名单显式高风险类（interaction_metrics.HIGH_RISK_TEACHER_CLASSES 同口径）
_HIGH_RISK_TEACHER_CLASSES = frozenset({"prostate"})


@dataclass
class PriorityRow:
    """一个 case 的 priority 采样行（含各分量值与可用性，DecisionTrace 前置）。"""

    mask_version_id: str
    qc_report_id: str = ""
    priority: float = 0.0
    components: dict[str, float | None] = field(default_factory=dict)  # w1..w4 值（None=不可用）
    availability: dict[str, bool] = field(default_factory=dict)  # 分量数据源是否可用
    passed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "mask_version_id": self.mask_version_id,
            "qc_report_id": self.qc_report_id,
            "priority": round(self.priority, 4),
            "components": {
                k: (None if v is None else round(v, 4)) for k, v in self.components.items()
            },
            "availability": dict(self.availability),
            "passed": self.passed,
        }


def _load_priority_cfg(thresholds: dict[str, Any] | None) -> dict[str, Any]:
    """sampling_priority 配置（缺省等权 1——与冷启动声明一致）。"""
    from .qc import load_qc_thresholds

    th = thresholds if thresholds is not None else load_qc_thresholds()
    cfg = th.get("sampling_priority")
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "weights": {k: float(cfg.get("weights", {}).get(k, 1.0)) for k in ("w1", "w2", "w3", "w4")},
    }


def _interaction_cfg(thresholds: dict[str, Any] | None) -> dict[str, Any]:
    """interaction_convergence 的 config（N_max/K_max 来源，不硬编码）。"""
    from .qc import load_qc_thresholds

    th = thresholds if thresholds is not None else load_qc_thresholds()
    return th.get("rules", {}).get(RULE_INTERACTION_CONVERGENCE, {})


def component_convergence(
    interaction_metrics: dict[str, Any] | None,
    interaction_cfg: dict[str, Any],
) -> float | None:
    """w1 convergence 分量（[0,1]；无 interactionMetrics 返回 None）。

    每类：普通类 max(min(numPrompts/N_max,1), min(convergenceSteps/K_max,1))；
    高风险类（teacherReliability=low）只用 numPrompts 一项（M6-b 同口径：
    convergenceSteps 对高风险类无独立校准依据）。case 级 = 各类最大值。
    N_max=n_max_prompts、K_max=k_max_steps 读 M6-b config。
    """
    if not interaction_metrics:
        return None
    n_max = int(interaction_cfg.get("n_max_prompts", 15))
    k_max = int(interaction_cfg.get("k_max_steps", 6))
    worst = 0.0
    for entry in interaction_metrics.values():
        if not isinstance(entry, dict):
            continue
        num_prompts = entry.get("numPrompts")
        is_int = isinstance(num_prompts, int) and not isinstance(num_prompts, bool)
        num_prompts = num_prompts if is_int else 0
        if entry.get("teacherReliability") == "low":
            per_class = min(num_prompts / n_max, 1.0) if n_max > 0 else 1.0
        else:
            steps = entry.get("convergenceSteps")
            steps = steps if isinstance(steps, int) and not isinstance(steps, bool) else 0
            # n_max/k_max <=0（config 异常）→ 该项饱和到 1.0（不抛 ZeroDivisionError）
            prompts_term = min(num_prompts / n_max, 1.0) if n_max > 0 else 1.0
            steps_term = min(steps / k_max, 1.0) if k_max > 0 else 1.0
            per_class = max(prompts_term, steps_term)
        worst = max(worst, per_class)
    return worst


def component_abandonment(interaction_metrics: dict[str, Any] | None) -> float | None:
    """w2 abandonment 分量（[0,1]；无 interactionMetrics 返回 None）。

    任一类 abandonedAttempts >= 1 则 1，否则 0——与 M6-b
    interaction_abandonment 判据（min_abandonments=1）同形：放弃一次即记
    信号。放弃计数只认非负 int（bool 显式排除，_int_field 同口径）。
    """
    if not interaction_metrics:
        return None
    for entry in interaction_metrics.values():
        if not isinstance(entry, dict):
            continue
        n = entry.get("abandonedAttempts")
        if isinstance(n, int) and not isinstance(n, bool) and n >= 1:
            return 1.0
    return 0.0


def component_disagreement(
    rule_scores: dict[str, Any] | None,
) -> tuple[float | None, dict[str, float]]:
    """w3 teacher_student_disagreement 分量（[0,1]；规则 skipped 返回 None）。

    聚合 = 最差类：1 - min(逐类 Dice)。数据 = QCReport.rule_scores 里
    multi_teacher_consistency 的 detail.per_class_dice（按类名对齐的真实
    体素比较）。mean 聚合会稀释单类整类漏检（s0495 prostate min 0.0 而
    mean 0.8771），最差类聚合把整类漏检顶到 1.0（理由见模块 docstring）。
    返回 (分量值, 逐类 dice dict)——逐类 dice 供 w4 参评类集使用。
    """
    rs = (rule_scores or {}).get(RULE_MULTI_TEACHER) if isinstance(rule_scores, dict) else None
    if not isinstance(rs, dict) or not isinstance(rs.get("per_class_dice"), dict):
        return None, {}
    numeric = (int, float)
    per_class = {k: float(v) for k, v in rs["per_class_dice"].items() if isinstance(v, numeric)}
    if not per_class:
        return None, {}
    return 1.0 - min(per_class.values()), per_class


def component_low_freq_presence(
    per_class_dice: dict[str, float],
    interaction_metrics: dict[str, Any] | None,
    per_class_case_count: dict[str, int] | None,
) -> float | None:
    """w4 low_frequency_class_presence 分量（[0,1]；无参评类集返回 None）。

    定义：case 的参评类中低频类占比（低频类数 / 参评类数，已在 [0,1]）。
    低频名单 = eval/holdout_results.json 117 类 per_class_cases 中 <10 cases
    的类 + 显式高风险类 prostate（interaction_metrics.load_teacher_eval
    口径）。参评类集优先 w3 的逐类比较类集（真实可比、最代表该 case 实际
    涉及的器官），w3 不可用时回退 interactionMetrics 的类键并集（nnInteractive
    交互过的类）；两者都无 → None（无信号不猜）。
    """
    classes = set(per_class_dice) or (
        {k for k, v in (interaction_metrics or {}).items() if isinstance(v, dict)}
    )
    if not classes:
        return None
    if per_class_case_count is None:
        return None  # 名单缺（eval 产物缺失）→ 分量不可用，不猜
    low_freq = {
        c for c in classes if c in _HIGH_RISK_TEACHER_CLASSES or per_class_case_count.get(c, 0) < 10
    }
    return len(low_freq) / len(classes)


def sample_review_tasks_by_priority(
    qc_rows: list[dict[str, Any]],
    thresholds: dict[str, Any] | None = None,
    per_class_case_count: dict[str, int] | None = None,
) -> SamplingPlan:
    """过程风险驱动采样（M6-c）：按 priority 降序取前 batch_size 名。

    qc_rows：每行 {mask_version_id, passed, overall_score, qc_report_id?,
    rule_scores?, interaction_metrics?}——rule_scores 是该 case 最新 QCReport
    的 rule_scores（w3/w4 数据源），interaction_metrics 是同 case 最新
    MaskVersion 的 provenance.interactionMetrics（w1/w2 数据源）。两者都缺
    的行按降级语义处理（w1=w2=0 / w3 不可用），不 raise。

    per_class_case_count：117 类低频名单（缺省 load_teacher_eval() 读
    eval/holdout_results.json；显式传 None 不查文件）。名单缺失时 w4 记
    不可用（不猜）。

    返回 SamplingPlan（兼容形状：flagged_ids=参审 ids 按优先级降序、
    clean_ids 恒空——优先级模型里没有「clean 组」概念，未入选 case 本轮
    不审；n_total=入选数；flagged_pool/clean_pool=参审/未入选池大小）。
    新增字段：ranked（PriorityRow 列表，priority 降序全量）、
    component_availability（各分量的数据源可用计数）。

    降级显式记录：w3 不可用的行 availability["w3"]=False + plan 含
    w3_available 汇总；w1/w2 缺失同记录（任务书 3：返回值里记录各分量
    可用情况，DecisionTrace 实体是 M6-f，本轮不造）。
    """
    from .qc import load_qc_thresholds

    th = thresholds if thresholds is not None else load_qc_thresholds()
    pcfg = _load_priority_cfg(th)
    weights = pcfg["weights"]
    icfg = _interaction_cfg(th)
    if per_class_case_count is None:
        per_class_case_count = load_teacher_eval()

    batch = int(th.get("review_sampling", {}).get("batch_size", 10))
    rows: list[PriorityRow] = []
    for r in qc_rows:
        im = r.get("interaction_metrics")
        im = im if isinstance(im, dict) and im else None
        w1 = component_convergence(im, icfg)
        w2 = component_abandonment(im)
        w3, per_class = component_disagreement(r.get("rule_scores"))
        w4 = component_low_freq_presence(per_class, im, per_class_case_count)
        avail = {
            "w1": w1 is not None,
            "w2": w2 is not None,
            "w3": w3 is not None,
            "w4": w4 is not None,
        }
        priority = (
            weights["w1"] * (w1 or 0.0)
            + weights["w2"] * (w2 or 0.0)
            + weights["w3"] * (w3 or 0.0)
            + weights["w4"] * (w4 or 0.0)
        )
        rows.append(
            PriorityRow(
                mask_version_id=str(r.get("mask_version_id", "")),
                qc_report_id=str(r.get("qc_report_id", "")),
                priority=priority,
                components={"w1": w1, "w2": w2, "w3": w3, "w4": w4},
                availability=avail,
                passed=bool(r.get("passed", True)),
            )
        )

    rows.sort(key=lambda x: (-x.priority, x.mask_version_id))
    selected = rows[:batch] if batch > 0 else rows
    n_avail = {k: sum(1 for row in rows if row.availability[k]) for k in ("w1", "w2", "w3", "w4")}
    plan = SamplingPlan(
        flagged_ids=[row.mask_version_id for row in selected],
        clean_ids=[],
        n_total=len(selected),
        flagged_pool=len(rows),
        clean_pool=0,
    )
    plan.ranked = rows  # type: ignore[attr-defined] — M6-c 新增：全量排名（DecisionTrace 前置）
    plan.component_availability = {  # type: ignore[attr-defined]
        **{k: n for k, n in n_avail.items()},
        "w3_available": n_avail["w3"] > 0,
        "note": (
            "w3 不可用（multi_teacher skipped / 无学生 mask）的行 w3 置零，只用 w1/w2/w4 排序"
            if n_avail["w3"] == 0
            else ""
        ),
    }
    return plan
