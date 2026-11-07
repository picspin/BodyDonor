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
- **多教师一致性规则 M3 不启用**：M3 只有 TotalSegmentator 一个教师，无第二
  mask 可比（proposal §7 明示该规则在 M4 学生模型出现后启用，不为通过验收
  造假数据）。配置 enabled=false；M4 起按同一冷启动流程校准后开启。
- **抽检混合采样**（proposal §6 第 5 阶段）：flagged 70% / clean 随机 30%
  生成 ReviewTask（clean 组确认「没 flag 的真好」）。

规则实现（全为 NIfTI 体素统计，无 GPU、无网络）：

- volume_prior：体积占比出窗
- connected_component：主连通域占比低 / 连通域数过多
- holes：内部空洞占比高
- boundary_smoothness：表面积/体积形状因子异常
- hu_distribution：mask 内 HU 分布出软组织窗（骨类跳过）
- multi_teacher_consistency：**M3 关闭**
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

QC_THRESHOLDS_PATH = Path(__file__).resolve().parents[2] / "config" / "qc_thresholds.yaml"

# 规则名（QCReport.rule_scores 的键；与 yaml rules.* 对应）
RULE_VOLUME_PRIOR = "volume_prior"
RULE_CONNECTED_COMPONENT = "connected_component"
RULE_HOLES = "holes"
RULE_BOUNDARY_SMOOTHNESS = "boundary_smoothness"
RULE_HU_DISTRIBUTION = "hu_distribution"
RULE_MULTI_TEACHER = "multi_teacher_consistency"
RULE_LEFT_RIGHT = "left_right_symmetry"

# 恒开检查（不占 config 槽位；一票否决）
RULE_EMPTY_MASK = "empty_mask"
RULE_FULL_MASK = "full_mask"

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


def rule_multi_teacher_consistency(**_kw) -> tuple[bool, float, dict[str, Any]]:
    """多教师一致性：**M3 不执行**（单教师无第二 mask 可比）。

    M4 学生模型出现后启用（proposal §7：不为通过验收造假数据）。本函数恒返回
    skipped=True，M3 的 QCReport 不含该规则的 flagged 记录。
    """
    return False, 1.0, {"skipped": "M3 单教师：该规则在 M4 学生模型出现后启用"}


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
) -> QCResult:
    """对一张 mask 评分（proposal §7 全部规则；M3 无多教师一致性）。

    - 空 mask / 全 mask：恒开检查，任一命中一票否决（passed=false）
    - volume_prior 在 hard_flag_rules 里：一票否决
    - overall_score = 1 - flagged_rules/enabled_rules（线性降，空/全 mask = 0）
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
    for rule in (RULE_VOLUME_PRIOR, RULE_CONNECTED_COMPONENT, RULE_HOLES,
                 RULE_BOUNDARY_SMOOTHNESS):
        cfg = _rule_cfg(th, rule)
        if not cfg.get("enabled", True):
            rule_scores[rule] = {"skipped": "disabled"}
            continue
        flagged, score, detail = BUILTIN_RULES[rule](
            arr, img_shape=img_shape, cfg=cfg
        )
        rule_outputs.append((rule, flagged, score, detail))
    # hu_distribution（需要原图 HU + 类名）
    cfg = _rule_cfg(th, RULE_HU_DISTRIBUTION)
    if cfg.get("enabled", True):
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
    # multi_teacher_consistency：M3 恒 skipped（单教师）
    cfg = _rule_cfg(th, RULE_MULTI_TEACHER)
    if cfg.get("enabled", False):
        # M4 起才可能进入这里；M3 配置恒 false
        flagged, score, detail = rule_multi_teacher_consistency()
        rule_outputs.append((RULE_MULTI_TEACHER, flagged, score, detail))
    else:
        rule_scores[RULE_MULTI_TEACHER] = {"skipped": cfg.get(
            "note", "M3 单教师：该规则在 M4 学生模型出现后启用")}

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


__all__ = [
    "QCResult",
    "RULE_BOUNDARY_SMOOTHNESS",
    "RULE_CONNECTED_COMPONENT",
    "RULE_EMPTY_MASK",
    "RULE_FULL_MASK",
    "RULE_HOLES",
    "RULE_HU_DISTRIBUTION",
    "RULE_LEFT_RIGHT",
    "RULE_MULTI_TEACHER",
    "RULE_VOLUME_PRIOR",
    "QC_THRESHOLDS_PATH",
    "SamplingPlan",
    "load_qc_thresholds",
    "run_qc",
    "sample_review_tasks",
]
