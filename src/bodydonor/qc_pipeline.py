"""QC 编排层（M3，proposal §6 阶段 4/5）：MaskVersion 评分落库 + 抽检任务生成。

阶段 4（自动质控）：最新 teacher MaskVersion -> run_qc 评分 -> QCReport 落库
（passed=false 即低质量过滤，不进蒸馏训练集）。

阶段 5（人工抽检）：QCReport 结果混合采样（flagged 70% / clean 随机 30%）->
ReviewTask 落库（proposal §6 第 5 阶段；评审协议见 M2-ALIGN-REPORT 3.5：
单图 + 单 mask，审阅页 /review?series_id=...）。

写路径走单一写入纪律（db.write_lock，M0-fix）；QC 引擎本体在 qc.py（纯体素
统计，无 GPU）。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from .config import Settings
from .models import MaskVersion, QCReport, ReviewTask
from .qc import (
    QC_THRESHOLDS_PATH,
    RULE_MULTI_TEACHER,
    _rule_cfg,
    load_qc_thresholds,
    run_qc,
    sample_review_tasks,
)
from .qc_trace import record_gate_trace, record_qc_trace
from .student_pred import StudentPredictionError, load_student_prediction


class QCOrchestrationError(Exception):
    """QC 编排失败（unknown mask version、评分异常等）。"""


def _latest_mask_version(session, series_id: str) -> MaskVersion | None:  # noqa: ANN001
    from sqlalchemy import select

    return (
        session.execute(
            select(MaskVersion)
            .where(MaskVersion.series_id == series_id[:60])
            .order_by(MaskVersion.version.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def qc_series(
    series_id: str,
    settings: Settings,
    thresholds: dict[str, Any] | None = None,
    case_id: str | None = None,
) -> dict[str, Any]:
    """对最新 MaskVersion 评分并落 QCReport（阶段 4）。

    返回 {qc_report_id, mask_version_id, overall_score, flags, passed}；
    评分本体 run_qc（qc.py）。重复评分幂等：同 MaskVersion 已有 QCReport 时
    生成新版本记录（版本历史保留，评审可 diff 阈值改动的影响）。

    M6-e 接线（关闭 M4 §3.3 open deviation）：case_id 给出且 multi_teacher_
    consistency enabled 时，经 load_student_prediction 读同 case 学生 mask，
    传给 run_qc 让规则在生产路径真跑。学生预测缺失（StudentPredictionError，
    生产 case 一律如此——预测产物只覆盖 hold-out 30 例）→ 显式 skipped
    {"skipped": "no student mask"}，不进 flags、不计入 enabled 分母（M4 已定
    语义不变）。case_id 不给 → 同样 skipped，不硬造映射。

    M6-b 接线：最新 MaskVersion 的 provenance.interactionMetrics（M6-a 采集，
    有 nnInteractive 交互的 case 才非空）传给 run_qc，过程级规则
    interaction_convergence / interaction_abandonment 在生产路径真跑。
    无 provenance / 空 interactionMetrics（纯自动 case）→ run_qc 内显式
    skipped "no interaction metrics"（不进 flags、不计入 enabled 分母）。
    """
    import numpy as np

    from . import db
    from .storage import LocalFsStorage

    th = thresholds or load_qc_thresholds()
    url = settings.database.url
    engine = db._engine(url)
    try:
        with engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            mv = _latest_mask_version(s, series_id)
            s.close()
    finally:
        engine.dispose()
    if mv is None:
        raise QCOrchestrationError(f"unknown series or no mask version: {series_id}")

    # ---- M6-b：provenance.interactionMetrics（M6-a 采集；纯自动 case 为空）----
    prov = mv.provenance if isinstance(mv.provenance, dict) else {}
    interaction_metrics = prov.get("interactionMetrics")
    interaction_metrics = interaction_metrics if isinstance(interaction_metrics, dict) else {}

    # ---- M6-e：学生 mask 可选路径（case_id 给出才查，缺失不造假）----
    student_mask_arr = None
    student_classes = None
    student_source = "no case_id"
    if case_id is not None and _rule_cfg(th, RULE_MULTI_TEACHER).get("enabled", False):
        try:
            pred = load_student_prediction(case_id, settings)
            student_mask_arr = pred.mask_arr
            student_classes = pred.classes
            student_source = f"{pred.model_version_id}"
        except StudentPredictionError as e:
            student_source = f"unavailable: {e}"

    st = LocalFsStorage(settings.resolved_data_dir / "storage")
    # 原图与 mask 同目录：mask key 形如 case/series/mask_vN.nii.gz 或 case/series/mask.nii.gz
    series_dir = "/".join(mv.storage_key.split("/")[:2])
    img_key = f"{series_dir}/image.nii.gz"
    mask_arr = None
    img_shape = None
    hu_values = None
    totalseg_classes: dict[int, str] = {}
    try:
        payload = st.load(mv.storage_key)
        with TmpNifti(payload) as img:
            mask_arr = np.asarray(img.dataobj)
            img_shape = tuple(int(v) for v in img.shape)
    except (KeyError, OSError) as e:
        raise QCOrchestrationError(f"mask 文件缺失或损坏：{mv.storage_key}（{e}）") from e
    if st.exists(img_key):
        with TmpNifti(st.load(img_key)) as img:
            hu_values = np.asarray(img.dataobj)
    # labels.json -> label 索引 -> totalseg_class 名（HU 骨类跳过 + 左右对称性）
    labels_key = f"{series_dir}/labels.json"
    if st.exists(labels_key):
        import json as _json

        try:
            doc = _json.loads(bytes(st.load(labels_key)))
            for lb in doc.get("labels", []):
                totalseg_classes[int(lb["index"])] = str(lb.get("totalseg_class", ""))
        except (ValueError, KeyError, TypeError):
            totalseg_classes = {}

    result = run_qc(
        mask_arr,
        img_shape,
        mask_version_id=mv.mask_version_id,
        series_id=series_id,
        settings=settings,
        hu_values=hu_values,
        totalseg_classes=totalseg_classes or None,
        thresholds=th,
        # M6-e 接线：case_id 给出且学生预测可取 → 真跑 multi_teacher_consistency；
        # 缺失/无 case_id → run_qc 内显式 skipped="no student mask"（不进 flags、
        # 不计入 enabled 分母，M4 已定语义）
        student_mask_arr=student_mask_arr,
        student_classes=student_classes,
        interaction_metrics=interaction_metrics,
    )
    # QCReport + qc_scoring DecisionTrace 落库（单一写入纪律；同一事务）
    engine = db._engine(url)
    try:
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            try:
                report = QCReport(
                    qc_report_id=f"qc-{uuid.uuid4().hex[:8]}",
                    mask_version_id=mv.mask_version_id,
                    overall_score=result.overall_score,
                    flags=result.flags,
                    rule_scores=result.rule_scores,
                    passed=result.passed,
                    config_version=str(th.get("cold_start", {}).get("calibrated_at", "")),
                )
                s.add(report)
                # M6-f：qc_scoring trace 与 QCReport 同一事务（决策在哪发生就在哪记）
                s.add(
                    record_qc_trace(
                        case_id=case_id,
                        series_id=series_id,
                        mask_version_id=mv.mask_version_id,
                        qc_report_id=report.qc_report_id,
                        flags=result.flags,
                        rule_scores=result.rule_scores,
                        interaction_metrics=interaction_metrics,
                        student_source=student_source,
                        thresholds=th,
                    )
                )
                s.commit()
                report_id = report.qc_report_id
            finally:
                s.close()
    finally:
        engine.dispose()
    out = result.to_dict()
    out["qc_report_id"] = report_id
    # M6-e：学生 mask 血缘（case 级，DecisionTrace 前置；not persisted to QCReport）
    out["student_source"] = student_source
    return out


class TmpNifti:
    """bytes -> nibabel 图像上下文（临时文件，用完即删；与 app._nifti_from_bytes 同模式）。"""

    def __init__(self, payload: bytes) -> None:
        import tempfile

        # SIM115：delete=False + 手动 unlink 是 nibabel 路径读取所需（nib 5.x 只吃路径），
        # 用完即删；noqa 只标这一行
        self._tmp = tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False)  # noqa: SIM115
        self._tmp.write(payload)
        self._tmp.close()

    def __enter__(self) -> Any:
        import nibabel as nib

        return nib.load(self._tmp.name)

    def __exit__(self, *exc: Any) -> None:
        Path(self._tmp.name).unlink(missing_ok=True)


def _ensure_review_task(
    session,
    mask_version_id: str,
    series_id: str,
    qc_report_id: str,  # noqa: ANN001
    group: str,  # noqa: ANN001 — forced | suggested | flagged | clean
) -> str:
    """取或建 ReviewTask（幂等：同 mask_version 已有 pending 任务不重复建）。

    M6-d：已有 pending 任务但分组更低（suggested < forced）时升级 notes 里的
    sample_group（红标不可静默丢失）。已决策（claimed/accepted/…）的任务
    不动——决策历史保留，不因重跑生成而改写。
    """
    from sqlalchemy import select

    existing = (
        session.execute(
            select(ReviewTask)
            .join(QCReport, ReviewTask.qc_report_id == QCReport.qc_report_id)
            .where(QCReport.mask_version_id == mask_version_id)
        )
        .scalars()
        .first()
    )
    if existing is not None:
        # M6-d 分组升级：pending 且旧分组低（非 forced -> forced）时改写
        # notes 的 sample_group（幂等不重复建任务，但红标跟随最新分档）。
        if existing.status == "pending" and group == "forced":
            notes = existing.notes or ""
            if "sample_group=forced" not in notes:
                if "sample_group=" in notes:
                    notes = notes.replace("sample_group=suggested", "sample_group=forced")
                    notes = notes.replace("sample_group=clean", "sample_group=forced")
                    notes = notes.replace("sample_group=flagged", "sample_group=forced")
                else:
                    notes = f"{notes} | sample_group=forced" if notes else "sample_group=forced"
                existing.notes = notes
        return existing.review_task_id
    task = ReviewTask(
        review_task_id=f"rt-{uuid.uuid4().hex[:8]}",
        qc_report_id=qc_report_id,
        series_id=series_id[:60],
        status="pending",
        notes=f"sample_group={group}",
    )
    session.add(task)
    session.flush()
    return task.review_task_id


def generate_review_tasks(
    settings: Settings,
    batch_size: int | None = None,
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """阶段 5：QCReport 结果采样 -> ReviewTask 落库。

    M6-c 起生产路径 = 过程风险驱动采样（sample_review_tasks_by_priority，
    config sampling_priority.enabled）：按 priority 降序取前 batch_size 名。
    旧 flagged 70% / clean 30% 路径（sample_review_tasks）保留不删——对照
    路径与既有测试不静默改行为（切换决定见 M6-C-REPORT）。

    候选池 = 每个 Series 最新 MaskVersion 的最新 QCReport（无 QCReport 的
    Series 跳过——先跑 qc_series）。priority 采样的数据源随候选池一并取：
    QCReport.rule_scores（w3/w4）+ MaskVersion.provenance.interactionMetrics
    （w1/w2）；缺的行按降级语义置零/记不可用（qc_priority.py docstring）。
    """
    from sqlalchemy import func, select

    from . import db
    from .qc_priority import sample_review_tasks_by_priority

    th = thresholds or load_qc_thresholds()
    if batch_size is not None:
        th = dict(th)
        th["review_sampling"] = {
            **th.get("review_sampling", {}),
            "batch_size": batch_size,
        }

    use_priority = bool(th.get("sampling_priority", {}).get("enabled", False))

    url = settings.database.url
    engine = db._engine(url)
    rows: list[dict[str, Any]] = []
    try:
        with engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            # 每个 mask_version 最新 QCReport（按 created_at 降序第一条）
            sub = select(
                QCReport.mask_version_id,
                QCReport.qc_report_id,
                QCReport.passed,
                QCReport.overall_score,
                QCReport.flags,
                QCReport.rule_scores,
                func.row_number()
                .over(
                    partition_by=QCReport.mask_version_id,
                    order_by=QCReport.created_at.desc(),
                )
                .label("rn"),
            ).subquery()
            latest = s.execute(
                select(
                    sub.c.mask_version_id,
                    sub.c.qc_report_id,
                    sub.c.passed,
                    sub.c.overall_score,
                    sub.c.flags,
                    sub.c.rule_scores,
                ).where(sub.c.rn == 1)
            ).all()
            for r in latest:
                rows.append(
                    {
                        "mask_version_id": r.mask_version_id,
                        "qc_report_id": r.qc_report_id,
                        "passed": bool(r.passed),
                        "overall_score": float(r.overall_score or 0.0),
                        # M6-f：QCReport.flags 原文（trace rules_triggered 与
                        # 落库 flags 逐项一致的数据源，验收 b）
                        "flags": r.flags if isinstance(r.flags, list) else [],
                        # M6-c：w3/w4 数据源（multi_teacher_consistency detail 逐类 Dice）
                        "rule_scores": r.rule_scores if isinstance(r.rule_scores, dict) else {},
                    }
                )
            # mask_version -> series（ReviewTask 挂 Series 便于检索）+ interactionMetrics
            # row_by_mv 索引：回填 interaction_metrics 与 qc_id 均 O(1)（大库不 O(n²)）
            row_by_mv = {r["mask_version_id"]: r for r in rows}
            mv_ids = list(row_by_mv)
            mv_map = {}
            # M6-f：series -> case_id（trace 用；Series.case_id 是普通列，无懒加载）
            case_by_series: dict[str, str | None] = {}
            if mv_ids:
                for mv in (
                    s.execute(select(MaskVersion).where(MaskVersion.mask_version_id.in_(mv_ids)))
                    .scalars()
                    .all()
                ):
                    mv_map[mv.mask_version_id] = mv
                    case_by_series[mv.series_id] = mv.series.case_id
                    prov = mv.provenance if isinstance(mv.provenance, dict) else {}
                    im = prov.get("interactionMetrics")
                    # M6-c：w1/w2 数据源（per-class 过程指标，纯自动 case 为空）
                    row_by_mv[mv.mask_version_id]["interaction_metrics"] = (
                        im if isinstance(im, dict) else {}
                    )
            s.close()
    finally:
        engine.dispose()

    if use_priority:
        plan = sample_review_tasks_by_priority(rows, thresholds=th)
    else:
        plan = sample_review_tasks(rows, thresholds=th)
    created: list[dict[str, Any]] = []
    # M6-f：ranked 行 / gating 三档 id 集（gate_decision trace 的 tier/信号源）
    ranked_by_mv = {}
    if use_priority:
        ranked_by_mv = {
            row.mask_version_id: row for row in getattr(plan, "ranked", []) or []
        }
    gating_tier_by_mv: dict[str, str] = {}
    if use_priority:
        gating = getattr(plan, "gating", {})
        for tier_name in ("forced", "suggested", "auto"):
            for mv_id in gating.get(tier_name, []) or []:
                gating_tier_by_mv[mv_id] = tier_name
    component_availability = dict(getattr(plan, "component_availability", {}) or {})
    engine = db._engine(url)
    try:
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            try:
                if use_priority:
                    # M6-d gating：forced（标红）+ suggested（不标红）建
                    # ReviewTask，auto（自动接受）不建——高置信 case 减少医生
                    # 负担。gating 按全量排名分档（tier_for_rank），不是
                    # batch_size 截断集（flagged_ids 仍是 M6-c 语义：top
                    # batch_size，作采样对照保留）。
                    gating = getattr(plan, "gating", {})
                    tier_groups = (
                        ("forced", gating.get("forced", [])),
                        ("suggested", gating.get("suggested", [])),
                    )
                else:
                    tier_groups = (("flagged", plan.flagged_ids), ("clean", plan.clean_ids))
                for group, ids in tier_groups:
                    for mv_id in ids:
                        mv = mv_map.get(mv_id)
                        if mv is None:
                            continue
                        qc_id = row_by_mv[mv_id]["qc_report_id"]
                        rt_id = _ensure_review_task(s, mv_id, mv.series_id, qc_id, group)
                        created.append(
                            {
                                "review_task_id": rt_id,
                                "mask_version_id": mv_id,
                                "series_id": mv.series_id,
                                "group": group,
                            }
                        )
                # M6-f：gate_decision trace——priority 路径全量 ranked 行各记
                # 一条（auto 也在内：自动接受本身是门禁决策）；tier 取
                # gating_tier_by_mv（旧 70/30 路径无 gating → tier=group 语义
                # 不冒充，记 group）。QCReport flags 逐项带入（验收 b）。
                for r in rows:
                    mv_id = r["mask_version_id"]
                    mv = mv_map.get(mv_id)
                    if mv is None:
                        continue
                    rs = r["rule_scores"] if isinstance(r["rule_scores"], dict) else {}
                    if use_priority:
                        tier = gating_tier_by_mv.get(mv_id, "auto")
                        rt_entry = next(
                            (t for t in created if t["mask_version_id"] == mv_id), None
                        )
                        rt_id = rt_entry["review_task_id"] if rt_entry else None
                        group_out = rt_entry["group"] if rt_entry else None
                    else:
                        tier = group_out = next(
                            (
                                g
                                for g, ids2 in tier_groups
                                if mv_id in (ids2 or [])
                            ),
                            None,
                        )
                        rt_id = None
                    s.add(
                        record_gate_trace(
                            case_id=case_by_series.get(mv.series_id),
                            series_id=mv.series_id,
                            mask_version_id=mv_id,
                            qc_report_id=r["qc_report_id"],
                            flags=r["flags"],
                            rule_scores=rs,
                            interaction_metrics=r.get("interaction_metrics"),
                            ranked_row=ranked_by_mv.get(mv_id),
                            component_availability=component_availability,
                            tier=tier,
                            review_task_id=rt_id,
                            sample_group=group_out,
                            thresholds=th,
                        )
                    )
                s.commit()
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()
    finally:
        engine.dispose()
    out = {"plan": plan.to_dict(), "tasks": created}
    if use_priority:
        # M6-c：priority 采样的全量排名与各分量可用性（DecisionTrace 前置，
        # M6-f 才落实体；本轮只在返回值与报告里记）。
        ranked = getattr(plan, "ranked", None)
        if ranked is not None:
            out["ranked"] = [row.to_dict() for row in ranked]
            out["component_availability"] = dict(getattr(plan, "component_availability", {}))
        # M6-d：三档 gating 汇总（forced/suggested/auto id 列表 + 计数 + 比例）。
        out["gating"] = dict(getattr(plan, "gating", {}))
    return out


__all__ = [
    "QCOrchestrationError",
    "QC_THRESHOLDS_PATH",
    "generate_review_tasks",
    "qc_series",
]
