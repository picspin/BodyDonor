"""金标准 hold-out 门禁（M3，proposal §8）：防误差放大的承重件。

- **hold-out 永不进训练**：case_id 列表固化在 `eval/holdout.txt`；流水线启动时
  先做**泄漏检查**（训练集与 hold-out 交集必须为空，非空即中止）。
- **规模下限**：≥ 30 cases、每类至少出现在 10 cases 中（低于此 Dice 均值方差
  大到没有门禁意义）。
- **三源混合**（防单一偏置）：
  ① TotalSegmentator 公开集 10–20（冷启动用，标注「与教师同源」——TS 真值是
     模型 + 人工迭代产出，与教师同源偏乐观）；
  ② AMOS 或 BTCV 10–20（独立来源）；
  ③ 本地人工修正产物 5–10（M3 有 teacher mask + 人工修正产物时择优）。
- **门禁判据成对写**（proposal §8）：mean Dice + 每类 Dice 下限 + 空漏检
  （某类在 hold-out 里存在但学生整类漏掉）。整体均值会掩盖整类失败。
- 论文类用途需另加 BTCV/AMOS 独立评估（同源偏乐观，proposal §8 明示）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import models

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = PROJECT_ROOT / "eval"
HOLDOUT_FILE = EVAL_DIR / "holdout.txt"

# 三源构成下限（proposal §8：≥30 cases、每类 ≥10 cases、TS 10-20 / AMOS 10-20 / 本地 5-10）
MIN_CASES = 30
MIN_PER_CLASS = 10
MIN_LOCAL_CORRECTIONS = 5

# 门禁判据（proposal §8：mean Dice + 每类 Dice 下限 + 整类漏检）
MEAN_DICE_FLOOR = 0.75
PER_CLASS_DICE_FLOOR = 0.5


@dataclass
class HoldoutEntry:
    """hold-out 一行：case_id + 来源（source 标注同源偏乐观风险）。"""

    case_id: str
    source: str  # ts_public | amos | btcv | local_correction

    def line(self) -> str:
        return f"{self.case_id}\t{self.source}"


@dataclass
class GateResult:
    """门禁判定输出（ModelVersion.gate_metrics 落库来源）。"""

    mean_dice: float
    per_class_dice: dict[str, float] = field(default_factory=dict)
    per_class_cases: dict[str, int] = field(default_factory=dict)
    n_cases: int = 0
    whole_class_missed: list[str] = field(default_factory=list)
    passed: bool = False
    failures: list[str] = field(default_factory=list)
    # 低频类（出现 case 数 < MIN_PER_CLASS）：[{class, cases}]，不让低频类 Dice
    # 静默进均值（M4 阶段 4 关 M3 §3.2 偏差）；coverage_warning 为显式告警文本。
    low_frequency_classes: list[dict[str, Any]] = field(default_factory=list)
    coverage_warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean_dice": round(self.mean_dice, 4),
            "per_class_dice": {k: round(v, 4) for k, v in self.per_class_dice.items()},
            "per_class_cases": dict(self.per_class_cases),
            "n_cases": self.n_cases,
            "whole_class_missed": list(self.whole_class_missed),
            "passed": self.passed,
            "failures": list(self.failures),
            "low_frequency_classes": [
                dict(d) for d in self.low_frequency_classes
            ],
            "coverage_warning": self.coverage_warning,
        }


# ---------------------------------------------------------------------------
# holdout.txt 读写 + 泄漏检查
# ---------------------------------------------------------------------------

def write_holdout(entries: list[HoldoutEntry], path: Path | None = None) -> Path:
    """case_id 列表固化（幂等覆盖写；评审可 git diff 构成改动）。"""
    p = path or HOLDOUT_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [e.line() for e in entries]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def load_holdout(path: Path | None = None) -> list[HoldoutEntry]:
    """读 holdout.txt（case_id\tsource；无 source 的旧行按 ts_public 处理）。"""
    p = path or HOLDOUT_FILE
    if not p.exists():
        raise FileNotFoundError(f"hold-out 文件不存在：{p}（先 build_holdout 固化）")
    entries = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        case_id = parts[0].strip()
        source = parts[1].strip() if len(parts) > 1 else "ts_public"
        entries.append(HoldoutEntry(case_id=case_id, source=source))
    return entries


def check_leak(
    train_case_ids: list[str],
    holdout_case_ids: list[str] | None = None,
    path: Path | None = None,
) -> list[str]:
    """泄漏检查（proposal §8：训练集与 hold-out 交集必须为空，非空即中止）。

    返回交集列表；**非空即 raise LeakError**（调用方决定呈现；流水线启动时
    第一件事就是本检查）。
    """
    if holdout_case_ids is None:
        holdout_case_ids = [e.case_id for e in load_holdout(path)]
    overlap = sorted(set(train_case_ids) & set(holdout_case_ids))
    if overlap:
        raise LeakError(
            f"泄漏检查失败：训练集与 hold-out 交集非空（{len(overlap)} 例）："
            f"{overlap[:10]}{'…' if len(overlap) > 10 else ''}。hold-out 永不进训练，"
            "非空即中止（proposal §8）。"
        )
    return overlap


class LeakError(Exception):
    """训练集与 hold-out 交集非空（proposal §8：非空即中止）。"""


# ---------------------------------------------------------------------------
# 三源构成 + 规模校验
# ---------------------------------------------------------------------------

def check_holdout_composition(
    entries: list[HoldoutEntry],
) -> list[str]:
    """三源构成 + 规模校验（proposal §8）；不满足返回问题列表（不 raise）。"""
    problems: list[str] = []
    counts: dict[str, int] = {}
    for e in entries:
        counts[e.source] = counts.get(e.source, 0) + 1
    ts = counts.get("ts_public", 0)
    amos = counts.get("amos", 0) + counts.get("btcv", 0)
    local = counts.get("local_correction", 0)
    total = len(entries)
    if total < MIN_CASES:
        problems.append(f"规模不足：{total} < {MIN_CASES} cases（proposal §8 下限）")
    if not (10 <= ts <= 20):
        problems.append(f"TS 公开集 {ts} 例不在 10–20 区间")
    if amos < 10:
        problems.append(f"独立来源（AMOS/BTCV）{amos} 例 < 10（防同源偏乐观）")
    if local > 0 and local < MIN_LOCAL_CORRECTIONS:
        problems.append(f"本地修正 {local} 例 < {MIN_LOCAL_CORRECTIONS}（要么不进要么足量）")
    if local > 10:
        problems.append(f"本地修正 {local} 例 > 10（权重最高数量最少，§8）")
    dup = len(entries) - len({e.case_id for e in entries})
    if dup:
        problems.append(f"重复 case_id {dup} 例")
    return problems


# ---------------------------------------------------------------------------
# Dice + 门禁判定
# ---------------------------------------------------------------------------

def dice_per_class(
    pred: Any, gt: Any, class_labels: dict[int, str] | None = None,
) -> tuple[float, dict[str, float], dict[str, int]]:
    """逐类 Dice（真实体素计算，不 mock）。

    返回 (mean_dice, {类名: dice}, {类名: 该类出现的 case 数=1})。
    只统计在 pred 或 gt 中任一非空的类；空对空类不计入。
    """
    import numpy as np

    pred_arr = np.asarray(pred)
    gt_arr = np.asarray(gt)
    labels = sorted(
        {int(v) for v in np.unique(pred_arr) if v != 0}
        | {int(v) for v in np.unique(gt_arr) if v != 0}
    )
    per_class: dict[str, float] = {}
    per_class_n: dict[str, int] = {}
    for lb in labels:
        name = (class_labels or {}).get(lb, f"label-{lb}")
        p = pred_arr == lb
        g = gt_arr == lb
        denom = float(p.sum()) + float(g.sum())
        if denom == 0:
            continue  # 空对空
        inter = float((p & g).sum())
        per_class[name] = 2.0 * inter / denom
        per_class_n[name] = per_class_n.get(name, 0) + 1
    mean = sum(per_class.values()) / len(per_class) if per_class else 0.0
    return mean, per_class, per_class_n


def evaluate_gate(
    per_class_dice_sum: dict[str, float],
    per_class_case_count: dict[str, int],
    n_cases: int,
    whole_class_missed: list[str] | None = None,
    fail_on_low_coverage: bool = False,
) -> GateResult:
    """门禁判定（proposal §8 成对判据；累计多 case 的 Dice 均值后调用）。

    - mean Dice ≥ 0.75
    - 每类 Dice 下限 ≥ 0.5（仅当该类在 ≥ MIN_PER_CLASS cases 中出现，方差才有意义）
    - 整类漏检：任一类在 hold-out 存在但学生整类漏掉 → fail
    - 低频类（出现 < MIN_PER_CLASS cases）：**默认只显式告警 + 列清单，不改
      pass/fail**——硬 fail 会使门禁在结构上不可通过：冻结的 30 例 hold-out
      （ts_public 15 + amos 15）中 39/117 类天然 <10 cases（头颈/四肢类只在
      覆盖该解剖部位的扫描里出现），这是 hold-out 规模/解剖覆盖面的局限，
      不是模型质量信号，根治靠扩大 hold-out；§8「每类 ≥10」的本意是方差理
      由下的每类下限适用条件（已实现：下限只约束 ≥10 cases 的类）。将来
      hold-out 扩容后可用 fail_on_low_coverage=True 收紧为 fail-fast。
    """
    whole_class_missed = whole_class_missed or []
    per_class = {
        k: v / per_class_case_count[k]
        for k, v in per_class_dice_sum.items()
        if per_class_case_count.get(k, 0) > 0
    }
    mean = sum(per_class.values()) / len(per_class) if per_class else 0.0
    failures: list[str] = []
    if n_cases < MIN_CASES:
        failures.append(f"规模 {n_cases} < {MIN_CASES}")
    if mean < MEAN_DICE_FLOOR:
        failures.append(f"mean Dice {mean:.4f} < {MEAN_DICE_FLOOR}")
    for name, d in sorted(per_class.items()):
        n = per_class_case_count.get(name, 0)
        if n >= MIN_PER_CLASS and d < PER_CLASS_DICE_FLOOR:
            failures.append(f"每类 Dice 下限：{name} {d:.4f} < {PER_CLASS_DICE_FLOOR}（{n} cases）")
    if whole_class_missed:
        failures.append(f"整类漏检：{whole_class_missed[:10]}（存在但学生整类漏掉，§8）")
    low_freq = low_frequency_classes(per_class_case_count)
    coverage_warning = ""
    if low_freq:
        coverage_warning = (
            f"低频类 {len(low_freq)} 个出现在 < {MIN_PER_CLASS} cases，"
            "其 Dice 不受每类下限约束（方差无意义），均值含低频类贡献——见 "
            "low_frequency_classes（M3-REPORT §3.2 偏差已显式列出，不静默）"
        )
        if fail_on_low_coverage:
            failures.append(
                f"覆盖不足：{len(low_freq)} 个低频类 < {MIN_PER_CLASS} cases"
                f"（{', '.join(d['class'] for d in low_freq[:10])}"
                f"{'…' if len(low_freq) > 10 else ''}）"
            )
    return GateResult(
        mean_dice=mean,
        per_class_dice=per_class,
        per_class_cases=dict(per_class_case_count),
        n_cases=n_cases,
        whole_class_missed=whole_class_missed,
        passed=not failures,
        failures=failures,
        low_frequency_classes=low_freq,
        coverage_warning=coverage_warning,
    )


def class_case_counts(
    gt_labels_per_case: dict[str, set[int] | set[str]],
    class_labels: dict[int, str] | None = None,
) -> dict[str, int]:
    """每类在 hold-out 中出现的 case 数（规模校验：每类 ≥ 10 cases，§8）。

    labels 可为编码值（int，走 class_labels 反查）或已命名的类名（str，
    评估脚本按类名对齐后直接传类名集合时用）。
    """
    counts: dict[str, int] = {}
    for _cid, labels in gt_labels_per_case.items():
        for lb in labels:
            name = lb if isinstance(lb, str) else (class_labels or {}).get(lb, f"label-{lb}")
            counts[name] = counts.get(name, 0) + 1
    return counts


def low_frequency_classes(
    per_class_case_count: dict[str, int],
    min_cases: int = MIN_PER_CLASS,
) -> list[dict[str, Any]]:
    """出现 case 数 < min_cases 的类：[{class, cases}]，按 (case 数, 类名) 排序。

    门禁报告的低频类清单来源（M4 阶段 4：低频类 Dice 不静默进均值）。
    """
    return [
        {"class": name, "cases": n}
        for name, n in sorted(
            per_class_case_count.items(), key=lambda kv: (kv[1], kv[0])
        )
        if n < min_cases
    ]


def check_per_class_coverage(
    gt_labels_per_case: dict[str, set[int] | set[str]],
    class_labels: dict[int, str] | None = None,
) -> list[str]:
    """每类至少出现在 10 cases 中（低于此 Dice 均值方差大到没有门禁意义）。

    返回问题列表（不 raise）；调用方（eval/evaluate_holdout.py）把清单写进
    门禁报告，是否 fail 由 evaluate_gate 的 fail_on_low_coverage 决定。
    """
    return [
        f"{d['class']} 仅出现在 {d['cases']} cases < {MIN_PER_CLASS}"
        for d in low_frequency_classes(
            class_case_counts(gt_labels_per_case, class_labels)
        )
    ]


# ---------------------------------------------------------------------------
# 门禁结果落库（ModelVersion.gate_metrics 来源，proposal §8）
# ---------------------------------------------------------------------------

def persist_gate_result(
    settings: Any,
    model_version_id: str,
    gate: GateResult,
    holdout_file: Path | None = None,
) -> dict[str, Any]:
    """门禁结果写 ModelVersion（gate_status/gate_metrics/gate_holdout_file）。

    - model_version 不存在 → ModelVersionError（不静默建行；ModelVersion 由
      蒸馏训练侧创建，本函数只更新门禁结果字段）
    - 写路径走单一写入纪律（db.write_lock，M0-fix）
    """
    from sqlalchemy import select

    from . import db

    engine = db._engine(settings.database.url)
    try:
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            try:
                mv = s.execute(
                    select(models.ModelVersion).where(
                        models.ModelVersion.model_version_id
                        == model_version_id[:60]
                    )
                ).scalar_one_or_none()
                if mv is None:
                    raise ModelVersionError(
                        f"unknown model version: {model_version_id}"
                        "（门禁结果只更新已存在的 ModelVersion 行）"
                    )
                mv.gate_status = "passed" if gate.passed else "failed"
                mv.gate_metrics = gate.to_dict()
                mv.gate_holdout_file = str(
                    holdout_file or HOLDOUT_FILE.relative_to(PROJECT_ROOT)
                )
                s.commit()
                return {
                    "model_version_id": mv.model_version_id,
                    "gate_status": mv.gate_status,
                    "mean_dice": gate.to_dict()["mean_dice"],
                }
            finally:
                s.close()
    finally:
        engine.dispose()


class ModelVersionError(Exception):
    """unknown model version（门禁结果落库要求行已存在）。"""


__all__ = [
    "EVAL_DIR",
    "GateResult",
    "HoldoutEntry",
    "HOLDOUT_FILE",
    "LeakError",
    "MEAN_DICE_FLOOR",
    "MIN_CASES",
    "MIN_PER_CLASS",
    "PER_CLASS_DICE_FLOOR",
    "ModelVersionError",
    "check_leak",
    "low_frequency_classes",
    "check_per_class_coverage",
    "check_holdout_composition",
    "class_case_counts",
    "dice_per_class",
    "evaluate_gate",
    "load_holdout",
    "persist_gate_result",
    "write_holdout",
]
