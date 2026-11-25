"""M6-c：过程风险驱动采样（sample_review_tasks_by_priority）单测 + 排序一致性检验。

- 分量单测：w1/w2/w3/w4 各分量定义与归一化、降级语义（无 interactionMetrics
  → w1=w2=0 + 记不可用；multi_teacher skipped → w3 不可用 + 记不可用）。
- 接线单测：generate_review_tasks 生产路径用 priority 采样（ranked/每分量
  可用性进返回值）；sampling_priority.enabled=false 回退 70/30 旧路径。
- 排序一致性检验（验收）：30 例 hold-out 真实 w3（真实教师+学生 mask 现算，
  同 test_m6e_wiring.py 口径，不 hardcode 数字）+ 真实 w4（39/117 低频名单，
  eval/holdout_results.json per_class_cases）+ 合成 w1/w2（synthetic
  interactionMetrics，只用于覆盖 w1/w2 分量）。

硬性验收：s0209 与 prostate 漏检 case（教师整类漏检，AMOS 侧实测
amos_0004/0011/0014/0015/0016 教师 prostate 体素 0——不 hardcode 进断言，
从现算逐类 Dice=0 的 case 里指认）进前 20%（30 例中前 6）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bodydonor.qc import SamplingPlan
from bodydonor.qc_priority import (
    component_abandonment,
    component_convergence,
    component_disagreement,
    component_low_freq_presence,
    sample_review_tasks_by_priority,
)

REPO = Path(__file__).resolve().parents[1]

# 只开 multi_teacher 的最小 thresholds（回归只看该规则 + sampling_priority）
MIN_THRESHOLDS = {
    "cold_start": {"dataset": "m6c_regression"},
    "sampling_priority": {
        "enabled": True,
        "weights": {"w1": 1.0, "w2": 1.0, "w3": 1.0, "w4": 1.0},
    },
    "rules": {
        "multi_teacher_consistency": {"enabled": True, "threshold": 0.80},
        "interaction_convergence": {"enabled": True, "n_max_prompts": 15, "k_max_steps": 6},
        "interaction_abandonment": {"enabled": True, "min_abandonments": 1},
        "volume_prior": {"enabled": False},
        "connected_component": {"enabled": False},
        "holes": {"enabled": False},
        "boundary_smoothness": {"enabled": False},
        "hu_distribution": {"enabled": False},
        "left_right_symmetry": {"enabled": False},
    },
    "review_sampling": {"batch_size": 6},
    "overall": {"hard_flag_rules": [], "min_score": 0.5},
}

ICFG = {"n_max_prompts": 15, "k_max_steps": 6}


# ---------------------------------------------------------------------------
# 分量单测
# ---------------------------------------------------------------------------


def test_w1_convergence_normal_class() -> None:
    """普通类：max(min(numPrompts/N_max,1), min(convergenceSteps/K_max,1))。"""
    im = {"liver": {"numPrompts": 5, "convergenceSteps": 4, "teacherReliability": "high"}}
    assert component_convergence(im, ICFG) == pytest.approx(0.6667, abs=1e-3)  # max(1/3, 4/6)
    im2 = {"liver": {"numPrompts": 20, "convergenceSteps": None, "teacherReliability": "high"}}
    assert component_convergence(im2, ICFG) == 1.0


def test_w1_convergence_high_risk_uses_prompts_only() -> None:
    """高风险类（teacherReliability=low）：只看 numPrompts（M6-b 同口径）。"""
    im = {"prostate": {"numPrompts": 5, "convergenceSteps": 9, "teacherReliability": "low"}}
    # 9 步 > k_max=6 但高风险类不看 steps → 5/15
    assert component_convergence(im, ICFG) == pytest.approx(1 / 3, abs=1e-3)


def test_w1_convergence_zero_thresholds_no_crash() -> None:
    """n_max/k_max=0（config 异常）→ 该项饱和 1.0，不抛 ZeroDivisionError。"""
    im = {"liver": {"numPrompts": 5, "convergenceSteps": 3, "teacherReliability": "high"}}
    assert component_convergence(im, {"n_max_prompts": 0, "k_max_steps": 0}) == 1.0
    im2 = {"prostate": {"numPrompts": 5, "teacherReliability": "low"}}
    assert component_convergence(im2, {"n_max_prompts": 0, "k_max_steps": 6}) == 1.0


def test_w1_w2_missing_interaction_metrics() -> None:
    """无 interactionMetrics → w1/w2 不可用（None，不造假）。"""
    assert component_convergence(None, ICFG) is None
    assert component_convergence({}, ICFG) is None
    assert component_abandonment(None) is None
    assert component_abandonment({}) is None


def test_w2_abandonment_any_class() -> None:
    """任一类 abandonedAttempts>=1 → 1，否则 0（M6-b 判据同形）。"""
    assert component_abandonment({"liver": {"abandonedAttempts": 0}}) == 0.0
    assert (
        component_abandonment(
            {"liver": {"abandonedAttempts": 0}, "aorta": {"abandonedAttempts": 2}}
        )
        == 1.0
    )
    # bool 显式排除（int 子类）
    assert component_abandonment({"liver": {"abandonedAttempts": True}}) == 0.0


def test_w3_disagreement_worst_class_not_mean() -> None:
    """最差类聚合：1 - min(逐类 Dice)——整类漏检（dice 0）顶到 1.0。"""
    rs = {
        "multi_teacher_consistency": {
            "per_class_dice": {"liver": 0.95, "prostate": 0.0, "aorta": 0.90},
            "mean_dice": 0.6167,
        }
    }
    w3, per_class = component_disagreement(rs)
    assert w3 == 1.0  # mean 0.6167 会稀释；最差类 = 1 - 0.0
    assert per_class["prostate"] == 0.0


def test_w3_unavailable_when_skipped() -> None:
    """规则 skipped（无学生 mask）→ w3 不可用。"""
    assert component_disagreement(
        {"multi_teacher_consistency": {"skipped": "no student mask"}}
    ) == (None, {})
    assert component_disagreement(None) == (None, {})
    assert component_disagreement({"multi_teacher_consistency": {"per_class_dice": {}}}) == (
        None,
        {},
    )


def test_w4_low_freq_presence_prostate() -> None:
    """参评类中低频类（+prostate 显式高风险）占比。"""
    counts = {"liver": 30, "prostate": 18, "rib_left_1": 9}
    # prostate 显式高风险（计数 18 不在 <10 名单但显式点名）+ rib_left_1 低频
    w4 = component_low_freq_presence(
        {"liver": 0.9, "prostate": 0.5, "rib_left_1": 0.7}, None, counts
    )
    assert w4 == pytest.approx(2 / 3)
    # 名单缺 → 不可用
    assert component_low_freq_presence({"liver": 0.9}, None, None) is None
    # 参评类集回退 interactionMetrics 键
    im = {"skull": {"numPrompts": 1}, "liver": {"numPrompts": 1}}
    w4b = component_low_freq_presence({}, im, {"skull": 5, "liver": 30})
    assert w4b == pytest.approx(0.5)


def test_w4_low_freq_threshold_equals_min_per_class() -> None:
    """低频阈值边界守卫：9 判低频、10 不判（阈值锁定 MIN_PER_CLASS 当前值 10）。"""
    from bodydonor.interaction_metrics import MIN_PER_CLASS

    assert MIN_PER_CLASS == 10
    # 9 < MIN_PER_CLASS → 低频；10 不 < → 非低频（行为零变化：阈值仍为 10）
    w4 = component_low_freq_presence(
        {"a": 0.9, "b": 0.9}, None, {"a": 9, "b": 10}
    )
    assert w4 == pytest.approx(0.5)


def test_plan_degradation_marks_availability() -> None:
    """降级：w3 缺失行 availability["w3"]=False + plan 记 w3_available=False。"""
    rows = [
        {"mask_version_id": "mv-a", "passed": True, "rule_scores": {}},
        {
            "mask_version_id": "mv-b",
            "passed": True,
            "rule_scores": {"multi_teacher_consistency": {"per_class_dice": {"liver": 0.5}}},
            "interaction_metrics": {
                "liver": {"numPrompts": 20, "teacherReliability": "high", "abandonedAttempts": 0}
            },
        },
    ]
    plan = sample_review_tasks_by_priority(
        rows, thresholds=MIN_THRESHOLDS, per_class_case_count={"liver": 30}
    )
    assert isinstance(plan, SamplingPlan)
    avail = plan.component_availability  # type: ignore[attr-defined]
    assert avail["w3_available"] is True  # mv-b 有 w3
    ranked = plan.ranked  # type: ignore[attr-defined]
    by_id = {r.mask_version_id: r for r in ranked}
    assert by_id["mv-a"].availability["w3"] is False
    assert by_id["mv-a"].components["w3"] is None
    # mv-b：w1=20/15→1.0，w3=1-0.5=0.5；mv-a 全 0 → mv-b 排第一
    assert plan.flagged_ids[0] == "mv-b"
    assert by_id["mv-a"].priority == 0.0


def test_plan_w3_fully_unavailable_note() -> None:
    """全部行 w3 不可用 → plan 记 w3_available=False + note（任务书 3）。"""
    rows = [{"mask_version_id": "mv-a", "passed": True, "rule_scores": {}}]
    plan = sample_review_tasks_by_priority(
        rows, thresholds=MIN_THRESHOLDS, per_class_case_count={"liver": 30}
    )
    avail = plan.component_availability  # type: ignore[attr-defined]
    assert avail["w3_available"] is False
    assert "w3 不可用" in avail["note"]


def test_plan_no_signal_sorts_last() -> None:
    """三来源全缺 → priority=0 排最后（无信号不冒充高风险）。"""
    rows = [
        {"mask_version_id": "mv-zero", "passed": True, "rule_scores": {}},
        {
            "mask_version_id": "mv-signal",
            "passed": True,
            "rule_scores": {"multi_teacher_consistency": {"per_class_dice": {"liver": 0.6}}},
        },
    ]
    plan = sample_review_tasks_by_priority(
        rows, thresholds=MIN_THRESHOLDS, per_class_case_count={"liver": 30}
    )
    assert plan.flagged_ids == ["mv-signal", "mv-zero"]


def test_plan_respects_batch_size() -> None:
    """batch_size 截断 + 全量 ranked 保留（排名顺序不丢）。"""
    rows = [
        {
            "mask_version_id": f"mv-{i}",
            "passed": True,
            "rule_scores": {
                "multi_teacher_consistency": {"per_class_dice": {"liver": 1.0 - i * 0.1}}
            },
        }
        for i in range(5)
    ]
    plan = sample_review_tasks_by_priority(
        rows, thresholds=MIN_THRESHOLDS, per_class_case_count={"liver": 30}
    )
    assert plan.n_total == 5  # batch_size 6 > 5 行
    # w3=1-min(逐类)：mv-4 逐类 min 0.6（分歧最大）排第一；全入选按 priority 降序
    assert plan.flagged_ids == ["mv-4", "mv-3", "mv-2", "mv-1", "mv-0"]
    th = dict(MIN_THRESHOLDS)
    th["review_sampling"] = {"batch_size": 2}
    plan2 = sample_review_tasks_by_priority(rows, thresholds=th, per_class_case_count={"liver": 30})
    assert plan2.n_total == 2
    assert plan2.flagged_ids == ["mv-4", "mv-3"]  # priority 降序前 2
    assert len(plan2.ranked) == 5  # type: ignore[attr-defined] — 全量排名保留


# ---------------------------------------------------------------------------
# generate_review_tasks 接线
# ---------------------------------------------------------------------------


def _seed_db(tmp_path: Path, n: int) -> Any:  # noqa: ANN401 — Settings 返回（懒导入避免模块级依赖）
    """临时库 + n 个 series（各一个 teacher MaskVersion + QCReport）。"""
    from bodydonor import db
    from bodydonor.config import Settings
    from bodydonor.db import init_db
    from bodydonor.models import Case, MaskVersion, QCReport, Series

    url = f"sqlite:///{tmp_path / 'm6c_wiring.db'}"
    init_db(url)
    s = Settings()
    s.database.url = url
    engine = db._engine(url)
    try:
        with engine.connect() as c:
            from sqlalchemy.orm import Session

            sess = Session(c)
            for i in range(n):
                cid, sid, mvid = f"case-{i}", f"series-{i}", f"mv-{i}"
                sess.add(Case(case_id=cid))
                sess.add(Series(series_id=sid, case_id=cid))
                sess.add(
                    MaskVersion(
                        mask_version_id=mvid, series_id=sid, source="teacher", storage_key=f"x/{i}"
                    )
                )
                sess.add(
                    QCReport(
                        qc_report_id=f"qc-{i}",
                        mask_version_id=mvid,
                        overall_score=1.0,
                        flags=[],
                        rule_scores={
                            "multi_teacher_consistency": {
                                "per_class_dice": {"liver": 1.0 - i * 0.2}
                            }
                        },
                        passed=True,
                    )
                )
            sess.commit()
            sess.close()
    finally:
        engine.dispose()
    return s


def test_generate_review_tasks_uses_priority_path(tmp_path: Path) -> None:
    """生产路径：sampling_priority.enabled → ranked/component_availability 进返回值。"""
    from bodydonor.qc_pipeline import generate_review_tasks

    s = _seed_db(tmp_path, 3)
    out = generate_review_tasks(s, thresholds=MIN_THRESHOLDS)
    assert "ranked" in out
    assert "component_availability" in out
    assert out["component_availability"]["w3_available"] is True
    # priority 降序：w3=1-min(per_class_dice)——mv-2 逐类 min 0.6（分歧最大）w3=0.4 >
    # mv-1 0.2 > mv-0 0.0
    assert [r["mask_version_id"] for r in out["ranked"]] == ["mv-2", "mv-1", "mv-0"]
    assert [t["mask_version_id"] for t in out["tasks"]] == ["mv-2", "mv-1", "mv-0"]


def test_generate_review_tasks_fallback_70_30(tmp_path: Path) -> None:
    """sampling_priority.enabled=false → 回退旧 70/30 路径（无 ranked 字段）。"""
    from bodydonor.qc_pipeline import generate_review_tasks

    th = json.loads(json.dumps(MIN_THRESHOLDS))
    th["sampling_priority"]["enabled"] = False
    th["review_sampling"] = {"batch_size": 2, "flagged_ratio": 0.70, "min_clean_pool": 1}
    s = _seed_db(tmp_path, 3)
    out = generate_review_tasks(s, thresholds=th)
    assert "ranked" not in out
    assert out["plan"]["n_total"] >= 1  # flagged 池空 → clean 兜底（passed=true 30% 路径）


# ---------------------------------------------------------------------------
# 排序一致性检验（30 例 hold-out：真实 w3/w4 + 合成 w1/w2）
# ---------------------------------------------------------------------------


def _teacher_volume_and_classes(case_id: str) -> tuple[np.ndarray, dict[int, str]] | None:
    """case_id -> (教师整图 arr, teacher_classes)；同 test_m6e_wiring.py 口径。"""
    import nibabel as nib

    data = Path.home() / "data/BodyDonor"
    ts_map = json.loads((data / "downloads/ct_lite/ts_class_map.json").read_text())
    if case_id.startswith("amos_"):
        teacher_dir = data / "downloads/amos22_subset" / case_id / "ts_out"
    else:
        teacher_dir = data / "downloads/ct_lite/ts_out" / case_id
    dataset_labels = set(
        json.loads((data / "student_holdout/pred/dataset.json").read_text(encoding="utf-8"))[
            "labels"
        ].keys()
    ) - {"background"}
    ref = None
    teacher_arr = None
    teacher_classes: dict[int, str] = {}
    for f in sorted(teacher_dir.glob("*.nii.gz")):
        name = f.name.removesuffix(".nii.gz")
        if name not in ts_map or name not in dataset_labels:
            continue
        img = nib.load(str(f))
        if ref is None:
            ref = img
            teacher_arr = np.zeros(img.shape, dtype=np.int16)
        teacher_arr[np.asarray(img.dataobj) > 0] = ts_map[name]
        teacher_classes[ts_map[name]] = name
    if teacher_arr is None:
        return None
    return teacher_arr, teacher_classes


def _real_products_available() -> bool:
    """真实产物齐全性（同 test_m6e_wiring.py 口径：教师+学生+低频名单）。"""
    data = Path.home() / "data/BodyDonor"
    paths = [
        data / "downloads/amos22_subset",
        data / "downloads/ct_lite/ts_class_map.json",
        data / "downloads/ct_lite/ts_out",
        data / "student_holdout/pred/dataset.json",
        REPO / "eval/holdout_results.json",
    ]
    if not all(p.exists() for p in paths):
        return False
    from bodydonor.student_pred import StudentPredictionError, load_student_prediction

    for case_id in _holdout_ids():
        if not (data / "student_holdout/pred" / f"{case_id}.nii.gz").exists():
            return False
        try:
            load_student_prediction(case_id)
        except StudentPredictionError:
            return False
    return True


def _holdout_ids() -> list[str]:
    return [
        line.split("\t")[0]
        for line in (REPO / "eval/holdout.txt").read_text().splitlines()
        if line.strip()
    ]


def _teacher_eval_counts() -> dict[str, int]:
    """117 类 per_class_cases（低频名单来源；缺文件时 skip 不 fail）。"""
    from bodydonor.interaction_metrics import load_teacher_eval

    counts = load_teacher_eval()
    assert counts is not None, "eval/holdout_results.json 缺失（低频名单不可用）"
    return counts


@pytest.mark.local_artifacts
@pytest.mark.skipif(
    not _real_products_available(),
    reason="~/data/BodyDonor 真实产物缺失（排序检验依赖 30 例 hold-out 教师+学生 mask）",
)
def test_ranking_consistency_30_holdout_top20() -> None:
    """排序一致性检验（验收）：s0209 与 prostate 漏检 case 进前 20%（前 6）。

    语料（任务书 5）：
    - 真实 w3：30 例 hold-out 教师+学生 mask 现算 rule_multi_teacher_consistency
      的逐类 Dice（不 hardcode 数字）。
    - 真实 w4：39/117 低频名单（eval/holdout_results.json per_class_cases，
      <10 cases；prostate 显式高风险）× w3 的逐类比较类集。
    - 合成 w1/w2：synthetic interactionMetrics，只用于覆盖 w1/w2 分量
      （hold-out case 无真实交互轨迹——interactionMetrics 采集始于 M6-a
      生产路径，hold-out 回放不产生）。合成值全 0：无过程信号的 case 不因
      合成分量获得优先级，排序纯由真实 w3/w4 驱动。
    """
    from bodydonor.qc import rule_multi_teacher_consistency
    from bodydonor.student_pred import load_student_prediction

    counts = _teacher_eval_counts()
    cfg = {"enabled": True, "threshold": 0.80}
    rows: list[dict] = []
    w3_values: dict[str, float] = {}
    prostate_miss: list[str] = []
    for case_id in _holdout_ids():
        t = _teacher_volume_and_classes(case_id)
        assert t is not None, f"{case_id}: 无教师输出"
        teacher_arr, teacher_classes = t
        pred = load_student_prediction(case_id)
        _flagged, _score, detail = rule_multi_teacher_consistency(
            teacher_arr, np.asarray(pred.mask_arr), teacher_classes, pred.classes, cfg
        )
        rule_scores = {"multi_teacher_consistency": detail}
        pcd = detail.get("per_class_dice", {})
        w3_values[case_id] = 1.0 - min(pcd.values()) if pcd else 0.0
        # prostate 整类漏检指认（deep-reviewer 修正）：直接数教师侧体素——
        # 教师有 prostate 标签但体素 0 且学生侧非空（排除学生侧漏检的镜像情形，
        # per_class_dice 只算两边任一非空的类，dice=0 不区分哪边空）
        pv = next((v for v, n in teacher_classes.items() if n == "prostate"), None)
        if pv is not None:
            teacher_p_vox = int((teacher_arr == pv).sum())
            sv = next((k for k, v in pred.classes.items() if v == "prostate"), None)
            student_p_vox = int((np.asarray(pred.mask_arr) == sv).sum()) if sv else 0
            if teacher_p_vox == 0 and student_p_vox > 0:
                prostate_miss.append(case_id)
        rows.append(
            {
                "mask_version_id": case_id,
                "passed": True,
                "rule_scores": rule_scores,
                # synthetic：hold-out 无真实交互轨迹，w1/w2 置 0（无过程信号）
                "interaction_metrics": {},
            }
        )

    plan = sample_review_tasks_by_priority(
        rows, thresholds=MIN_THRESHOLDS, per_class_case_count=counts
    )
    ranked = plan.ranked  # type: ignore[attr-defined]
    rank_by_id = {r.mask_version_id: i + 1 for i, r in enumerate(ranked)}
    top6 = plan.flagged_ids[:6]

    # 硬性验收：s0209（case 级 mean 0.1181，w3≈1.0）与 prostate 漏检 case 进前 6
    assert "s0209" in top6, f"s0209 排名 {rank_by_id.get('s0209')} 不在前 6：{top6}"
    assert prostate_miss, "数据里未指认出教师整类漏检 prostate 的 case（检查教师 ts_out）"
    for case_id in prostate_miss:
        assert case_id in top6, (
            f"prostate 漏检 case {case_id} 排名 {rank_by_id.get(case_id)} 不在前 6：{top6}"
        )
    # s0209 的 w3 是最高档（逐类 min=0 → w3=1.0）。等权下 5 个 w4 加成的 case
    # （参评类集含 prostate：amos_0004/amos_0021/s0495 1.0667、amos_0016 1.0657、
    # amos_0005 1.0079——prostate 分歧 + gallbladder 整类漏检，语义正确）排在其前，
    # s0209 排第 6 = 前 20% 边界内（硬性验收即前 6，不再加更严的个人断言）。
    assert rank_by_id["s0209"] <= 6, f"s0209 排名 {rank_by_id['s0209']}（应在前 20%）"

    # 报告数字：前 10 名 + s0209/prostate case 的分量值
    report_rows = [
        {
            "rank": i + 1,
            "case_id": r.mask_version_id,
            "priority": round(r.priority, 4),
            **{
                k: (None if r.components[k] is None else round(r.components[k], 4))
                for k in ("w1", "w2", "w3", "w4")
            },
        }
        for i, r in enumerate(ranked[:10])
    ]
    print("\nM6-C 排名表（前 10）:", json.dumps(report_rows, ensure_ascii=False, indent=1))
    for cid in ("s0209", *prostate_miss):
        r = next(r for r in ranked if r.mask_version_id == cid)
        print(
            f"{cid}: rank={rank_by_id[cid]} priority={r.priority:.4f} "
            f"w3={r.components['w3']:.4f} w4={r.components['w4']:.4f}"
        )
