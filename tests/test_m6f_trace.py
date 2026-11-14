"""M6-f：DecisionTrace 单测 + 接线 + rules_triggered 对比 + trace-back 验收。

- schema 单测：record_qc_trace / record_gate_trace 构造形状（pipeline_stage、
  rules_triggered=flags、human_outcome 恒 null、calibration 快照）。
- 降级单测：无 interactionMetrics（coverage=False + skipped 原文）、无
  priority 路径（ranked_row=None → 不硬造分量值）、human_outcome null。
- update_human_outcome：补填 + 幂等不覆盖（append/update 语义）。
- 接线单测：qc_series 同一事务记 qc_scoring trace；generate_review_tasks
  全量 ranked 各记 gate_decision trace（auto 也在内）；旧 70/30 路径也记。
- 验收 b：rules_triggered 与 QCReport.flags 逐项对比（两条 trace 各自与
  落库 flags 逐一相等）。
- 验收 a：任一 case 从 trace 单独回溯全链（inputs → signals → rules →
  tier → human_outcome）。
- 真实 hold-out 验收：30 例现算（真实 w3+w4，w1/w2 置 0 如实标注），
  trace-back 具体案例。
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bodydonor.models import DecisionTrace, QCReport
from bodydonor.qc_priority import PriorityRow
from bodydonor.qc_trace import (
    record_gate_trace,
    record_qc_trace,
    snapshot_calibration,
    update_human_outcome,
)

REPO = Path(__file__).resolve().parents[1]

# 与 test_m6d_gating.py MIN_THRESHOLDS 同款（只开 multi_teacher + sampling_priority）
MIN_THRESHOLDS = {
    "cold_start": {"dataset": "m6f_regression"},
    "sampling_priority": {
        "enabled": True,
        "source": "cold_start",
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


# ---------------------------------------------------------------------------
# schema 单测（record_qc_trace / record_gate_trace / snapshot_calibration）
# ---------------------------------------------------------------------------


def test_snapshot_calibration_defaults_and_overrides() -> None:
    """缺省与冷启动声明一致；config 覆盖生效（M7 对照基准）。"""
    snap = snapshot_calibration({})
    assert snap["priority_weights"] == {"w1": 1.0, "w2": 1.0, "w3": 1.0, "w4": 1.0}
    assert snap["gating_ratios"] == {
        "forced_ratio": 0.20,
        "suggested_ratio": 0.60,
        "auto_ratio": 0.20,
    }
    assert snap["source"] == {"sampling_priority": "cold_start", "gating": "cold_start"}
    snap2 = snapshot_calibration(MIN_THRESHOLDS)
    assert snap2["priority_weights"] == {"w1": 1.0, "w2": 1.0, "w3": 1.0, "w4": 1.0}
    assert snap2["source"]["sampling_priority"] == "cold_start"
    # 覆盖：改权重 = config diff，快照随之变
    th = {
        "sampling_priority": {"weights": {"w1": 2.0}, "source": "calibrated_on_42"},
        "gating": {"forced_ratio": 0.5},
    }
    snap3 = snapshot_calibration(th)
    assert snap3["priority_weights"]["w1"] == 2.0
    assert snap3["gating_ratios"]["forced_ratio"] == 0.5
    assert snap3["source"]["sampling_priority"] == "calibrated_on_42"


def _qc_kwargs() -> dict[str, Any]:
    """qc_scoring trace 的公共入参（rule_scores 含 M6-b 两条规则的原文）。"""
    return {
        "case_id": "case-x",
        "series_id": "series-x",
        "mask_version_id": "mv-x",
        "qc_report_id": "qc-x",
        "flags": ["interaction_abandonment"],
        "rule_scores": {
            "multi_teacher_consistency": {"skipped": "no student mask"},
            "interaction_convergence": {
                "flagged": False,
                "flagged_classes": [],
                "worst_num_prompts": 3,
            },
            "interaction_abandonment": {
                "flagged": True,
                "flagged_classes": ["liver"],
                "worst_abandoned_attempts": 2,
            },
        },
        "interaction_metrics": {"liver": {"numPrompts": 3, "abandonedAttempts": 2}},
        "student_source": "no case_id",
        "thresholds": MIN_THRESHOLDS,
    }


def test_record_qc_trace_shape() -> None:
    """qc_scoring trace：stage/rules_triggered=flags/human_outcome null/calibration。"""
    t = record_qc_trace(**_qc_kwargs())
    assert isinstance(t, DecisionTrace)
    assert t.pipeline_stage == "qc_scoring"
    assert t.case_id == "case-x" and t.series_id == "series-x"
    assert t.mask_version_id == "mv-x"
    assert t.rules_triggered == ["interaction_abandonment"]
    assert t.human_outcome is None  # 尚无人工决策，不造假
    assert t.gate_decision is None  # 门禁决策未发生
    assert t.input_refs == {
        "mask_version_id": "mv-x",
        "qc_report_id": "qc-x",
        "student_source": "no case_id",
    }
    # process_signals：M6-b 规则信号 + 交互覆盖
    sig = t.process_signals
    assert sig["interaction_coverage"] is True
    assert sig["interaction_abandonment"]["flagged"] is True
    assert sig["interaction_abandonment"]["detail"]["flagged_classes"] == ["liver"]
    assert sig["interaction_convergence"]["flagged"] is False
    assert t.calibration["priority_weights"] == {"w1": 1.0, "w2": 1.0, "w3": 1.0, "w4": 1.0}
    assert t.calibration["gating_ratios"]["forced_ratio"] == 0.20
    assert t.calibration["source"]["gating"] == "cold_start"


def test_record_qc_trace_no_interaction_degradation() -> None:
    """纯自动 case：coverage=False + skipped 原文（不硬造信号）。"""
    kw = _qc_kwargs()
    kw["interaction_metrics"] = None
    kw["flags"] = []  # 纯自动 case 无交互 flag（QCReport.flags 同为空）
    kw["rule_scores"] = {
        "interaction_convergence": {"skipped": "no interaction metrics"},
        "interaction_abandonment": {"skipped": "no interaction metrics"},
    }
    t = record_qc_trace(**kw)
    sig = t.process_signals
    assert sig["interaction_coverage"] is False
    assert sig["interaction_convergence"]["detail"] == {"skipped": "no interaction metrics"}
    assert sig["interaction_convergence"]["flagged"] is False
    assert "interaction_abandonment" not in t.rules_triggered


def test_record_qc_trace_no_case_id() -> None:
    """case_id 未给出（生产 qc 阶段常态）→ null，不硬造。"""
    kw = _qc_kwargs()
    kw["case_id"] = None
    t = record_qc_trace(**kw)
    assert t.case_id is None


def test_record_gate_trace_shape() -> None:
    """gate_decision trace：tier + ReviewTask 生成 + M6-c priority 信号。"""
    row = PriorityRow(
        mask_version_id="mv-x",
        qc_report_id="qc-x",
        priority=2.5,
        components={"w1": 0.5, "w2": 1.0, "w3": None, "w4": 0.25},
        availability={"w1": True, "w2": True, "w3": False, "w4": True},
        gating_tier="forced",
    )
    t = record_gate_trace(
        case_id="case-x",
        series_id="series-x",
        mask_version_id="mv-x",
        qc_report_id="qc-x",
        flags=[],
        rule_scores={"multi_teacher_consistency": {"skipped": "no student mask"}},
        interaction_metrics={},
        ranked_row=row,
        component_availability={
            "w1": 3, "w2": 3, "w3": 0, "w4": 3, "w3_available": False, "note": "w3 不可用",
        },
        tier="forced",
        review_task_id="rt-x",
        sample_group="forced",
        thresholds=MIN_THRESHOLDS,
    )
    assert t.pipeline_stage == "gate_decision"
    assert t.gate_decision == {
        "tier": "forced",
        "review_task_generated": True,
        "review_task_id": "rt-x",
        "sample_group": "forced",
    }
    assert t.review_task_id == "rt-x"
    assert t.rules_triggered == []
    assert t.human_outcome is None
    # M6-c priority 信号（w1–w4 + availability + w3_available）
    sig = t.process_signals
    assert sig["priority"] == 2.5
    assert sig["components"]["w3"] is None
    assert sig["availability"]["w3"] is False
    assert sig["w3_available"] is False
    assert "w3 不可用" in sig["note"]
    assert sig["interaction_coverage"] is False  # 纯自动 case 如实记录
    assert t.calibration["gating_ratios"]["forced_ratio"] == 0.20


def test_record_gate_trace_auto_no_task() -> None:
    """tier=auto：不生成 ReviewTask（M6-d 语义）→ review_task_generated=False。"""
    row = PriorityRow(mask_version_id="mv-x", priority=0.0, gating_tier="auto")
    t = record_gate_trace(
        case_id="case-x",
        series_id="series-x",
        mask_version_id="mv-x",
        qc_report_id="qc-x",
        flags=[],
        rule_scores={},
        interaction_metrics=None,
        ranked_row=row,
        component_availability={},
        tier="auto",
        review_task_id=None,
        sample_group=None,
        thresholds=MIN_THRESHOLDS,
    )
    assert t.gate_decision["tier"] == "auto"
    assert t.gate_decision["review_task_generated"] is False
    assert "review_task_id" not in t.gate_decision
    assert t.review_task_id is None


def test_update_human_outcome_fill_and_idempotent() -> None:
    """补填 human_outcome；已补填的不覆盖（幂等，历史不改写）。"""
    t = record_qc_trace(**_qc_kwargs())
    assert t.human_outcome is None
    update_human_outcome(t, "accepted", notes="ok")
    assert t.human_outcome["decision"] == "accepted"
    assert t.human_outcome["notes"] == "ok"
    assert "decided_at" in t.human_outcome
    # 重放决策：不覆盖首次决策（历史不改写）
    update_human_outcome(t, "rejected", notes="second")
    assert t.human_outcome["decision"] == "accepted"


# ---------------------------------------------------------------------------
# 接线单测（qc_series / generate_review_tasks 产 trace）
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> Any:
    from bodydonor.config import Settings
    from bodydonor.db import init_db

    url = f"sqlite:///{tmp_path / 'm6f_trace.db'}"
    init_db(url)
    s = Settings()
    s.database.url = url
    return s


def _seed_mask_payload(tmp_path: Path, settings: Any, arr: np.ndarray) -> str:
    """mask + labels.json 写盘 + DB 种子（test_m6e_wiring.py 同款）。"""
    import nibabel as nib
    from sqlalchemy.orm import Session

    from bodydonor import db
    from bodydonor.models import Case, MaskVersion, Series
    from bodydonor.storage import LocalFsStorage

    engine = db._engine(settings.database.url)
    try:
        with engine.connect() as c:
            sess = Session(c)
            sess.add(Case(case_id="case-wired"))
            sess.add(Series(series_id="series-wired", case_id="case-wired"))
            sess.add(
                MaskVersion(
                    mask_version_id="mv-wired",
                    series_id="series-wired",
                    source="teacher",
                    storage_key="case-wired/series-wired/mask.nii.gz",
                )
            )
            sess.commit()
            sess.close()
    finally:
        engine.dispose()
    st = LocalFsStorage(settings.resolved_data_dir / "storage")
    p = tmp_path / "seed_mask.nii.gz"
    nib.save(nib.Nifti1Image(arr, np.eye(4)), str(p))
    st.save("case-wired/series-wired/mask.nii.gz", p.read_bytes())
    st.save(
        "case-wired/series-wired/labels.json",
        json.dumps({"labels": [{"index": 3, "totalseg_class": "aorta"}]}).encode(),
    )
    return "series-wired"


def _traces(settings: Any) -> list[DecisionTrace]:
    from sqlalchemy.orm import Session

    from bodydonor import db

    engine = db._engine(settings.database.url)
    try:
        with engine.connect() as c:
            sess = Session(c)
            rows = (
                sess.query(DecisionTrace)
                .order_by(DecisionTrace.pipeline_stage, DecisionTrace.timestamp)
                .all()
            )
            sess.expunge_all()
            sess.close()
    finally:
        engine.dispose()
    return rows


def test_qc_series_writes_qc_scoring_trace(tmp_path: Path) -> None:
    """qc_series：QCReport 与 qc_scoring trace 同一事务落库（决策在哪发生就在哪记）。"""
    arr = np.zeros((4, 4, 4), dtype=np.uint8)
    arr[1:3, 1:3, 1:3] = 3
    s = _make_settings(tmp_path)
    _seed_mask_payload(tmp_path, s, arr)
    from bodydonor.qc_pipeline import qc_series

    out = qc_series("series-wired", s, thresholds=MIN_THRESHOLDS)
    traces = [t for t in _traces(s) if t.pipeline_stage == "qc_scoring"]
    assert len(traces) == 1
    t = traces[0]
    assert t.mask_version_id == "mv-wired"
    assert t.input_refs["qc_report_id"] == out["qc_report_id"]
    assert t.input_refs["student_source"] == "no case_id"
    assert t.human_outcome is None
    assert t.calibration["gating_ratios"]["forced_ratio"] == 0.20


def test_generate_review_tasks_writes_gate_traces(tmp_path: Path) -> None:
    """generate_review_tasks：全量 ranked 各记 gate_decision trace（auto 也在内）；
    forced/suggested 的 trace 带 ReviewTask id；rules_triggered 与 QCReport 逐项一致。"""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from bodydonor import db
    from bodydonor.models import Case, MaskVersion, Series
    from bodydonor.qc_pipeline import generate_review_tasks

    s = _make_settings(tmp_path)
    # 3 个 series 各一个 mask + QCReport（w3 递减 → priority 降序确定）
    engine = db._engine(s.database.url)
    try:
        with engine.connect() as c:
            sess = Session(c)
            for i in range(3):
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
                        flags=["holes"] if i == 0 else [],
                        rule_scores={
                            "multi_teacher_consistency": {
                                "per_class_dice": {"liver": 1.0 - i / 3}
                            }
                        },
                        passed=True,
                    )
                )
            sess.commit()
            sess.close()
    finally:
        engine.dispose()

    out = generate_review_tasks(s, thresholds=MIN_THRESHOLDS)
    traces = _traces(s)
    gate = [t for t in traces if t.pipeline_stage == "gate_decision"]
    assert len(gate) == 3  # 全量 ranked（auto 也在内，不因 batch_size=6 截断丢）
    by_mv = {t.mask_version_id: t for t in gate}
    g = out["gating"]
    # tier 与 gating 汇总一致
    for tier_name in ("forced", "suggested", "auto"):
        for mv_id in g[tier_name]:
            assert by_mv[mv_id].gate_decision["tier"] == tier_name
    # auto 不带 ReviewTask；forced/suggested 带
    for mv_id in g["auto"]:
        assert by_mv[mv_id].gate_decision["review_task_generated"] is False
        assert by_mv[mv_id].review_task_id is None
    for mv_id in set(g["forced"]) | set(g["suggested"]):
        assert by_mv[mv_id].gate_decision["review_task_generated"] is True
        assert by_mv[mv_id].review_task_id
        assert by_mv[mv_id].gate_decision["sample_group"] in ("forced", "suggested")
    # M6-c priority 信号在 trace 里（w1–w4 + availability + w3_available）
    any_t = gate[0]
    assert "priority" in any_t.process_signals
    assert "w3_available" in any_t.process_signals
    # 验收 b（接线层）：trace.rules_triggered == 落库 QCReport.flags 逐项
    eng = db._engine(s.database.url)
    try:
        with eng.connect() as c:
            sess = Session(c)
            flags_by_mv = dict(
                sess.execute(
                    select(QCReport.mask_version_id, QCReport.flags)
                ).all()
            )
            sess.close()
    finally:
        eng.dispose()
    for t in gate:
        assert t.rules_triggered == flags_by_mv[t.mask_version_id], t.mask_version_id


def test_generate_review_tasks_old_path_writes_traces(tmp_path: Path) -> None:
    """旧 70/30 路径：也记 gate_decision trace（tier=group 语义，不冒充三档）。"""
    from sqlalchemy.orm import Session

    from bodydonor import db
    from bodydonor.models import Case, MaskVersion, Series
    from bodydonor.qc_pipeline import generate_review_tasks

    th = json.loads(json.dumps(MIN_THRESHOLDS))
    th["sampling_priority"]["enabled"] = False
    th["review_sampling"] = {"batch_size": 2, "flagged_ratio": 0.70, "min_clean_pool": 1}
    s = _make_settings(tmp_path)
    engine = db._engine(s.database.url)
    try:
        with engine.connect() as c:
            sess = Session(c)
            sess.add(Case(case_id="case-0"))
            sess.add(Series(series_id="series-0", case_id="case-0"))
            sess.add(
                MaskVersion(
                    mask_version_id="mv-0",
                    series_id="series-0",
                    source="teacher",
                    storage_key="x/0",
                )
            )
            sess.add(
                QCReport(
                    qc_report_id="qc-0",
                    mask_version_id="mv-0",
                    overall_score=0.0,
                    flags=["volume_prior"],
                    rule_scores={},
                    passed=False,
                )
            )
            sess.commit()
            sess.close()
    finally:
        engine.dispose()
    generate_review_tasks(s, thresholds=th)
    gate = [t for t in _traces(s) if t.pipeline_stage == "gate_decision"]
    assert len(gate) == 1
    assert gate[0].gate_decision["tier"] == "flagged"  # group 语义，不冒充 forced/auto
    assert gate[0].rules_triggered == ["volume_prior"]
    assert "priority" not in gate[0].process_signals  # 无 priority 路径不硬造分量
    # deep-review W 修正：旧路径 flagged 也建 ReviewTask → trace 带任务 id
    #（决策回流按 review_task_id 锚定；不写 id 会让 human_outcome 永久 null）
    assert gate[0].gate_decision["review_task_generated"] is True
    assert gate[0].review_task_id
    assert gate[0].gate_decision["review_task_id"] == gate[0].review_task_id
    assert gate[0].gate_decision["sample_group"] == "flagged"


def test_decision_endpoint_backfills_human_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """决策回流端点：ReviewTask 状态写库同一事务补填 trace.human_outcome。

    deep-review Blocker 防回归：回流端点 record_review_decision 落盘
    config/qc_thresholds.yaml（版本控制内）——测试必须 monkeypatch
    QC_THRESHOLDS_PATH 到临时副本（不污染真配置；断言前后 sha256 不变）。
    """
    from fastapi.testclient import TestClient

    import bodydonor.qc_reflux as reflux_mod
    from bodydonor import app as app_module
    from bodydonor.app import app
    from bodydonor.qc import QC_THRESHOLDS_PATH, load_qc_thresholds

    # 阈值文件隔离：临时副本（yaml.safe_dump 不写回真配置）
    th_backup = load_qc_thresholds(QC_THRESHOLDS_PATH)
    tmp_th = tmp_path / "qc_thresholds_test.yaml"
    import yaml as _yaml

    _yaml.safe_dump(th_backup, tmp_th.open("w"), allow_unicode=True, sort_keys=False)
    monkeypatch.setattr(reflux_mod, "QC_THRESHOLDS_PATH", tmp_th)
    before = tmp_th.read_bytes()

    s = _make_settings(tmp_path)
    # 审阅端点用 app.py 全局 settings.database.url——统一指向临时库（同一库）
    monkeypatch.setattr(
        app_module.settings,
        "database",
        type(app_module.settings.database)(url=s.database.url),
    )
    # 种子：series + mask + qc + 评分（qc_series 产 qc_scoring trace）+ 抽检任务
    arr = np.zeros((4, 4, 4), dtype=np.uint8)
    arr[1:3, 1:3, 1:3] = 3
    _seed_mask_payload(tmp_path, s, arr)
    from bodydonor.qc_pipeline import generate_review_tasks, qc_series

    qc_series("series-wired", s, thresholds=MIN_THRESHOLDS)
    generate_review_tasks(s, thresholds=MIN_THRESHOLDS)
    # 该 series 落在哪个档位 → forced/suggested 会有任务；auto 不会
    gate = [t for t in _traces(s) if t.pipeline_stage == "gate_decision"]
    assert len(gate) == 1
    if gate[0].gate_decision["review_task_generated"] is False:
        pytest.skip("case 落 auto 档（无 ReviewTask，回流端点无场景）")
    rt_id = gate[0].review_task_id

    # 回流前：human_outcome 恒 null
    gate = [t for t in _traces(s) if t.pipeline_stage == "gate_decision"]
    assert gate[0].human_outcome is None

    with TestClient(app) as client:
        resp = client.post(
            f"/qc/review-tasks/{rt_id}/decision",
            params={"decision": "accepted", "notes": "looks fine"},
        )
    assert resp.status_code == 200, resp.text
    gate = _traces(s)
    gate = [t for t in gate if t.pipeline_stage == "gate_decision"]
    ho = gate[0].human_outcome
    assert ho is not None
    assert ho["decision"] == "accepted"
    assert ho["notes"] == "looks fine"
    assert "decided_at" in ho
    # 回放同决策：幂等不覆盖
    with TestClient(app) as client:
        client.post(
            f"/qc/review-tasks/{rt_id}/decision",
            params={"decision": "rejected"},
        )
    gate = [t for t in _traces(s) if t.pipeline_stage == "gate_decision"]
    assert gate[0].human_outcome["decision"] == "accepted"  # 历史不改写
    # deep-review Blocker 防回归：回流只写临时副本（真配置前后 sha256 一致——
    # 回流计数进临时 yaml，版本控制内 qc_thresholds.yaml 零改动）
    assert tmp_th.read_bytes() != before  # 回流累计写进的是临时副本
    assert load_qc_thresholds(QC_THRESHOLDS_PATH) == th_backup  # 真配置未被改写


# ---------------------------------------------------------------------------
# 验收 b：rules_triggered 与 QCReport.flags 逐项对比（真实 qc_series 流程）
# ---------------------------------------------------------------------------


def test_acceptance_b_rules_triggered_matches_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收 b（硬性）：qc_series 全流程后，qc_scoring trace 的 rules_triggered
    与落库 QCReport.flags 逐项相等（构造同源保证 + 对比测试实证）。"""
    from sqlalchemy.orm import Session

    from bodydonor import db
    from bodydonor.qc_pipeline import qc_series

    arr = np.zeros((4, 4, 4), dtype=np.uint8)
    arr[1:3, 1:3, 1:3] = 3
    s = _make_settings(tmp_path)
    _seed_mask_payload(tmp_path, s, arr)
    # 有交互的 case：provenance.interactionMetrics 非空 → M6-b 规则真跑
    engine = db._engine(s.database.url)
    try:
        with engine.connect() as c:
            sess = Session(c)
            mv = sess.query(
                __import__("bodydonor.models", fromlist=["MaskVersion"]).MaskVersion
            ).filter_by(mask_version_id="mv-wired").one()
            mv.provenance = {
                "interactionMetrics": {
                    "liver": {
                        "numPrompts": 20,
                        "convergenceSteps": 2,
                        "perStepDeltaDice": [0.1, 0.005],
                        "finalLocalDice": 0.85,
                        "teacherReliability": "high",
                        "abandonedAttempts": 1,
                    }
                }
            }
            sess.commit()
            sess.close()
    finally:
        engine.dispose()
    # 学生 mask monkeypatch：multi_teacher 真跑（mean_dice 进 rule_scores）
    import bodydonor.qc_pipeline as qcp

    def fake_load(case_id: str, settings: Any = None) -> Any:
        return __import__(
            "bodydonor.student_pred", fromlist=["StudentPrediction"]
        ).StudentPrediction(
            case_id=case_id,
            mask_arr=arr,
            classes={3: "aorta"},
            pred_path=tmp_path / "fake.nii.gz",
            model_version_id="student-test",
            weights_key="/tmp/w.pth",
        )

    monkeypatch.setattr(qcp, "load_student_prediction", fake_load)
    out = qc_series("series-wired", s, thresholds=MIN_THRESHOLDS, case_id="case-wired")

    # 对比测试：每条 qc_scoring trace vs 落库 QCReport.flags 逐项
    eng = db._engine(s.database.url)
    try:
        with eng.connect() as c:
            sess = Session(c)
            reports = sess.query(QCReport).all()
            flags_by_mv = {r.mask_version_id: list(r.flags) for r in reports}
            traces = (
                sess.query(DecisionTrace).filter_by(pipeline_stage="qc_scoring").all()
            )
            sess.expunge_all()
            sess.close()
    finally:
        eng.dispose()
    assert len(traces) == 1
    t = traces[0]
    assert t.mask_version_id in flags_by_mv
    # 逐项相等（不是子集）：构造同源 + 对比实证
    assert list(t.rules_triggered) == flags_by_mv[t.mask_version_id]
    # 同 run 的返回 flags 也一致（三端一致：QCResult/QCReport/trace）
    assert list(t.rules_triggered) == list(out["flags"])
    # 交互 case：interaction_convergence 真跑（numPrompts=20 > 15 → flag）
    assert "interaction_convergence" in t.rules_triggered
    assert t.process_signals["interaction_coverage"] is True


# ---------------------------------------------------------------------------
# 验收 a：trace-back——从 trace 单独回溯全链（inputs → signals → rules →
# tier → human_outcome）
# ---------------------------------------------------------------------------


def test_acceptance_a_trace_back_full_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收 a（硬性）：任一 case（具体示例）从 trace 单独回溯决策链——
    inputs → process signals → triggered rules → gate tier → human outcome，
    不需要回到中间变量。回流端点同样 monkeypatch QC_THRESHOLDS_PATH
    （Blocker 防回归：不污染版本控制内 qc_thresholds.yaml）。"""
    from fastapi.testclient import TestClient

    import bodydonor.qc_reflux as reflux_mod
    from bodydonor import app as app_module
    from bodydonor import db
    from bodydonor.app import app
    from bodydonor.qc import QC_THRESHOLDS_PATH, load_qc_thresholds

    th_backup = load_qc_thresholds(QC_THRESHOLDS_PATH)
    tmp_th = tmp_path / "qc_thresholds_test.yaml"
    import yaml as _yaml

    _yaml.safe_dump(th_backup, tmp_th.open("w"), allow_unicode=True, sort_keys=False)
    monkeypatch.setattr(reflux_mod, "QC_THRESHOLDS_PATH", tmp_th)

    arr = np.zeros((4, 4, 4), dtype=np.uint8)
    arr[1:3, 1:3, 1:3] = 3
    s = _make_settings(tmp_path)
    monkeypatch.setattr(
        app_module.settings,
        "database",
        type(app_module.settings.database)(url=s.database.url),
    )
    _seed_mask_payload(tmp_path, s, arr)
    # 学生 mask monkeypatch：multi_teacher 真跑（student_source 记 ModelVersion id）
    import bodydonor.qc_pipeline as qcp

    def fake_load(case_id: str, settings: Any = None) -> Any:
        from bodydonor.student_pred import StudentPrediction

        return StudentPrediction(
            case_id=case_id,
            mask_arr=arr,
            classes={3: "aorta"},
            pred_path=tmp_path / "fake.nii.gz",
            model_version_id="student-test",
            weights_key="/tmp/w.pth",
        )

    monkeypatch.setattr(qcp, "load_student_prediction", fake_load)
    from bodydonor.qc_pipeline import generate_review_tasks, qc_series

    qc_series("series-wired", s, thresholds=MIN_THRESHOLDS, case_id="case-wired")
    generate_review_tasks(s, thresholds=MIN_THRESHOLDS)
    # 回流一个人工决策（accepted）
    gate = [t for t in _traces(s) if t.pipeline_stage == "gate_decision"]
    assert len(gate) == 1
    if gate[0].gate_decision["review_task_generated"] is False:
        pytest.skip("case 落 auto 档（无 ReviewTask，人工决策无场景）")
    with TestClient(app) as client:
        r = client.post(
            f"/qc/review-tasks/{gate[0].review_task_id}/decision",
            params={"decision": "accepted", "notes": "trace-back example"},
        )
    assert r.status_code == 200, r.text

    # ---- 从 trace 单独回溯（只读 DecisionTrace 行）----
    from sqlalchemy.orm import Session as _Session

    eng = db._engine(s.database.url)
    try:
        with eng.connect() as c:
            sess = _Session(c)
            traces = sess.query(DecisionTrace).order_by(DecisionTrace.timestamp).all()
            sess.expunge_all()
            sess.close()
    finally:
        eng.dispose()
    qc_t = next(t for t in traces if t.pipeline_stage == "qc_scoring")
    gate_t = next(t for t in traces if t.pipeline_stage == "gate_decision")

    # 1) inputs：mask 版本 / QCReport / 学生来源指针
    assert qc_t.input_refs["mask_version_id"] == "mv-wired"
    assert qc_t.input_refs["qc_report_id"]
    assert qc_t.input_refs["student_source"] == "student-test"
    assert qc_t.case_id == "case-wired" and qc_t.series_id == "series-wired"
    # 2) process signals：交互覆盖 + M6-b 规则信号（纯自动 case coverage=False）
    assert qc_t.process_signals["interaction_coverage"] is False
    assert "interaction_convergence" in qc_t.process_signals
    assert "interaction_abandonment" in qc_t.process_signals
    # 3) triggered rules：与 QCReport.flags 逐项一致（验收 b 复证）
    assert list(qc_t.rules_triggered) == list(gate_t.rules_triggered)
    # 4) gate tier：三档之一 + ReviewTask 生成语义一致
    assert gate_t.gate_decision["tier"] in ("forced", "suggested", "auto")
    assert gate_t.gate_decision["review_task_generated"] is True
    assert gate_t.gate_decision["review_task_id"] == gate_t.review_task_id
    assert gate_t.gate_decision["sample_group"] == gate_t.gate_decision["tier"]
    # gate trace 的 priority 信号（M6-c 分量 + availability）
    assert "priority" in gate_t.process_signals
    assert "w3_available" in gate_t.process_signals
    # 5) human outcome：回流补填（非 null，accepted）
    assert gate_t.human_outcome == {
        "decision": "accepted",
        "notes": "trace-back example",
        "decided_at": gate_t.human_outcome["decided_at"],
    }
    # calibration 快照（M7 对照基准）
    assert qc_t.calibration["gating_ratios"] == gate_t.calibration["gating_ratios"]
    assert qc_t.calibration["priority_weights"]["w1"] == 1.0


# ---------------------------------------------------------------------------
# 真实 hold-out 验收（30 例现算：真实 w3+w4 + w1/w2 置 0 如实标注）
# ---------------------------------------------------------------------------


def _teacher_volume_and_classes(case_id: str) -> tuple[Any, dict[int, str]] | None:
    """case_id -> (教师整图 arr, teacher_classes)；test_m6d_gating.py 同款。"""
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
    """真实产物齐全性（test_m6d_gating.py 同款口径）。"""
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

    holdout = [
        line.split("\t")[0]
        for line in (REPO / "eval/holdout.txt").read_text().splitlines()
        if line.strip()
    ]
    for case_id in holdout:
        if not (data / "student_holdout/pred" / f"{case_id}.nii.gz").exists():
            return False
        try:
            load_student_prediction(case_id)
        except StudentPredictionError:
            return False
    return True


@pytest.mark.local_artifacts
@pytest.mark.skipif(
    not _real_products_available(),
    reason="~/data/BodyDonor 真实产物缺失（trace 验收依赖 30 例 hold-out 教师+学生 mask）",
)
def test_real_holdout_trace_back_30_cases() -> None:
    """真实 hold-out 验收（30 例现算，不 hardcode 档位结果）：

    真实 w3（教师+学生 mask 现算逐类 Dice）+ 真实 w4（117 类低频名单）+
    w1/w2 置 0（hold-out 无真实交互轨迹，如实标注 synthetic——无过程信号
    不造假）。构造 qc_rows → sample_review_tasks_by_priority 排名分档 →
    把排名结果落成 gate_decision trace（record_gate_trace）→ 任选一 case
    从 trace 单独回溯：inputs/signals/rules/tier/human_outcome 全链。
    """
    from bodydonor.qc import rule_multi_teacher_consistency
    from bodydonor.qc_priority import sample_review_tasks_by_priority
    from bodydonor.student_pred import load_student_prediction

    cfg = {"enabled": True, "threshold": 0.80}
    rows: list[dict] = []
    for case_id in [
        line.split("\t")[0]
        for line in (REPO / "eval/holdout.txt").read_text().splitlines()
        if line.strip()
    ]:
        t = _teacher_volume_and_classes(case_id)
        assert t is not None, f"{case_id}: 无教师输出"
        teacher_arr, teacher_classes = t
        pred = load_student_prediction(case_id)
        flagged, _score, detail = rule_multi_teacher_consistency(
            teacher_arr, np.asarray(pred.mask_arr), teacher_classes, pred.classes, cfg
        )
        rows.append(
            {
                "mask_version_id": case_id,
                "passed": True,
                "qc_report_id": f"qc-regress-{case_id}",
                # rules_triggered 与规则 flagged 同源（多教师一致 <0.80 则 flag）
                "flags": ["multi_teacher_consistency"] if flagged else [],
                "rule_scores": {"multi_teacher_consistency": detail},
                # synthetic：hold-out 无真实交互轨迹，w1/w2 无过程信号（不造假）
                "interaction_metrics": {},
            }
        )
    plan = sample_review_tasks_by_priority(rows, thresholds=MIN_THRESHOLDS)
    ranked = plan.ranked  # type: ignore[attr-defined]
    assert len(ranked) == 30
    # 落成 gate_decision trace（真实排名 → record_gate_trace）
    traces = []
    for row in ranked:
        qc_row = next(r for r in rows if r["mask_version_id"] == row.mask_version_id)
        traces.append(
            record_gate_trace(
                case_id=row.mask_version_id,
                series_id=row.mask_version_id,
                mask_version_id=row.mask_version_id,
                qc_report_id=row.qc_report_id or qc_row["qc_report_id"],
                flags=qc_row["flags"],
                rule_scores=qc_row["rule_scores"],
                interaction_metrics=qc_row["interaction_metrics"],
                ranked_row=row,
                component_availability=dict(plan.component_availability),  # type: ignore[attr-defined]
                tier=row.gating_tier,
                review_task_id=None,  # 本验收只测排名落痕，不建 ReviewTask
                sample_group=None,
                thresholds=MIN_THRESHOLDS,
            )
        )
    assert len(traces) == 30
    # 任选一 case（priority 最高者，从 ranked 指认不 hardcode）从 trace 回溯
    top = traces[0]
    assert top.pipeline_stage == "gate_decision"
    assert top.case_id == top.mask_version_id
    assert top.input_refs["mask_version_id"] == top.case_id
    assert top.process_signals["priority"] >= 0.0
    assert top.process_signals["interaction_coverage"] is False  # hold-out 无交互
    assert "w3_available" in top.process_signals
    assert top.gate_decision["tier"] in ("forced", "suggested", "auto")
    assert top.gate_decision["review_task_generated"] is False
    assert top.human_outcome is None  # 无人工决策（不造假）
    assert top.calibration["gating_ratios"]["forced_ratio"] == 0.20
    assert top.calibration["priority_weights"] == {"w1": 1.0, "w2": 1.0, "w3": 1.0, "w4": 1.0}
    assert top.calibration["source"]["sampling_priority"] == "cold_start"
    # 三档覆盖齐全（30 例默认 20/60/20 → 6/18/6）
    tiers = [t.gate_decision["tier"] for t in traces]
    assert tiers.count("forced") == 6
    assert tiers.count("suggested") == 18
    assert tiers.count("auto") == 6
    print(
        "\nM6-F 真实 hold-out trace:",
        json.dumps(
            {
                "top_case": {
                    "case_id": top.case_id,
                    "tier": top.gate_decision["tier"],
                    "priority": top.process_signals["priority"],
                    "components": top.process_signals["components"],
                    "availability": top.process_signals["availability"],
                },
                "n_tiers": {k: tiers.count(k) for k in ("forced", "suggested", "auto")},
            },
            ensure_ascii=False,
            indent=1,
        ),
    )


def test_old_path_decision_backfills_human_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """deep-review W 失败测试先行（旧 70/30 路径）：flagged ReviewTask 人工
    决策后 gate trace 的 human_outcome 补填（旧实现 rt_id=None → 回流端点
    找不到 trace → human_outcome 永久 null）。"""
    import json as _json

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    import bodydonor.qc_reflux as reflux_mod
    from bodydonor import app as app_module
    from bodydonor import db
    from bodydonor.app import app
    from bodydonor.models import Case, MaskVersion, QCReport, Series
    from bodydonor.qc import QC_THRESHOLDS_PATH, load_qc_thresholds
    from bodydonor.qc_pipeline import generate_review_tasks

    # 阈值文件隔离（Blocker 防回归同款）
    th_backup = load_qc_thresholds(QC_THRESHOLDS_PATH)
    tmp_th = tmp_path / "qc_thresholds_test.yaml"
    import yaml as _yaml

    _yaml.safe_dump(th_backup, tmp_th.open("w"), allow_unicode=True, sort_keys=False)
    monkeypatch.setattr(reflux_mod, "QC_THRESHOLDS_PATH", tmp_th)

    th = _json.loads(_json.dumps(MIN_THRESHOLDS))
    th["sampling_priority"]["enabled"] = False
    th["review_sampling"] = {"batch_size": 2, "flagged_ratio": 0.70, "min_clean_pool": 1}
    s = _make_settings(tmp_path)
    monkeypatch.setattr(
        app_module.settings,
        "database",
        type(app_module.settings.database)(url=s.database.url),
    )
    engine = db._engine(s.database.url)
    try:
        with engine.connect() as c:
            sess = Session(c)
            sess.add(Case(case_id="case-0"))
            sess.add(Series(series_id="series-0", case_id="case-0"))
            sess.add(
                MaskVersion(
                    mask_version_id="mv-0",
                    series_id="series-0",
                    source="teacher",
                    storage_key="x/0",
                )
            )
            sess.add(
                QCReport(
                    qc_report_id="qc-0",
                    mask_version_id="mv-0",
                    overall_score=0.0,
                    flags=["volume_prior"],
                    rule_scores={},
                    passed=False,
                )
            )
            sess.commit()
            sess.close()
    finally:
        engine.dispose()
    generate_review_tasks(s, thresholds=th)
    gate = [t for t in _traces(s) if t.pipeline_stage == "gate_decision"]
    assert len(gate) == 1
    rt_id = gate[0].review_task_id
    assert rt_id, "flagged trace 必须带 ReviewTask id（回流锚点）"

    with TestClient(app) as client:
        r = client.post(
            f"/qc/review-tasks/{rt_id}/decision", params={"decision": "accepted"}
        )
    assert r.status_code == 200, r.text
    gate = [t for t in _traces(s) if t.pipeline_stage == "gate_decision"]
    assert gate[0].human_outcome is not None
    assert gate[0].human_outcome["decision"] == "accepted"


def test_repeated_gate_runs_backfill_all_traces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """deep-review W 失败测试先行（重跑累积）：同一 review_task_id 下多条
    gate_decision trace 全部补填（旧实现只补 timestamp 最新一条，更早的
    行 human_outcome 永久 null，M7 校准会误读为「无决策」）。"""
    from fastapi.testclient import TestClient

    import bodydonor.qc_reflux as reflux_mod
    from bodydonor import app as app_module
    from bodydonor.app import app
    from bodydonor.qc import QC_THRESHOLDS_PATH, load_qc_thresholds
    from bodydonor.qc_pipeline import generate_review_tasks, qc_series

    th_backup = load_qc_thresholds(QC_THRESHOLDS_PATH)
    tmp_th = tmp_path / "qc_thresholds_test.yaml"
    import yaml as _yaml

    _yaml.safe_dump(th_backup, tmp_th.open("w"), allow_unicode=True, sort_keys=True)
    monkeypatch.setattr(reflux_mod, "QC_THRESHOLDS_PATH", tmp_th)

    s = _make_settings(tmp_path)
    monkeypatch.setattr(
        app_module.settings,
        "database",
        type(app_module.settings.database)(url=s.database.url),
    )
    arr = np.zeros((4, 4, 4), dtype=np.uint8)
    arr[1:3, 1:3, 1:3] = 3
    _seed_mask_payload(tmp_path, s, arr)
    qc_series("series-wired", s, thresholds=MIN_THRESHOLDS, case_id="case-wired")
    # 重跑两轮 generate_review_tasks（第二轮任务幂等复用，不再新建）
    generate_review_tasks(s, thresholds=MIN_THRESHOLDS)
    generate_review_tasks(s, thresholds=MIN_THRESHOLDS)
    gate = [t for t in _traces(s) if t.pipeline_stage == "gate_decision"]
    assert len(gate) == 2  # 重跑累积两条 gate_decision trace
    assert all(t.review_task_id for t in gate)
    rt_id = gate[0].review_task_id
    assert rt_id

    with TestClient(app) as client:
        r = client.post(
            f"/qc/review-tasks/{rt_id}/decision", params={"decision": "corrected"}
        )
    assert r.status_code == 200, r.text
    gate = [t for t in _traces(s) if t.pipeline_stage == "gate_decision"]
    # 全部补填（不只 timestamp 最新一条）
    assert all(t.human_outcome is not None for t in gate), (
        [t.human_outcome for t in gate]
    )
    assert all(t.human_outcome["decision"] == "corrected" for t in gate)


def test_migration_builds_review_task_id_index(tmp_path: Path) -> None:
    """deep-review W4 失败测试先行：models.DecisionTrace.review_task_id 有
    index=True（create_all 建 ix_decision_traces_review_task_id），迁移
    a4f9e2c81d03 必须同样建该索引，否则迁移建库与 create_all 漂移——
    human_outcome 回流端点按 review_task_id 查询在迁移建库上慢查询。"""
    db = tmp_path / "mig_index.db"
    url = f"sqlite:///{db}"
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db_url={url}", "upgrade", "head"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        env={
            "PATH": "/usr/bin:/bin",
            "VIRTUAL_ENV": str(REPO / ".venv"),
        },
    )
    assert r.returncode == 0, f"alembic upgrade 失败：{r.stdout[-500:]}{r.stderr[-500:]}"
    c = sqlite3.connect(db)
    try:
        # PRAGMA index_list 行形如 (seq, name, unique, origin, partial)
        indexes = {
            row[1] for row in c.execute("PRAGMA index_list(decision_traces)")
        }
    finally:
        c.close()
    assert "ix_decision_traces_review_task_id" in indexes, (
        f"迁移建库缺 review_task_id 索引，实际：{sorted(indexes)}"
    )


def _run_alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db_url={url}", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        env={
            "PATH": "/usr/bin:/bin",
            "VIRTUAL_ENV": str(REPO / ".venv"),
        },
    )


def test_migration_s3_roundtrip_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    """S3 做法 6 号项验收：f8a3b9c2d1e4 roundtrip（upgrade head → downgrade
    a4f9e2c81d03 → upgrade head）。6 列全可空——存量 DecisionTrace 行（先建
    14 列旧表并插一行）必须能升级；downgrade 后 6 列消失、原 14 列保留。"""
    db = tmp_path / "mig_s3_roundtrip.db"
    url = f"sqlite:///{db}"
    r = _run_alembic(url, "upgrade", "a4f9e2c81d03")
    assert r.returncode == 0, f"upgrade a4f9e2c81d03 失败：{r.stdout[-500:]}{r.stderr[-500:]}"
    # 存量行：14 列旧表插一行（模拟升级前已有 trace）
    c = sqlite3.connect(db)
    try:
        c.execute(
            "INSERT INTO decision_traces (trace_id, series_id, mask_version_id,"
            " timestamp, pipeline_stage) VALUES ('dt-pre', 's1', 'mv1',"
            " '2026-11-15 00:00:00', 'qc_scoring')"
        )
        c.commit()
    finally:
        c.close()
    # upgrade head（+6 列，存量行保留）
    r = _run_alembic(url, "upgrade", "head")
    assert r.returncode == 0, f"upgrade head 失败：{r.stdout[-500:]}{r.stderr[-500:]}"
    c = sqlite3.connect(db)
    try:
        cols = {row[1] for row in c.execute("PRAGMA table_info(decision_traces)")}
        assert {
            "modality",
            "teacher_id",
            "distill_profile",
            "skip_reason",
            "config_hash",
            "env",
        } <= cols, (
            f"upgrade head 后缺 S3 新列，实际：{sorted(cols)}"
        )
        row = c.execute(
            "SELECT trace_id, modality FROM decision_traces WHERE trace_id='dt-pre'"
        ).fetchone()
        assert row == ("dt-pre", None), f"存量行未保留或 modality 非空：{row}"
    finally:
        c.close()
    # downgrade a4f9e2c81d03（-6 列，原 14 列保留）
    r = _run_alembic(url, "downgrade", "a4f9e2c81d03")
    assert r.returncode == 0, f"downgrade 失败：{r.stdout[-500:]}{r.stderr[-500:]}"
    c = sqlite3.connect(db)
    try:
        cols = {row[1] for row in c.execute("PRAGMA table_info(decision_traces)")}
        assert "modality" not in cols and "trace_id" in cols and "rules_triggered" in cols, (
            f"downgrade 后列集不对：{sorted(cols)}"
        )
    finally:
        c.close()
    # 再 upgrade head（幂等可重放）
    r = _run_alembic(url, "upgrade", "head")
    assert r.returncode == 0, f"再 upgrade head 失败：{r.stdout[-500:]}{r.stderr[-500:]}"
    c = sqlite3.connect(db)
    try:
        cols = {row[1] for row in c.execute("PRAGMA table_info(decision_traces)")}
        assert "skip_reason" in cols, f"再 upgrade 后缺新列：{sorted(cols)}"
    finally:
        c.close()
