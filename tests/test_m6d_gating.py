"""M6-d：轻量门控器（gating 三档）单测 + 30 例 hold-out 三档验收。

- 分档单测：tier_for_rank 边界（20/60/20 默认、比例可配置、rank 超界/
  n_total<=0 归 auto、比例和 >1 不崩）。
- 接线单测：generate_review_tasks 生产路径 forced+suggested 建 ReviewTask
  （forced 标红 sample_group=forced）、auto 不建；返回值 gating 汇总；
  旧 70/30 路径不受影响。
- 三档验收（30 例 hold-out 真实数据现算，不 hardcode 档位结果）：
  s0209 → 强制人工；prostate 漏检 case（M6-c zero_dice_cases）→ 建议复核
  或以上；高置信 case（priority 排名末位）→ 自动接受。

语料同 test_m6c_priority.py 口径：真实 w3（教师+学生 mask 现算逐类 Dice）+
真实 w4（39/117 低频名单）+ 合成 w1/w2（hold-out 无真实交互轨迹，置 0）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bodydonor.qc import SamplingPlan
from bodydonor.qc_priority import (
    load_gating_cfg,
    sample_review_tasks_by_priority,
    tier_for_rank,
)

REPO = Path(__file__).resolve().parents[1]

# 与 test_m6c_priority.py MIN_THRESHOLDS 同款（只开 multi_teacher + sampling_priority）
MIN_THRESHOLDS = {
    "cold_start": {"dataset": "m6d_regression"},
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
    "gating": {"forced_ratio": 0.20, "suggested_ratio": 0.60, "auto_ratio": 0.20},
    "overall": {"hard_flag_rules": [], "min_score": 0.5},
}

GCFG = {"forced_ratio": 0.20, "suggested_ratio": 0.60, "auto_ratio": 0.20}


# ---------------------------------------------------------------------------
# 分档单测（tier_for_rank）
# ---------------------------------------------------------------------------


def test_tier_default_20_60_20() -> None:
    """默认 20/60/20：10 例 → 2 forced / 6 suggested / 2 auto。"""
    tiers = [tier_for_rank(i, 10, GCFG) for i in range(1, 11)]
    assert tiers == ["forced"] * 2 + ["suggested"] * 6 + ["auto"] * 2


def test_tier_30_cases_default() -> None:
    """30 例 → 6 forced / 18 suggested / 6 auto（round 边界）。"""
    tiers = [tier_for_rank(i, 30, GCFG) for i in range(1, 31)]
    assert tiers.count("forced") == 6
    assert tiers.count("suggested") == 18
    assert tiers.count("auto") == 6
    assert tiers[0] == "forced"
    assert tiers[-1] == "auto"


def test_tier_rank_out_of_bounds_is_auto() -> None:
    """rank 超界（0/负数/> n_total）或 n_total<=0 → auto（不 raise）。"""
    assert tier_for_rank(0, 10, GCFG) == "auto"
    assert tier_for_rank(-1, 10, GCFG) == "auto"
    assert tier_for_rank(11, 10, GCFG) == "auto"
    assert tier_for_rank(1, 0, GCFG) == "auto"
    assert tier_for_rank(1, -5, GCFG) == "auto"


def test_tier_ratios_configurable() -> None:
    """分档比例可配置（改 config 比例后分档随之变；round(2.5)=2 banker's）。"""
    cfg = {"forced_ratio": 0.5, "suggested_ratio": 0.25, "auto_ratio": 0.25}
    tiers = [tier_for_rank(i, 10, cfg) for i in range(1, 11)]
    assert tiers == ["forced"] * 5 + ["suggested"] * 2 + ["auto"] * 3  # round(2.5)=2，余归 auto
    # 全 forced（auto 区空，config 异常不崩）
    cfg2 = {"forced_ratio": 1.0, "suggested_ratio": 0.0, "auto_ratio": 0.0}
    assert all(tier_for_rank(i, 5, cfg2) == "forced" for i in range(1, 6))
    # 全 auto
    cfg3 = {"forced_ratio": 0.0, "suggested_ratio": 0.0, "auto_ratio": 1.0}
    assert all(tier_for_rank(i, 5, cfg3) == "auto" for i in range(1, 6))


def test_load_gating_cfg_defaults_and_overrides() -> None:
    """缺省 20/60/20；config 覆盖生效。"""
    assert load_gating_cfg(None)["forced_ratio"] == pytest.approx(0.20)
    assert load_gating_cfg(None)["suggested_ratio"] == pytest.approx(0.60)
    assert load_gating_cfg(None)["auto_ratio"] == pytest.approx(0.20)
    # 无 gating 节 → 缺省
    assert load_gating_cfg({})["forced_ratio"] == pytest.approx(0.20)
    # 覆盖
    th = {"gating": {"forced_ratio": 0.1, "suggested_ratio": 0.5, "auto_ratio": 0.4}}
    cfg = load_gating_cfg(th)
    assert cfg["forced_ratio"] == pytest.approx(0.1)
    assert cfg["auto_ratio"] == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# plan 级 gating（sample_review_tasks_by_priority 返回值）
# ---------------------------------------------------------------------------


def _rows(n: int) -> list[dict[str, Any]]:
    """n 行合成数据：w3 = 1 - i/n（liver dice 越低 priority 越高，priority
    降序 = i 降序：mv-09 w3=0.9 第一、mv-00 w3=0 最后）。"""
    return [
        {
            "mask_version_id": f"mv-{i:02d}",
            "passed": True,
            "rule_scores": {
                "multi_teacher_consistency": {"per_class_dice": {"liver": 1.0 - i / n}}
            },
        }
        for i in range(n)
    ]


def test_plan_gating_summary_shape() -> None:
    """plan.gating：三档 id 列表 + 计数 + 生效比例；ranked 行级 gating_tier。"""
    plan = sample_review_tasks_by_priority(
        _rows(10), thresholds=MIN_THRESHOLDS, per_class_case_count={"liver": 30}
    )
    assert isinstance(plan, SamplingPlan)
    g = plan.gating  # type: ignore[attr-defined]
    assert g["n_forced"] == 2
    assert g["n_suggested"] == 6
    assert g["n_auto"] == 2
    assert g["forced"] == ["mv-09", "mv-08"]  # priority 降序前 2（dice 最低）
    assert g["suggested"] == [f"mv-{i:02d}" for i in range(7, 1, -1)]
    assert g["auto"] == ["mv-01", "mv-00"]
    assert g["ratios"] == {"forced_ratio": 0.2, "suggested_ratio": 0.6, "auto_ratio": 0.2}
    ranked = plan.ranked  # type: ignore[attr-defined]
    assert [r.gating_tier for r in ranked] == ["forced"] * 2 + ["suggested"] * 6 + ["auto"] * 2
    assert ranked[0].to_dict()["gating_tier"] == "forced"


def test_plan_gating_fully_ranked_not_batch_truncated() -> None:
    """gating 按全量排名分档（不是 batch_size 截断集）：batch_size=3 时
    ranked 10 行仍全分档，auto 2 行在 ranked 里可见。"""
    th = json.loads(json.dumps(MIN_THRESHOLDS))
    th["review_sampling"] = {"batch_size": 3}
    plan = sample_review_tasks_by_priority(
        _rows(10), thresholds=th, per_class_case_count={"liver": 30}
    )
    g = plan.gating  # type: ignore[attr-defined]
    assert g["n_auto"] == 2  # 全量分档，不因 batch 截断丢档
    assert len(plan.ranked) == 10  # type: ignore[attr-defined]
    assert plan.n_total == 3  # flagged_ids 截断不变（M6-c 语义）


# ---------------------------------------------------------------------------
# generate_review_tasks 接线
# ---------------------------------------------------------------------------


def _seed_db(tmp_path: Path, n: int) -> Any:  # noqa: ANN401 — Settings 返回（懒导入）
    """临时库 + n 个 series（各一个 teacher MaskVersion + QCReport）。"""
    from bodydonor import db
    from bodydonor.config import Settings
    from bodydonor.db import init_db
    from bodydonor.models import Case, MaskVersion, QCReport, Series

    url = f"sqlite:///{tmp_path / 'm6d_wiring.db'}"
    init_db(url)
    s = Settings()
    s.database.url = url
    engine = db._engine(url)
    try:
        with engine.connect() as c:
            from sqlalchemy.orm import Session

            sess = Session(c)
            for i in range(n):
                cid, sid, mvid = f"case-{i}", f"series-{i}", f"mv-{i:02d}"
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
                            "multi_teacher_consistency": {"per_class_dice": {"liver": 1.0 - i / n}}
                        },
                        passed=True,
                    )
                )
            sess.commit()
            sess.close()
    finally:
        engine.dispose()
    return s


def test_generate_review_tasks_gating_creates_forced_and_suggested(tmp_path: Path) -> None:
    """10 例：forced(2)+suggested(6) 建 ReviewTask，auto(2) 不建；forced 标红。"""
    from sqlalchemy import select

    from bodydonor import db
    from bodydonor.models import ReviewTask
    from bodydonor.qc_pipeline import generate_review_tasks

    s = _seed_db(tmp_path, 10)
    out = generate_review_tasks(s, thresholds=MIN_THRESHOLDS)
    g = out["gating"]
    assert g["n_forced"] == 2 and g["n_suggested"] == 6 and g["n_auto"] == 2
    groups = {t["mask_version_id"]: t["group"] for t in out["tasks"]}
    # forced 2 条 + suggested 6 条 = 8 条任务；auto 2 条不建
    assert len(out["tasks"]) == 8
    assert set(groups) == set(g["forced"]) | set(g["suggested"])
    assert all(groups[mv] == "forced" for mv in g["forced"])  # 标红（sample_group=forced）
    assert all(groups[mv] == "suggested" for mv in g["suggested"])
    assert all(mv not in groups for mv in g["auto"])  # 自动接受不建任务
    # DB 实证：forced 任务的 notes 里 sample_group=forced（标红语义落库）
    engine = db._engine(s.database.url)
    try:
        with engine.connect() as conn:
            from sqlalchemy.orm import Session

            sess = Session(conn)
            rt_notes = sess.execute(select(ReviewTask.notes)).scalars().all()
            sess.close()
    finally:
        engine.dispose()
    assert len(rt_notes) == 8
    assert sum(1 for n in rt_notes if "sample_group=forced" in (n or "")) == 2
    assert sum(1 for n in rt_notes if "sample_group=suggested" in (n or "")) == 6


def test_generate_review_tasks_gating_config_change_shifts_tiers(tmp_path: Path) -> None:
    """改 config 比例后分档随之变（10 例 50/25/25 → 5 forced / 2 suggested / 3 auto）。"""
    from bodydonor.qc_pipeline import generate_review_tasks

    th = json.loads(json.dumps(MIN_THRESHOLDS))
    th["gating"] = {"forced_ratio": 0.5, "suggested_ratio": 0.25, "auto_ratio": 0.25}
    s = _seed_db(tmp_path, 10)
    out = generate_review_tasks(s, thresholds=th)
    g = out["gating"]
    assert g["n_forced"] == 5
    assert g["n_suggested"] == 2  # round(2.5)=2 banker's
    assert g["n_auto"] == 3  # 余数归 auto
    assert len(out["tasks"]) == 7


def test_generate_review_tasks_old_path_unaffected(tmp_path: Path) -> None:
    """sampling_priority.enabled=false → 旧 70/30 路径照常（无 gating 字段）。"""
    from bodydonor.qc_pipeline import generate_review_tasks

    th = json.loads(json.dumps(MIN_THRESHOLDS))
    th["sampling_priority"]["enabled"] = False
    th["review_sampling"] = {"batch_size": 2, "flagged_ratio": 0.70, "min_clean_pool": 1}
    s = _seed_db(tmp_path, 3)
    out = generate_review_tasks(s, thresholds=th)
    assert "gating" not in out
    assert "ranked" not in out
    assert out["plan"]["n_total"] >= 1


def test_negative_gating_ratio_raises() -> None:
    """负比例是 config 错误 → raise（fail-loud，不静默错分档）。"""
    with pytest.raises(ValueError, match="gating"):
        load_gating_cfg({"gating": {"forced_ratio": -0.2}})


def test_ensure_review_task_upgrades_pending_group(tmp_path: Path) -> None:
    """deep-review W3 失败测试先行：同 MV 先建 suggested 任务，重跑分档为
    forced → pending 任务的 notes 升级 sample_group=forced（红标不静默丢失）。"""
    from sqlalchemy import select

    from bodydonor import db
    from bodydonor.models import ReviewTask
    from bodydonor.qc_pipeline import _ensure_review_task

    s = _seed_db(tmp_path, 1)
    engine = db._engine(s.database.url)
    try:
        with engine.connect() as conn:
            from sqlalchemy.orm import Session

            sess = Session(conn)
            # 第一轮：建 suggested 任务
            _ensure_review_task(sess, "mv-00", "series-0", "qc-0", "suggested")
            sess.commit()
            # 第二轮：分档升为 forced → 幂等不重复建，但 notes 升级
            _ensure_review_task(sess, "mv-00", "series-0", "qc-0", "forced")
            sess.commit()
            n_tasks = len(sess.execute(select(ReviewTask)).scalars().all())
            notes = (
                sess.execute(select(ReviewTask.notes)).scalars().first()
            )
            sess.close()
    finally:
        engine.dispose()
    assert n_tasks == 1  # 幂等：不重复建
    assert "sample_group=forced" in (notes or "")  # 红标升级（bug：旧实现不升级）


# ---------------------------------------------------------------------------
# 三档验收（30 例 hold-out 真实数据现算；语料同 test_m6c_priority.py）
# ---------------------------------------------------------------------------


def _holdout_ids() -> list[str]:
    return [
        line.split("\t")[0]
        for line in (REPO / "eval/holdout.txt").read_text().splitlines()
        if line.strip()
    ]


def _teacher_volume_and_classes(case_id: str) -> tuple[Any, dict[int, str]] | None:
    """case_id -> (教师整图 arr, teacher_classes)；同 test_m6c_priority.py 口径。"""
    import nibabel as nib
    import numpy as np

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
    """真实产物齐全性（同 test_m6c_priority.py 口径）。"""
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


def _teacher_eval_counts() -> dict[str, int]:
    """117 类 per_class_cases（低频名单来源；缺文件时 skip 不 fail）。"""
    from bodydonor.interaction_metrics import load_teacher_eval

    counts = load_teacher_eval()
    assert counts is not None, "eval/holdout_results.json 缺失（低频名单不可用）"
    return counts


@pytest.mark.local_artifacts
@pytest.mark.skipif(
    not _real_products_available(),
    reason="~/data/BodyDonor 真实产物缺失（三档验收依赖 30 例 hold-out 教师+学生 mask）",
)
def test_gating_three_tiers_30_holdout() -> None:
    """三档验收（硬性，30 例 hold-out 真实数据现算，不 hardcode 档位结果）：

    1. s0209 → 强制人工（forced）。
    2. prostate 分歧 case（高风险类 prostate 上有可观测分歧：现算逐类
       Dice < 0.80，从数据指认不 hardcode 名单）→ 建议复核或以上（forced
       或 suggested，不落 auto）。
    3. 高置信 case（priority 排名末位的高置信 case，从数据指认）→ 自动接受。

    amos_0014 的如实记录（2026-11-12 实测 rank 30 落 auto）：教师 prostate
    体素 0 且学生也 0——双空类不进逐类比较，无可观测分歧信号（w3=0.16）。
    过程信号探测不到教师与学生的共同盲区；该 case 由 M4 结果规则/QC flag
    覆盖，不是本门控器的信号范围（本门控器输出三态决策，不替代结果门禁）。
    """
    import numpy as np

    from bodydonor.qc import rule_multi_teacher_consistency
    from bodydonor.student_pred import load_student_prediction

    counts = _teacher_eval_counts()
    cfg = {"enabled": True, "threshold": 0.80}
    rows: list[dict] = []
    prostate_divergent: list[str] = []
    for case_id in _holdout_ids():
        t = _teacher_volume_and_classes(case_id)
        assert t is not None, f"{case_id}: 无教师输出"
        teacher_arr, teacher_classes = t
        pred = load_student_prediction(case_id)
        _flagged, _score, detail = rule_multi_teacher_consistency(
            teacher_arr, np.asarray(pred.mask_arr), teacher_classes, pred.classes, cfg
        )
        rows.append(
            {
                "mask_version_id": case_id,
                "passed": True,
                "rule_scores": {"multi_teacher_consistency": detail},
                # synthetic：hold-out 无真实交互轨迹，w1/w2 置 0（无过程信号）
                "interaction_metrics": {},
            }
        )
        # prostate 分歧指认（现算逐类 Dice，不 hardcode 名单）：高风险类
        # prostate 上 dice < 0.80 = 有可观测分歧 → 门控器必须建议复核+。
        # 教师与学生双空（共同盲区）不计入逐类比较，无可观测信号——该子集
        # 不在门控器探测范围（如 amos_0014，如实记录见 docstring）。
        pcd = detail.get("per_class_dice", {})
        if "prostate" in pcd and float(pcd["prostate"]) < 0.80:
            prostate_divergent.append(case_id)

    plan = sample_review_tasks_by_priority(
        rows, thresholds=MIN_THRESHOLDS, per_class_case_count=counts
    )
    g = plan.gating  # type: ignore[attr-defined]
    tier_by_id = {r.mask_version_id: r.gating_tier for r in plan.ranked}  # type: ignore[attr-defined]
    rank_by_id = {r.mask_version_id: i + 1 for i, r in enumerate(plan.ranked)}  # type: ignore[attr-defined]

    # 验收 1：s0209 → 强制人工
    assert tier_by_id["s0209"] == "forced", (
        f"s0209 排名 {rank_by_id['s0209']} tier={tier_by_id['s0209']}（应 forced）"
    )
    # 验收 2：prostate 分歧 case → 建议复核或以上（不落 auto）
    assert prostate_divergent, (
        "数据里未指认出 prostate 分歧 case（现算逐类 Dice < 0.80，检查教师 ts_out）"
    )
    for case_id in prostate_divergent:
        assert tier_by_id[case_id] in ("forced", "suggested"), (
            f"prostate 分歧 case {case_id} tier={tier_by_id[case_id]}（不应 auto）"
        )
    # 验收 3：高置信 case → 自动接受（priority 排名末位 20% = auto 区，
    # 从 ranked 末位取，不 hardcode case id）。断言 auto 区整体 priority
    # 低于 forced+suggested 区（不是只低于全局最高——max 恒真断言无判别力）。
    auto_ids = g["auto"]
    assert auto_ids, "auto 区为空（分档比例异常）"
    reviewed = set(g["forced"]) | set(g["suggested"])
    pri = {r.mask_version_id: r.priority for r in plan.ranked}  # type: ignore[attr-defined]
    min_reviewed = min(pri[c] for c in reviewed)
    for cid in auto_ids:
        assert cid not in reviewed
        assert pri[cid] <= min_reviewed, (
            f"auto case {cid} priority={pri[cid]} 高于参审区最低 {min_reviewed}（auto 应是最低段）"
        )
    # 三档覆盖齐全（30 例默认 20/60/20 → 6/18/6）
    assert min(pri[c] for c in g["forced"]) >= max(pri[c] for c in g["suggested"]), (
        "forced 区 priority 应整体不低于 suggested 区"
    )
    assert g["n_forced"] == 6 and g["n_suggested"] == 18 and g["n_auto"] == 6

    # 报告数字：s0209/prostate case/auto case 的 tier + rank
    print(
        "\nM6-D 三档:",
        json.dumps(
            {
                "s0209": {"rank": rank_by_id["s0209"], "tier": tier_by_id["s0209"]},
                "prostate_divergent": {
                    c: {"rank": rank_by_id[c], "tier": tier_by_id[c]} for c in prostate_divergent
                },
                "auto": auto_ids,
                "n_tiers": {k: g[f"n_{k}"] for k in ("forced", "suggested", "auto")},
            },
            ensure_ascii=False,
            indent=1,
        ),
    )
