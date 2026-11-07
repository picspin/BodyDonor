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
from .qc import QC_THRESHOLDS_PATH, load_qc_thresholds, run_qc, sample_review_tasks


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
) -> dict[str, Any]:
    """对最新 MaskVersion 评分并落 QCReport（阶段 4）。

    返回 {qc_report_id, mask_version_id, overall_score, flags, passed}；
    评分本体 run_qc（qc.py）。重复评分幂等：同 MaskVersion 已有 QCReport 时
    生成新版本记录（版本历史保留，评审可 diff 阈值改动的影响）。
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
    )
    # QCReport 落库（单一写入纪律）
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
                s.commit()
                report_id = report.qc_report_id
            finally:
                s.close()
    finally:
        engine.dispose()
    out = result.to_dict()
    out["qc_report_id"] = report_id
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
    session, mask_version_id: str, series_id: str, qc_report_id: str,  # noqa: ANN001
    group: str,  # noqa: ANN001 — flagged | clean
) -> str:
    """取或建 ReviewTask（幂等：同 mask_version 已有 pending 任务不重复建）。"""
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
    """阶段 5：QCReport 结果混合采样 -> ReviewTask 落库。

    候选池 = 每个 Series 最新 MaskVersion 的最新 QCReport（无 QCReport 的
    Series 跳过——先跑 qc_series）。flagged 70%（passed=false，overall_score
    升序最差优先）/ clean 随机 30%（proposal §6 第 5 阶段硬约束）。
    """
    from sqlalchemy import func, select

    from . import db

    th = thresholds or load_qc_thresholds()
    if batch_size is not None:
        th = dict(th)
        th["review_sampling"] = {
            **th.get("review_sampling", {}), "batch_size": batch_size,
        }

    url = settings.database.url
    engine = db._engine(url)
    rows: list[dict[str, Any]] = []
    try:
        with engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            # 每个 mask_version 最新 QCReport（按 created_at 降序第一条）
            sub = (
                select(
                    QCReport.mask_version_id,
                    QCReport.qc_report_id,
                    QCReport.passed,
                    QCReport.overall_score,
                    func.row_number()
                    .over(
                        partition_by=QCReport.mask_version_id,
                        order_by=QCReport.created_at.desc(),
                    )
                    .label("rn"),
                ).subquery()
            )
            latest = (
                s.execute(
                    select(
                        sub.c.mask_version_id, sub.c.qc_report_id,
                        sub.c.passed, sub.c.overall_score,
                    ).where(sub.c.rn == 1)
                )
                .all()
            )
            for r in latest:
                rows.append({
                    "mask_version_id": r.mask_version_id,
                    "qc_report_id": r.qc_report_id,
                    "passed": bool(r.passed),
                    "overall_score": float(r.overall_score or 0.0),
                })
            # mask_version -> series（ReviewTask 挂 Series 便于检索）
            mv_ids = [r["mask_version_id"] for r in rows]
            mv_map = {}
            if mv_ids:
                for mv in s.execute(
                    select(MaskVersion).where(MaskVersion.mask_version_id.in_(mv_ids))
                ).scalars().all():
                    mv_map[mv.mask_version_id] = mv
            s.close()
    finally:
        engine.dispose()

    plan = sample_review_tasks(rows, thresholds=th)
    created: list[dict[str, Any]] = []
    engine = db._engine(url)
    try:
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            try:
                for group, ids in (("flagged", plan.flagged_ids), ("clean", plan.clean_ids)):
                    for mv_id in ids:
                        mv = mv_map.get(mv_id)
                        if mv is None:
                            continue
                        qc_id = next(
                            r["qc_report_id"] for r in rows
                            if r["mask_version_id"] == mv_id
                        )
                        rt_id = _ensure_review_task(
                            s, mv_id, mv.series_id, qc_id, group
                        )
                        created.append({
                            "review_task_id": rt_id, "mask_version_id": mv_id,
                            "series_id": mv.series_id, "group": group,
                        })
                s.commit()
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()
    finally:
        engine.dispose()
    return {"plan": plan.to_dict(), "tasks": created}


__all__ = [
    "QCOrchestrationError",
    "QC_THRESHOLDS_PATH",
    "generate_review_tasks",
    "qc_series",
]
