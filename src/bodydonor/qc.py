"""QC 引擎（M3，proposal §7）：规则评分 + 两级校准 + 抽检混合采样。

架构：

- **阈值全部集中在 `config/qc_thresholds.yaml`**（proposal §7：禁止散在代码里，
  改阈值 = config diff，不是代码 diff）；每个阈值带 `source` 注记
  （cold_start | calibrated_on_N），报告里可见。
- **两级校准**：
  - **冷启动**：在 TS 公开集（CT-Lite 镜像，CC-BY-4.0，含真值 mask）上跑 QC
    引擎，拿「QC 分数 vs 与真值 Dice」的相关性定 separating threshold
    （`qc_coldstart()`）。该数据集真值与教师同源偏乐观，只当起点（proposal §7）。
  - **在线校准**：人工抽检结果（接受/修正/拒绝）回流为标签，定期
    `qc_calibrate()` 重算各阈值并写回 yaml（source 变 calibrated_on_N）。
- **多教师一致性规则 M4 启用**：M3 只有 TotalSegmentator 一个教师，无第二
  mask 可比（proposal §7 明示不为通过验收造假数据）；M4 学生模型出现后启用，
  按 **mean Dice** 聚合（阈值 0.80，校准依据见 rule docstring：均值 flag
  5/30 例有判别力，最差类 flag 24/30 例无判别力）。单图 QC 无学生 mask 时
  显式 skipped（不进 flags、不计入 enabled 分母）。
- **抽检混合采样**（proposal §6 第 5 阶段）：flagged 70% / clean 随机 30%
  生成 ReviewTask（clean 组确认「没 flag 的真好」）。

规则实现（全为 NIfTI 体素统计，无 GPU、无网络）：

- volume_prior：体积占比出窗
- connected_component：主连通域占比低 / 连通域数过多
- holes：内部空洞占比高
- boundary_smoothness：表面积/体积形状因子异常
- hu_distribution：mask 内 HU 分布出软组织窗（骨类跳过）
- multi_teacher_consistency：教师 vs 学生逐类 Dice（M4 启用，均值聚合）
- left_right_symmetry：成对器官体积比异常
- empty/full mask 检查：恒开（空 mask/全 mask 一票否决，不占 config 槽位）
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import Settings
from .qc_interaction import (
    RULE_INTERACTION_ABANDONMENT,
    RULE_INTERACTION_CONVERGENCE,
    rule_interaction_abandonment,
    rule_interaction_convergence,
)

QC_THRESHOLDS_PATH = Path(__file__).resolve().parents[2] / "config" / "qc_thresholds.yaml"

# 规则名（QCReport.rule_scores 的键；与 yaml rules.* 对应）
RULE_VOLUME_PRIOR = "volume_prior"
RULE_CONNECTED_COMPONENT = "connected_component"
RULE_HOLES = "holes"
RULE_BOUNDARY_SMOOTHNESS = "boundary_smoothness"
RULE_HU_DISTRIBUTION = "hu_distribution"
RULE_MULTI_TEACHER = "multi_teacher_consistency"
RULE_LEFT_RIGHT = "left_right_symmetry"
# M6-b 过程级规则（provenance.interactionMetrics；qc_interaction.py 本体）：
# RULE_INTERACTION_CONVERGENCE / RULE_INTERACTION_ABANDONMENT 自 qc_interaction
# 导入（上方），yaml rules.* 同名

# 恒开检查（不占 config 槽位；一票否决）
RULE_EMPTY_MASK = "empty_mask"
RULE_FULL_MASK = "full_mask"

# 模态门闩（S3）：规则适用的模态。不在名单里的模态 → 规则 skipped
# {"skipped": "modality unsupported"}（不进 flags、不计入 enabled 分母）；
# modality 未知（None/空）→ skipped {"skipped": "modality_unknown"}（§2.2.3
# 跳过写原因，不静默按 CT 处理）。未声明的规则默认全模态适用（向后兼容）。
RULE_MODALITIES: dict[str, tuple[str, ...]] = {
    RULE_HU_DISTRIBUTION: ("CT",),  # HU 软组织窗只对 CT 有意义（MR 无 HU）
}

# 在线校准（proposal §7）：规则判别力下限（flag 组与 clean 组标签均值差下限，
# 低于此说明规则无判别力，只更新注记不改数值）
MIN_SEPARATION = 0.1

# 数值重算门槛（M4 阶段 4）：抽检决策 < 20 例时不做数值重算（M3-REPORT §3.3/§3.4：
# 冷启动阈值只是起点；标签量不足时硬算数值等于用噪声定阈值）。注记更新
# （source / discrimination）同样等够 20 例再写回——不够就记 pending，不动 yaml。
MIN_LABELS_FOR_RECALIBRATION = 20


def recalibration_ready(n_labels: int) -> tuple[bool, str]:
    """数值重算是否可触发：抽检决策 ≥ 20 例。返回 (ready, 原因)。"""
    if n_labels >= MIN_LABELS_FOR_RECALIBRATION:
        return True, (
            f"抽检决策 {n_labels} 例 ≥ {MIN_LABELS_FOR_RECALIBRATION}，可重算"
        )
    return False, (
        f"抽检决策 {n_labels} 例 < {MIN_LABELS_FOR_RECALIBRATION}——数值重算 "
        "pending，不写回 qc_thresholds.yaml（标签量不足时硬算数值等于用噪声定阈值）"
    )

# 骨类关键字（HU 软组织窗不适用，hu_distribution 跳过）
BONE_CLASS_KEYWORDS = (
    "vertebrae", "rib", "femur", "hip", "skull", "clavicula", "scapula",
    "humerus", "costal", "teeth", "sacrum", "craniofacial", "mandible",
)


def load_qc_thresholds(path: Path | None = None) -> dict[str, Any]:
    """qc_thresholds.yaml -> dict（缺失/损坏回退内置默认，source 标 fallback）。"""
    p = path or QC_THRESHOLDS_PATH
    if not p.exists():
        raise FileNotFoundError(f"QC 阈值配置不存在：{p}（先建 config/qc_thresholds.yaml）")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "rules" not in raw:
        raise ValueError(f"QC 阈值配置损坏（无 rules 段）：{p}")
    return raw


def _rule_cfg(thresholds: dict[str, Any], rule: str) -> dict[str, Any]:
    cfg = thresholds.get("rules", {}).get(rule, {})
    return cfg if isinstance(cfg, dict) else {}


def _is_bone_class(totalseg_class: str) -> bool:
    return any(k in totalseg_class.lower() for k in BONE_CLASS_KEYWORDS)


def _modality_skip(
    rule: str, modality: str | None
) -> dict[str, str] | None:
    """模态门闩：规则不适用于该模态 / modality 未知时返回 skipped 注记。

    返回 None = 规则可跑；返回 dict = rule_scores 记该 skipped（不进 flags、
    不计入 enabled 分母，与 disabled 规则同处理）。
    """
    if not modality:
        return {"skipped": "modality_unknown"}
    allowed = RULE_MODALITIES.get(rule)
    # 归一化（W8）：DICOM Modality 大小写/空白不齐（"ct"/" CT"）按 CT 处理，
    # 与 review_session.py 同口径
    m = modality.strip().upper()
    if allowed is not None and m not in allowed:
        # W7：unknown 与 None/空同语义（B1 后 ingest 层不再产出 unknown，
        # 旧数据仍可能回读）——归 modality_unknown，不再混用两种 skip 文案
        if m == "UNKNOWN":
            return {"skipped": "modality_unknown"}
        return {"skipped": "modality unsupported"}
    return None


# ---------------------------------------------------------------------------
# 规则实现（每条返回 (flagged: bool, score: float, detail: dict)；
# score ∈ [0,1]，1 = 该规则完全没问题）
# ---------------------------------------------------------------------------

def rule_volume_prior(
    arr: Any, img_shape: tuple[int, ...], cfg: dict[str, Any], **_kw
) -> tuple[bool, float, dict[str, Any]]:
    """体积先验：mask 体积占比出 [min_frac, max_frac] 窗则 flagged。"""
    if not cfg.get("enabled", True):
        return False, 1.0, {"skipped": "disabled"}
    n_vox = int((arr > 0).sum())
    total = int(math.prod(img_shape))
    frac = n_vox / total if total else 0.0
    mn, mx = float(cfg["min_frac"]), float(cfg["max_frac"])
    flagged = not (mn <= frac <= mx)
    return flagged, (0.0 if flagged else 1.0), {
        "volume_frac": round(frac, 6), "min_frac": mn, "max_frac": mx, "n_voxels": n_vox,
    }


def rule_connected_component(
    arr: Any, cfg: dict[str, Any], **_kw
) -> tuple[bool, float, dict[str, Any]]:
    """连通域检查：主连通域占比 < min_main_frac 或域数 > max_components 则 flagged。

    逐类统计（多标签图按 label 拆），取最差类的结果；二值图（0/1）也适用。
    """
    if not cfg.get("enabled", True):
        return False, 1.0, {"skipped": "disabled"}
    import numpy as np
    from scipy import ndimage

    labels = [int(v) for v in np.unique(arr) if v != 0]
    if not labels:
        return True, 0.0, {"error": "empty mask"}
    min_main = float(cfg["min_main_frac"])
    max_comp = int(cfg["max_components"])
    worst_main, worst_label, worst_n = 1.0, 0, 0
    struct = ndimage.generate_binary_structure(3, 1)  # 6-连通
    for lb in labels:
        binary = arr == lb
        lab_arr, n_comp = ndimage.label(binary, structure=struct)
        if n_comp == 0:
            continue
        sizes = ndimage.sum(binary, lab_arr, range(1, n_comp + 1))
        main_frac = float(sizes.max()) / float(binary.sum()) if binary.sum() else 0.0
        if main_frac < worst_main:
            worst_main, worst_label, worst_n = main_frac, lb, int(n_comp)
    flagged = worst_main < min_main or worst_n > max_comp
    return flagged, (0.0 if flagged else 1.0), {
        "min_main_frac": min_main, "max_components": max_comp,
        "worst_main_frac": round(worst_main, 4), "worst_label": worst_label,
        "worst_n_components": worst_n,
    }


def rule_holes(
    arr: Any, cfg: dict[str, Any], **_kw
) -> tuple[bool, float, dict[str, Any]]:
    """空洞检查：二值闭运算填充体积占比 > max_hole_frac 则 flagged（最差类）。"""
    if not cfg.get("enabled", True):
        return False, 1.0, {"skipped": "disabled"}
    import numpy as np
    from scipy import ndimage

    labels = [int(v) for v in np.unique(arr) if v != 0]
    if not labels:
        return True, 0.0, {"error": "empty mask"}
    max_hole = float(cfg["max_hole_frac"])
    struct = ndimage.generate_binary_structure(3, 1)
    worst, worst_label = 0.0, 0
    for lb in labels:
        binary = arr == lb
        filled = ndimage.binary_fill_holes(binary, structure=struct)
        total = int(filled.sum())
        if total == 0:
            continue
        hole_frac = float(filled.sum() - binary.sum()) / float(total)
        if hole_frac > worst:
            worst, worst_label = hole_frac, lb
    flagged = worst > max_hole
    return flagged, (0.0 if flagged else 1.0), {
        "max_hole_frac": max_hole, "worst_hole_frac": round(worst, 4),
        "worst_label": worst_label,
    }


def rule_boundary_smoothness(
    arr: Any, cfg: dict[str, Any], **_kw
) -> tuple[bool, float, dict[str, Any]]:
    """边界平滑度：表面积/体积^(2/3) 形状因子 > max_boundary_ratio 则 flagged。

    球体形状因子 ≈ 4.836（(4πr²)/( (4/3)πr³ )^(2/3) = (36π)^(1/3)）；体素锯齿
    会放大表面积。阈值按冷启动分布选定。
    """
    if not cfg.get("enabled", True):
        return False, 1.0, {"skipped": "disabled"}
    import numpy as np
    from scipy import ndimage

    labels = [int(v) for v in np.unique(arr) if v != 0]
    if not labels:
        return True, 0.0, {"error": "empty mask"}
    max_ratio = float(cfg["max_boundary_ratio"])
    struct = ndimage.generate_binary_structure(3, 1)
    worst, worst_label = 0.0, 0
    for lb in labels:
        binary = arr == lb
        n_vox = float(binary.sum())
        if n_vox < 8:
            continue  # 太小的类形状因子无意义
        # 表面积 = 边界面数（6-邻域侵蚀差分）
        eroded = ndimage.binary_erosion(binary, structure=struct)
        surface = float(binary.sum() - eroded.sum())
        if surface == 0:
            continue
        sphericity = surface / (n_vox ** (2.0 / 3.0))
        if sphericity > worst:
            worst, worst_label = sphericity, lb
    flagged = worst > max_ratio
    return flagged, (0.0 if flagged else 1.0), {
        "max_boundary_ratio": max_ratio, "worst_sphericity": round(worst, 4),
        "worst_label": worst_label,
    }


def rule_hu_distribution(
    arr: Any, hu_values: Any, totalseg_classes: dict[int, str] | None,
    cfg: dict[str, Any], **_kw
) -> tuple[bool, float, dict[str, Any]]:
    """HU 分布合理性：mask 内 HU P5..P95 出软组织窗则 flagged（骨类跳过）。

    hu_values：原图像素值数组（HU，int16 CT）；totalseg_classes：label -> 类名
    （骨类跳过软组织窗，§7.2 本院化校准主体——M3 用公开集冷启动初值）。
    """
    if not cfg.get("enabled", True):
        return False, 1.0, {"skipped": "disabled"}
    import numpy as np

    if hu_values is None:
        return False, 1.0, {"skipped": "no image pixels"}
    hu = np.asarray(hu_values)
    pct = int(cfg.get("percentile", 5))
    mn, mx = float(cfg["min_hu"]), float(cfg["max_hu"])
    skip_bone = bool(cfg.get("skip_bone_classes", True))
    labels = [int(v) for v in np.unique(arr) if v != 0]
    worst, worst_label = 0.0, 0
    for lb in labels:
        if skip_bone and totalseg_classes and _is_bone_class(totalseg_classes.get(lb, "")):
            continue
        vals = hu[arr == lb]
        if vals.size < 50:
            continue  # 体素太少 HU 分位无意义
        p5, p95 = np.percentile(vals, [pct, 100 - pct])
        # 出窗程度：窗口外宽度 / 窗宽
        out_w = max(0.0, mn - float(p5)) + max(0.0, float(p95) - mx)
        window = mx - mn
        severity = out_w / window if window > 0 else 0.0
        if severity > worst:
            worst, worst_label = severity, lb
    flagged = worst > 0.0
    return flagged, (0.0 if flagged else 1.0), {
        "min_hu": mn, "max_hu": mx, "percentile": pct,
        "worst_severity": round(worst, 4), "worst_label": worst_label,
        "skip_bone_classes": skip_bone,
    }


def rule_left_right_symmetry(
    arr: Any, totalseg_classes: dict[int, str] | None,
    cfg: dict[str, Any], **_kw
) -> tuple[bool, float, dict[str, Any]]:
    """左右对称性：成对类体积比 |log(V_L/V_R)| > max_log_ratio 则 flagged。

    只比较两侧都非空的类（一侧缺失不算——TS 单侧输出常见且合法）。
    """
    if not cfg.get("enabled", True):
        return False, 1.0, {"skipped": "disabled"}

    if not totalseg_classes:
        return False, 1.0, {"skipped": "no class names"}
    max_ratio = float(cfg["max_log_ratio"])
    pairs = [tuple(p) for p in cfg.get("pairs", [])]
    label_by_class = {v: k for k, v in totalseg_classes.items()}
    worst, worst_pair = 0.0, ""
    for left_cls, right_cls in pairs:
        li, ri = label_by_class.get(left_cls), label_by_class.get(right_cls)
        if li is None or ri is None:
            continue
        vl = float((arr == li).sum())
        vr = float((arr == ri).sum())
        if vl <= 0 or vr <= 0:
            continue  # 一侧缺失不比对（TS 输出单侧合法）
        lr = abs(math.log(vl / vr))
        if lr > worst:
            worst, worst_pair = lr, f"{left_cls}|{right_cls}"
    flagged = worst > max_ratio
    return flagged, (0.0 if flagged else 1.0), {
        "max_log_ratio": max_ratio, "worst_log_ratio": round(worst, 4),
        "worst_pair": worst_pair,
    }


def rule_multi_teacher_consistency(
    teacher_arr: Any, student_arr: Any,
    teacher_classes: dict[int, str] | None,
    student_classes: dict[int, str] | None,
    cfg: dict[str, Any], **_kw,
) -> tuple[bool, float, dict[str, Any]]:
    """多教师一致性（M4 启用）：教师 mask vs 学生 mask 逐类体素 Dice。

    M4 学生模型出现后启用（proposal §7：不为通过验收造假数据）。聚合方式 =
    参与比较类的 **mean Dice**（不是最差类）——校准依据（M4 收尾第二轮实测，
    30 例教师 vs 学生逐类 Dice，2026-11-11）：最差类聚合在 threshold=0.80 下
    flag 24/30 例（无判别力，基本等于全审），均值聚合 flag 5/30 例（flag 的
    正是分歧最大的 5 例：case 级均值最低者）。阈值数值保持 0.80 不改，校准的
    是聚合方式。阶段 5 校准输入：eval/student_teacher_consistency.json
    （419 对 mean 0.8560、p10 0.6986）。

    - 按类名对齐：教师值用 TS 标准值、学生值用 Dataset501 值 1-15（编号不同，
      必须按名字对齐，不能按值比）
    - 只比两者名字交集的类：学生范围外的教师类跳过（detail 记
      skipped_teacher_only_classes 数量与类名），不算不一致
    - 逐类 Dice 只算两边任一非空的类（空对空不计）
    - flagged = 参与比较类的 mean Dice < cfg threshold
    """
    if not cfg.get("enabled", True):
        return False, 1.0, {"skipped": "disabled"}
    import numpy as np

    if teacher_arr is None or student_arr is None:
        return False, 1.0, {"skipped": "no student mask"}
    threshold = float(cfg.get("threshold", 0.80))
    teacher_classes = teacher_classes or {}
    student_classes = student_classes or {}

    t_arr = np.asarray(teacher_arr)
    s_arr = np.asarray(student_arr)
    t_label_by_name = {v: k for k, v in teacher_classes.items()}
    s_label_by_name = {v: k for k, v in student_classes.items()}
    teacher_names = set(t_label_by_name)
    student_names = set(s_label_by_name)
    skipped_teacher_only = sorted(teacher_names - student_names)

    per_class: dict[str, float] = {}
    for name in sorted(teacher_names & student_names):
        t = t_arr == t_label_by_name[name]
        s = s_arr == s_label_by_name[name]
        denom = float(t.sum()) + float(s.sum())
        if denom == 0:
            continue  # 空对空不计
        per_class[name] = 2.0 * float((t & s).sum()) / denom

    if not per_class:
        return False, 1.0, {
            "threshold": threshold, "mean_dice": 1.0,
            "n_compared_classes": 0,
            "skipped_teacher_only_classes": len(skipped_teacher_only),
            "skipped_teacher_only_names": skipped_teacher_only,
            "note": "无交集非空类可比（不 flag，不算不一致）",
        }
    mean_dice = sum(per_class.values()) / len(per_class)
    worst_class = min(per_class, key=per_class.__getitem__)
    flagged = mean_dice < threshold
    return flagged, (0.0 if flagged else 1.0), {
        "threshold": threshold,
        "mean_dice": round(mean_dice, 4),
        "worst_class": worst_class,
        "worst_dice": round(per_class[worst_class], 4),
        "n_compared_classes": len(per_class),
        "per_class_dice": {k: round(v, 4) for k, v in sorted(per_class.items())},
        "skipped_teacher_only_classes": len(skipped_teacher_only),
        "skipped_teacher_only_names": skipped_teacher_only,
    }


# 恒开规则名 -> 实现
BUILTIN_RULES = {
    RULE_VOLUME_PRIOR: rule_volume_prior,
    RULE_CONNECTED_COMPONENT: rule_connected_component,
    RULE_HOLES: rule_holes,
    RULE_BOUNDARY_SMOOTHNESS: rule_boundary_smoothness,
}


@dataclass
class QCResult:
    """一次 QC 评分的完整输出（QCReport 落库来源）。"""

    mask_version_id: str
    series_id: str
    overall_score: float
    flags: list[str] = field(default_factory=list)
    rule_scores: dict[str, Any] = field(default_factory=dict)
    passed: bool = True
    config_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mask_version_id": self.mask_version_id,
            "series_id": self.series_id,
            "overall_score": round(self.overall_score, 4),
            "flags": list(self.flags),
            "rule_scores": dict(self.rule_scores),
            "passed": self.passed,
            "config_source": self.config_source,
        }


def run_qc(
    mask_arr: Any,
    img_shape: tuple[int, ...],
    mask_version_id: str,
    series_id: str,
    settings: Settings | None = None,
    hu_values: Any = None,
    totalseg_classes: dict[int, str] | None = None,
    thresholds: dict[str, Any] | None = None,
    student_mask_arr: Any = None,
    student_classes: dict[int, str] | None = None,
    interaction_metrics: dict[str, dict[str, Any]] | None = None,
    modality: str | None = None,
) -> QCResult:
    """对一张 mask 评分（proposal §7 全部规则）。

    - 空 mask / 全 mask：恒开检查，任一命中一票否决（passed=false）
    - volume_prior 在 hard_flag_rules 里：一票否决
    - overall_score = 1 - flagged_rules/enabled_rules（线性降，空/全 mask = 0）
    - multi_teacher_consistency（M4 启用）：仅当 student_mask_arr 给出才真跑；
      enabled 但没给 → rule_scores 记 skipped（不进 flags、不计入 enabled
      分母，与 disabled 规则同处理）
    - interaction_convergence / interaction_abandonment（M6-b 过程级）：
      仅当 interaction_metrics 非空 dict（provenance.interactionMetrics，
      有 nnInteractive 交互）才真跑；enabled 但无/空 → 显式 skipped（不进
      flags、不计入 enabled 分母，与 disabled 规则同处理，纯自动 case 不造假）
    - 模态门闩（S3）：modality 给出且规则不在 RULE_MODALITIES 适用名单 →
      skipped {"skipped": "modality unsupported"}；modality 未知（None/空）
      → skipped {"skipped": "modality_unknown"}（跳过写原因，不静默按 CT）。
      未声明模态的规则不受影响（恒开检查、volume_prior 等照跑）——modality
      未传/未知时 HU 规则跳过，不再静默按 CT。
    """
    import numpy as np

    settings = settings or _default_settings()
    th = thresholds or load_qc_thresholds()
    flags: list[str] = []
    rule_scores: dict[str, Any] = {}

    # ---- 恒开检查：空 mask / 全 mask（一票否决）----
    n_vox = int((mask_arr > 0).sum())
    total = int(math.prod(img_shape))
    if n_vox == 0:
        rule_scores[RULE_EMPTY_MASK] = {"flagged": True, "detail": {"n_voxels": 0}}
        return QCResult(
            mask_version_id, series_id, 0.0, [RULE_EMPTY_MASK], rule_scores,
            passed=False, config_source=str(th.get("cold_start", {}).get("dataset", "")),
        )
    rule_scores[RULE_EMPTY_MASK] = {"flagged": False, "detail": {"n_voxels": n_vox}}
    if total and n_vox >= total:
        rule_scores[RULE_FULL_MASK] = {"flagged": True, "detail": {"n_voxels": n_vox}}
        return QCResult(
            mask_version_id, series_id, 0.0, [RULE_FULL_MASK], rule_scores,
            passed=False, config_source=str(th.get("cold_start", {}).get("dataset", "")),
        )
    rule_scores[RULE_FULL_MASK] = {"flagged": False, "detail": {}}

    # ---- config 槽位规则 ----
    arr = np.asarray(mask_arr)
    rule_outputs: list[tuple[str, bool, float, dict[str, Any]]] = []

    # 体积先验（无 per-label 循环）
    cfg = _rule_cfg(th, RULE_VOLUME_PRIOR)
    if cfg.get("enabled", True):
        flagged, score, detail = rule_volume_prior(arr, img_shape, cfg)
        rule_outputs.append((RULE_VOLUME_PRIOR, flagged, score, detail))
    else:
        rule_scores[RULE_VOLUME_PRIOR] = {"skipped": "disabled"}

    # connected_component + holes + boundary_smoothness：单次 per-label 循环
    #（512×512×1000 层级 × 117 类逐规则循环会到分钟级；合并成一遍 + 早跳小类）
    import numpy as np
    from scipy import ndimage

    cc_cfg = _rule_cfg(th, RULE_CONNECTED_COMPONENT)
    holes_cfg = _rule_cfg(th, RULE_HOLES)
    bnd_cfg = _rule_cfg(th, RULE_BOUNDARY_SMOOTHNESS)
    cc_on, holes_on, bnd_on = (
        cc_cfg.get("enabled", True), holes_cfg.get("enabled", True),
        bnd_cfg.get("enabled", True),
    )
    struct = ndimage.generate_binary_structure(3, 1)
    present, present_sizes = np.unique(arr, return_counts=True)
    label_sizes = {
        int(v): int(s) for v, s in zip(present, present_sizes, strict=True) if v != 0
    }
    labels = list(label_sizes)
    # find_objects 一次拿全部类的外接框（2.2s vs 逐类 argwhere ~100 次全图扫描）
    obj_slices = ndimage.find_objects(arr)
    cc_detail = {"min_main_frac": float(cc_cfg.get("min_main_frac", 0.8)),
                 "max_components": int(cc_cfg.get("max_components", 20)),
                 "worst_main_frac": 1.0, "worst_label": 0, "worst_n_components": 0}
    holes_detail = {"max_hole_frac": float(holes_cfg.get("max_hole_frac", 0.2)),
                    "worst_hole_frac": 0.0, "worst_label": 0}
    bnd_detail = {"max_boundary_ratio": float(bnd_cfg.get("max_boundary_ratio", 1.2)),
                  "worst_sphericity": 0.0, "worst_label": 0}
    for lb in labels:
        if label_sizes[lb] < 50:
            continue  # 小类体素太少，域/洞/形状因子无意义（与 HU 阈值一致）
        # bbox 裁剪：逐类统计只在类的外接框内做（实测 44x 提速；域/洞/形状因子
        # 都是局部统计，外接框外全为背景，结果与全图一致）
        sl = obj_slices[lb - 1]
        if sl is None:
            continue
        binary = arr[sl] == lb
        if cc_on:
            lab_arr, n_comp = ndimage.label(binary, structure=struct)
            if n_comp > 0:
                sizes = ndimage.sum(binary, lab_arr, range(1, n_comp + 1))
                main_frac = float(sizes.max()) / float(label_sizes[lb])
                if main_frac < cc_detail["worst_main_frac"]:
                    cc_detail["worst_main_frac"] = round(main_frac, 4)
                    cc_detail["worst_label"] = lb
                    cc_detail["worst_n_components"] = int(n_comp)
        if holes_on:
            filled = ndimage.binary_fill_holes(binary, structure=struct)
            total_lb = int(filled.sum())
            if total_lb > 0:
                hole_frac = float(total_lb - label_sizes[lb]) / float(total_lb)
                if hole_frac > holes_detail["worst_hole_frac"]:
                    holes_detail["worst_hole_frac"] = round(hole_frac, 4)
                    holes_detail["worst_label"] = lb
        if bnd_on:
            n_vox_lb = float(label_sizes[lb])
            if n_vox_lb >= 8:
                eroded = ndimage.binary_erosion(binary, structure=struct)
                surface = float(label_sizes[lb] - eroded.sum())
                if surface > 0:
                    sphericity = surface / (n_vox_lb ** (2.0 / 3.0))
                    if sphericity > bnd_detail["worst_sphericity"]:
                        bnd_detail["worst_sphericity"] = round(sphericity, 4)
                        bnd_detail["worst_label"] = lb
    if cc_on:
        cc_flag = (cc_detail["worst_main_frac"] < float(cc_cfg["min_main_frac"])
                   or cc_detail["worst_n_components"] > int(cc_cfg["max_components"]))
        rule_outputs.append((RULE_CONNECTED_COMPONENT, cc_flag, 0.0 if cc_flag else 1.0, cc_detail))
    else:
        rule_scores[RULE_CONNECTED_COMPONENT] = {"skipped": "disabled"}
    if holes_on:
        holes_flag = holes_detail["worst_hole_frac"] > float(holes_cfg["max_hole_frac"])
        rule_outputs.append((RULE_HOLES, holes_flag, 0.0 if holes_flag else 1.0, holes_detail))
    else:
        rule_scores[RULE_HOLES] = {"skipped": "disabled"}
    if bnd_on:
        bnd_flag = bnd_detail["worst_sphericity"] > float(bnd_cfg["max_boundary_ratio"])
        rule_outputs.append(
            (RULE_BOUNDARY_SMOOTHNESS, bnd_flag, 0.0 if bnd_flag else 1.0, bnd_detail)
        )
    else:
        rule_scores[RULE_BOUNDARY_SMOOTHNESS] = {"skipped": "disabled"}

    # hu_distribution（需要原图 HU + 类名；模态门闩：只对 CT 有意义）
    cfg = _rule_cfg(th, RULE_HU_DISTRIBUTION)
    if cfg.get("enabled", True):
        mskip = _modality_skip(RULE_HU_DISTRIBUTION, modality)
        if mskip is not None:
            rule_scores[RULE_HU_DISTRIBUTION] = mskip
        else:
            flagged, score, detail = rule_hu_distribution(
                arr, hu_values, totalseg_classes, cfg
            )
            rule_outputs.append((RULE_HU_DISTRIBUTION, flagged, score, detail))
    else:
        rule_scores[RULE_HU_DISTRIBUTION] = {"skipped": "disabled"}
    # left_right_symmetry（需要类名）
    cfg = _rule_cfg(th, RULE_LEFT_RIGHT)
    if cfg.get("enabled", True):
        flagged, score, detail = rule_left_right_symmetry(
            arr, totalseg_classes, cfg
        )
        rule_outputs.append((RULE_LEFT_RIGHT, flagged, score, detail))
    else:
        rule_scores[RULE_LEFT_RIGHT] = {"skipped": "disabled"}
    # multi_teacher_consistency（M4 启用）：enabled 且给了学生 mask → 真跑；
    # enabled 但没给 → 显式 skipped（不进 flags、不计入 enabled 分母，与
    # disabled 规则同处理）——qc_pipeline 调用点目前无学生 mask 可用（单图
    # QC 时学生重推理代价 GPU 级），保持不传，不为传而传造假 mask
    cfg = _rule_cfg(th, RULE_MULTI_TEACHER)
    if cfg.get("enabled", False):
        if student_mask_arr is not None:
            flagged, score, detail = rule_multi_teacher_consistency(
                arr, student_mask_arr, totalseg_classes, student_classes, cfg,
            )
            rule_outputs.append((RULE_MULTI_TEACHER, flagged, score, detail))
        else:
            rule_scores[RULE_MULTI_TEACHER] = {"skipped": "no student mask"}
    else:
        rule_scores[RULE_MULTI_TEACHER] = {"skipped": "disabled"}

    # interaction_convergence / interaction_abandonment（M6-b 过程级）：
    # enabled 且 interaction_metrics 非空 → 真跑；enabled 但无/空 → 显式
    # skipped（纯自动 case 不造假，不进 flags、不计入 enabled 分母）。
    # high_risk_classes 显式名单传入（旧数据无 teacherReliability 标记时
    # 兜底判定，prostate + 39 低频名单同 interaction_metrics.py 口径）
    from .interaction_metrics import (
        HIGH_RISK_TEACHER_CLASSES,
        MIN_PER_CLASS,
        load_teacher_eval,
    )

    high_risk_classes = HIGH_RISK_TEACHER_CLASSES | {
        c for c, n in (load_teacher_eval() or {}).items() if n < MIN_PER_CLASS
    }
    interaction_cfg = th.get("rules", {}).get(RULE_INTERACTION_CONVERGENCE, {})
    convergence_enabled = interaction_cfg.get("enabled", False)
    abandonment_cfg = th.get("rules", {}).get(RULE_INTERACTION_ABANDONMENT, {})
    abandonment_enabled = abandonment_cfg.get("enabled", False)
    has_interaction = bool(interaction_metrics)
    if convergence_enabled:
        if has_interaction:
            flagged, score, detail = rule_interaction_convergence(
                interaction_metrics, interaction_cfg, high_risk_classes
            )
            rule_outputs.append((RULE_INTERACTION_CONVERGENCE, flagged, score, detail))
        else:
            rule_scores[RULE_INTERACTION_CONVERGENCE] = {"skipped": "no interaction metrics"}
    else:
        rule_scores[RULE_INTERACTION_CONVERGENCE] = {"skipped": "disabled"}
    if abandonment_enabled:
        if has_interaction:
            flagged, score, detail = rule_interaction_abandonment(
                interaction_metrics, abandonment_cfg, high_risk_classes
            )
            rule_outputs.append((RULE_INTERACTION_ABANDONMENT, flagged, score, detail))
        else:
            rule_scores[RULE_INTERACTION_ABANDONMENT] = {"skipped": "no interaction metrics"}
    else:
        rule_scores[RULE_INTERACTION_ABANDONMENT] = {"skipped": "disabled"}

    # ---- 汇总 ----
    hard_flags = set(th.get("overall", {}).get("hard_flag_rules", []))
    enabled_n = len(rule_outputs)
    flagged_n = 0
    for rule, flagged, _score, detail in rule_outputs:
        rule_scores[rule] = {"flagged": flagged, "score": round(_score, 4), **detail}
        if flagged:
            flags.append(rule)
            flagged_n += 1
    hard_fail = any(r in hard_flags for r in flags)
    overall = (0.0 if hard_fail else 1.0 - (flagged_n / enabled_n if enabled_n else 0.0))
    min_score = float(th.get("overall", {}).get("min_score", 0.5))
    passed = not hard_fail and overall >= min_score
    return QCResult(
        mask_version_id=mask_version_id,
        series_id=series_id,
        overall_score=overall,
        flags=flags,
        rule_scores=rule_scores,
        passed=passed,
        config_source=str(th.get("cold_start", {}).get("dataset", "")),
    )


# ---------------------------------------------------------------------------
# 抽检混合采样（proposal §6 第 5 阶段：flagged 70% / clean 随机 30%）
# ---------------------------------------------------------------------------

@dataclass
class SamplingPlan:
    """一轮抽检采样结果（ReviewTask 生成来源）。"""

    flagged_ids: list[str] = field(default_factory=list)   # mask_version_id
    clean_ids: list[str] = field(default_factory=list)
    n_total: int = 0
    flagged_pool: int = 0
    clean_pool: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "flagged": list(self.flagged_ids),
            "clean": list(self.clean_ids),
            "n_total": self.n_total,
            "flagged_pool": self.flagged_pool,
            "clean_pool": self.clean_pool,
        }


def sample_review_tasks(
    qc_rows: list[dict[str, Any]],
    thresholds: dict[str, Any] | None = None,
    rng: random.Random | None = None,
) -> SamplingPlan:
    """混合采样（proposal §6：flagged 70% / clean 随机 30%）。

    qc_rows：每行含 {mask_version_id, passed, overall_score}（passed=false 即
    flagged）。从 flagged 池按 overall_score 升序（最差优先）取 70%，clean 池
    随机取 30%；池子不足时按可用量缩放（clean 至少 min_clean_pool 条——确认
    「没 flag 的真好」）。
    """
    th = thresholds or load_qc_thresholds()
    cfg = th.get("review_sampling", {})
    flagged_ratio = float(cfg.get("flagged_ratio", 0.70))
    batch = int(cfg.get("batch_size", 10))
    min_clean = int(cfg.get("min_clean_pool", 1))
    rng = rng or random.Random()

    flagged_pool = sorted(
        (r for r in qc_rows if not r.get("passed", True)),
        key=lambda r: (r.get("overall_score", 0.0), r.get("mask_version_id", "")),
    )
    clean_pool = [r for r in qc_rows if r.get("passed", True)]
    rng.shuffle(clean_pool)

    n_flagged = min(len(flagged_pool), round(batch * flagged_ratio))
    n_clean = min(len(clean_pool), max(min_clean, batch - n_flagged))
    plan = SamplingPlan(
        flagged_ids=[r["mask_version_id"] for r in flagged_pool[:n_flagged]],
        clean_ids=[r["mask_version_id"] for r in clean_pool[:n_clean]],
        n_total=n_flagged + n_clean,
        flagged_pool=len(flagged_pool),
        clean_pool=len(clean_pool),
    )
    return plan


def _default_settings() -> Settings:
    from .config import load_settings

    return load_settings()


# ---------------------------------------------------------------------------
# 在线校准（proposal §7.2：决策回流标签 -> 阈值本体重算，bodydonor qc-calibrate）
# ---------------------------------------------------------------------------

# 决策 -> 校准标签（quality label）：accepted=1.0 / corrected=0.5 / rejected=0.0
DECISION_LABELS: dict[str, float] = {"accepted": 1.0, "corrected": 0.5, "rejected": 0.0}


def calibrate_thresholds(
    labels: list[dict[str, Any]],
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """阈值本体重算（proposal §7 在线校准；决策回流标签足够后调用）。

    labels：每条 {decision: accepted|corrected|rejected, flags: [规则名...]}——
    一条抽检决策 + 该 case 被 flag 的规则（QCReport.flags）。

    方法（最小合理选择，M3）：逐规则把「该规则 flag 过的 case 的标签均值」
    与「未 flag 过的 case 的标签均值」对比——flag 组标签均值显著低（差 >
    MIN_SEPARATION）说明规则有判别力，保留阈值；无判别力（差 <= MIN_SEPARATION）
    只更新 calibrated_on 注记、不改数值（**不静默放水**）。

    返回更新后的 thresholds dict（调用方落盘）。
    """
    th = thresholds or load_qc_thresholds()
    n = len(labels)
    if n == 0:
        return th
    y = [DECISION_LABELS.get(lb["decision"], 0.5) for lb in labels]

    for rule, cfg in th.get("rules", {}).items():
        if not isinstance(cfg, dict):
            continue
        # 该规则 flag 过 / 没 flag 过的 case 标签均值
        flagged_y = [y[i] for i, lb in enumerate(labels) if rule in lb.get("flags", [])]
        clean_y = [y[i] for i, lb in enumerate(labels) if rule not in lb.get("flags", [])]
        # 判别力：flag 组均值应低于 clean 组（flag 过的更可能被人工拒绝）
        sep = (sum(clean_y) / len(clean_y) if clean_y else 0.0) - (
            sum(flagged_y) / len(flagged_y) if flagged_y else 0.0
        )
        cfg["calibrated_on"] = n
        cfg["calibrated_at"] = _today()
        if sep > MIN_SEPARATION and flagged_y:
            # 有判别力：source 注记变 calibrated_on_N（阈值数值不动——M3 标签量
            # 不足以重算数值阈值；重算是 M4+ 标签 ≥ 20 例后的事，§7 明示）
            cfg["source"] = f"calibrated_on_{n}"
        cfg["discrimination"] = round(sep, 4)

    overall = th.setdefault("overall", {})
    overall["calibrated_on"] = n
    overall["calibrated_at"] = _today()
    return th


def save_calibrated_thresholds(
    thresholds: dict[str, Any], path: Any = None
) -> None:
    """校准结果写回 qc_thresholds.yaml（config diff 可评审）。"""
    import yaml as _yaml

    p = path or QC_THRESHOLDS_PATH
    with open(p, "w", encoding="utf-8") as f:
        _yaml.safe_dump(thresholds, f, allow_unicode=True, sort_keys=False)


def _today() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%d")


__all__ = [
    "QCResult",
    "RULE_MODALITIES",
    "RULE_BOUNDARY_SMOOTHNESS",
    "RULE_CONNECTED_COMPONENT",
    "RULE_EMPTY_MASK",
    "RULE_FULL_MASK",
    "RULE_HOLES",
    "RULE_HU_DISTRIBUTION",
    "RULE_LEFT_RIGHT",
    "RULE_MULTI_TEACHER",
    "RULE_VOLUME_PRIOR",
    "MIN_SEPARATION",
    "QC_THRESHOLDS_PATH",
    "SamplingPlan",
    "calibrate_thresholds",
    "load_qc_thresholds",
    "run_qc",
    "sample_review_tasks",
    "save_calibrated_thresholds",
]
