"""导入流水线编排（M1）：ingest -> 选择器 -> 导入报告 -> 落库 -> 试用计数。

自检门（proposal §5）：导入后先输出导入报告（Series 数、被跳过原因、层厚、方向、
HU 范围合理性），再开跑（落库）。CLI 与 API 都能取到同一报告（report.to_dict()）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import db
from .config import Settings
from .extract_guard import ExtractLimits
from .ingest import ImportReport, SeriesRecord, apply_series_rules, ingest
from .nifti_geom import reorient_ras
from .series_selector import MIN_SLICES, SeriesCandidate, select
from .storage import LocalFsStorage
from .usage_counter import TrialLimitError, check_trial_limits, fingerprint_hash, record_case_usage


class SliceGuardError(Exception):
    """commit 路径防线：< min_slices 的序列试图交给下游（未显式勾选覆盖）。"""


def guard_min_slices(
    candidates: list[SeriesCandidate],
    selected: list[str],
    user_overrides: list[str] | None = None,
) -> None:
    """commit 路径防线：拒绝把 < min_slices 的序列交给下游（M2 教师推理）。

    M2 的 TotalSegmentator 收到定位像残片会 3D 卷积报错。<min_slices 的序列
    只有用户显式勾选（user_overrides 含该 id，且该 id 本来被规则排除在层数上）
    才放行。
    """
    by_id = {c.series_id: c for c in candidates}
    overrides = set(user_overrides or [])
    for sid in selected:
        c = by_id.get(sid)
        if c is None:
            continue
        if c.num_slices < MIN_SLICES and sid not in overrides:
            raise SliceGuardError(
                f"series {sid} 层数 {c.num_slices} < {MIN_SLICES}，拒绝交给下游"
                f"（定位像/残片会导致 3D 卷积报错；显式 --include 勾选可覆盖）"
            )


@dataclass
class ImportOutcome:
    """一次导入的完整产物：报告 + 选择器结果 + 落库结果。"""

    report: ImportReport
    candidates: list[SeriesCandidate] = field(default_factory=list)
    selected_series_ids: list[str] = field(default_factory=list)
    case_ids: list[str] = field(default_factory=list)
    db_errors: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report": self.report.to_dict(),
            "selector": [c.to_report() for c in self.candidates],
            "selected_series_ids": list(self.selected_series_ids),
            "case_ids": list(self.case_ids),
            "db_errors": list(self.db_errors),
            "usage": dict(self.usage),
        }


def scan(
    path_or_stream,  # noqa: ANN001 — Path | BinaryIO | bytes
    settings: Settings,
    source_name: str = "",
    recursive: bool = True,
) -> ImportOutcome:
    """扫描阶段（自检门）：ingest + 选择器规则，产出导入报告，不落库、不计数。"""
    limits = ExtractLimits.from_settings(settings)
    report = ingest(path_or_stream, limits=limits, source_name=source_name, recursive=recursive)
    candidates = apply_series_rules(report)
    return ImportOutcome(
        report=report,
        candidates=candidates,
        selected_series_ids=[c.series_id for c in candidates if not c.excluded],
    )


def commit(
    outcome: ImportOutcome,
    settings: Settings,
    storage: LocalFsStorage,
    user_overrides: list[str] | None = None,
) -> ImportOutcome:
    """落库阶段（报告确认后调用）：建 Case/Series、写像素、维护试用计数。

    试用计数超限 → raise TrialLimitError（调用方决定如何呈现；不落半批数据——
    计数检查在写库前完成）。
    """
    selected = select(outcome.candidates, user_overrides=user_overrides)
    outcome.selected_series_ids = selected
    if not selected:
        outcome.usage = {"recorded": False, "reason": "no series selected"}
        return outcome
    # commit 路径防线：< min_slices 序列不交给下游（用户显式勾选才放行）
    guard_min_slices(outcome.candidates, selected, user_overrides=user_overrides)

    fp = fingerprint_hash()
    url = settings.database.url
    db.init_db(url)  # 幂等：CLI 直连路径无 lifespan，commit 前兜底建表
    _migrate_legacy_schema(url)  # M0 旧库兼容：cases/series 补 M0-fix 实体列
    engine = db._engine(url)
    try:
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            try:
                # 试用限制预检（无 license：年 200 / 月 20，先到者为准）
                check_trial_limits(s, fp)
                for rec in outcome.report.series:
                    if rec.series_id not in selected:
                        continue
                    case_id = _ensure_case(s, rec)
                    _ensure_series(s, rec, case_id)
                    _persist_source_instances(s, rec)  # M2-5(a)：源实例信息回填
                    key = _save_pixels(storage, rec)
                    if key is not None:
                        _backfill_storage_key(s, rec.series_id[:60], key)
                    if case_id not in outcome.case_ids:
                        outcome.case_ids.append(case_id)
                    # 计数写路径（幂等：重复导入同一 Series 不重复计）
                    usage = record_case_usage(s, case_id, fp)
                    outcome.usage = usage
                s.commit()
            except TrialLimitError:
                s.rollback()
                raise
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()
    finally:
        engine.dispose()
    return outcome


_LEGACY_COLUMNS: dict[str, dict[str, str]] = {
    # M0 旧库（commit 516a03e）缺 M0-fix 实体体系的列；SQLite ALTER TABLE 逐列补
    "cases": {
        "project_id": "TEXT REFERENCES projects(project_id)",
        "notes": "TEXT",
    },
    "series": {
        "slice_thickness_mm": "REAL",
        "orientation": "VARCHAR(32)",
        "hu_range": "VARCHAR(64)",
        "storage_key": "VARCHAR(512)",
        "source_instances_json": "JSON",  # M2-5(a)：源 DICOM 实例信息
    },
}


def _migrate_legacy_schema(url: str) -> None:
    """M0 旧库兼容：缺列时逐列 ALTER TABLE ADD COLUMN（幂等）。

    正式迁移走 Alembic（`alembic upgrade head`）；这里只兜底
    「M0 旧库 + init_db create_all 不改已存在表」的场景。
    """
    engine = db._engine(url)
    try:
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy import text

            for table, cols in _LEGACY_COLUMNS.items():
                existing = {
                    row[1]
                    for row in conn.execute(
                        text(f"PRAGMA table_info({table})")
                    ).fetchall()
                }
                if not existing:
                    continue  # 表不存在（全新库 create_all 已建对）
                for col, ddl in cols.items():
                    if col not in existing:
                        conn.execute(
                            text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                        )
            conn.commit()
    finally:
        engine.dispose()


def _ensure_case(session, rec: SeriesRecord) -> str:  # noqa: ANN001
    """按 case_key（StudyInstanceUID / NIfTI stem）取或建 Case。"""
    from sqlalchemy import select

    from .models import Case

    case_id = rec.case_key[:60]
    existing = session.execute(select(Case).where(Case.case_id == case_id)).scalar_one_or_none()
    if existing is None:
        session.add(Case(case_id=case_id, source="import"))
        session.flush()
    return case_id


def _ensure_series(session, rec: SeriesRecord, case_id: str) -> str:  # noqa: ANN001
    """取或建 Series（按 series_id 幂等）。"""
    from sqlalchemy import select

    from .models import Series

    existing = session.execute(
        select(Series).where(Series.series_id == rec.series_id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing.series_id
    session.add(
        Series(
            series_id=rec.series_id[:60],
            case_id=case_id,
            modality=rec.modality,
            num_slices=rec.num_slices,
            slice_thickness_mm=rec.slice_thickness_mm,
            orientation=rec.orientation,
            hu_range=f"{rec.hu_range[0]}..{rec.hu_range[1]}" if rec.hu_range else None,
            storage_key=None,  # 落盘后回填
        )
    )
    session.flush()
    return rec.series_id[:60]


def _backfill_storage_key(session, series_id: str, key: str) -> None:  # noqa: ANN001
    """像素落盘后回填 Series.storage_key。"""
    from sqlalchemy import select

    from .models import Series

    existing = session.execute(
        select(Series).where(Series.series_id == series_id)
    ).scalar_one_or_none()
    if existing is not None:
        existing.storage_key = key
        session.flush()


def _persist_source_instances(session, rec: SeriesRecord) -> None:  # noqa: ANN001
    """M2-5(a)：源 DICOM 实例信息持久化（ingest 提取 -> Series.source_instances_json）。

    DICOM 来源序列才有（NIfTI 输入无源实例，export_seg 走合成 fallback）。
    幂等：已有则不覆盖。
    """
    if rec.source_instances is None:
        return
    from sqlalchemy import select

    from .models import Series

    existing = session.execute(
        select(Series).where(Series.series_id == rec.series_id[:60])
    ).scalar_one_or_none()
    if existing is not None and existing.source_instances_json is None:
        existing.source_instances_json = rec.source_instances
        session.flush()


def load_source_instances(series_id: str, settings: Settings) -> dict[str, Any] | None:
    """从 DB 读回源实例信息（export_seg 真实引用路径的入口）。

    无记录 / 无 DICOM 来源（NIfTI 输入）返回 None —— 调用方走合成 fallback。
    """
    from sqlalchemy import select

    from . import db
    from .models import Series

    url = settings.database.url
    engine = db._engine(url)
    try:
        with engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            row = s.execute(
                select(Series).where(Series.series_id == series_id[:60])
            ).scalar_one_or_none()
            out = row.source_instances_json if row is not None else None
            s.close()
            return out
    finally:
        engine.dispose()


def source_info_from_persisted(
    persisted: dict[str, Any] | None, series_uid: str, study_uid: str, modality: str | None
) -> Any:
    """把持久化的源实例信息转成 export_seg.SourceSeriesInfo（真实引用路径）。

    persisted 结构见 ingest.extract_source_instances。调用方（export 联调层）
    传 series/study UID + modality（S3：移除 CT 缺省——模态必须显式给出，
    不静默按 CT 处理）；persisted 为 None 时调用方走合成 fallback。
    """
    from .export_seg import SourceSeriesInfo

    if persisted is None:
        raise ValueError(
            "source_info_from_persisted: persisted 为 None——调用方应走合成 fallback，"
            "不传 None 进本函数"
        )
    if not modality:
        raise ValueError(
            "source_info_from_persisted: modality 为 None/空——模态必须显式给出"
            "（M7-S3 8dc6549 同语义，不静默缺省）"
        )
    instances = persisted.get("instances", [])
    first = instances[0] if instances else {}
    iop = tuple(first.get("image_orientation_patient") or (1.0, 0.0, 0.0, 0.0, 1.0, 0.0))
    ps = first.get("pixel_spacing") or (1.0, 1.0)
    st = first.get("slice_thickness_mm") or 1.0
    src_insts = [
        {
            "sop_instance_uid": inst.get("sop_instance_uid", ""),
            "image_position_patient": inst.get("image_position_patient") or [0.0, 0.0, 0.0],
        }
        for inst in instances
    ]
    return SourceSeriesInfo(
        series_uid=series_uid,
        study_uid=study_uid,
        modality=modality,
        rows=int(persisted.get("rows", 0)),
        columns=int(persisted.get("columns", 0)),
        pixel_spacing=(float(ps[0]), float(ps[1]) if len(ps) > 1 else float(ps[0])),
        slice_thickness_mm=float(st),
        orientation_patient=tuple(float(v) for v in iop),
        source_instances=src_insts,
    )


def _save_pixels(storage: LocalFsStorage, rec: SeriesRecord) -> str | None:
    """像素落盘（NIfTI 转换产物；DICOM 转 NIfTI）。失败不中断（记 warning）。

    M1-fix：落盘前做 RAS+ 标准方向重排（nibabel as_closest_canonical），
    重排结果写回 rec.reorientation 供导入报告写明方向。
    """
    if rec.pixel_data is None:
        return None
    key = f"{rec.case_key[:60]}/{rec.series_id[:60]}/image.nii.gz"
    try:
        import tempfile

        import nibabel as nib
        import SimpleITK as sitk  # noqa: N813 — sitk 是官方惯用别名

        with tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False) as f:
            tmp = f.name
        try:
            sitk.WriteImage(rec.pixel_data, tmp)
            # RAS+ 标准方向重排（M1-fix：DICOM -> NIfTI 落盘前强制）
            nib_img = nib.load(tmp)
            nib_img, reorient_desc = reorient_ras(nib_img)
            rec.reorientation = reorient_desc
            nib.save(nib_img, tmp)
            data = Path(tmp).read_bytes()
        finally:
            Path(tmp).unlink(missing_ok=True)
        storage.save(key, data)
        return key
    except Exception:  # noqa: BLE001 — 像素落盘失败不中断导入
        return None
