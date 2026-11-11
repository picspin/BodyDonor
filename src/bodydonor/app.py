"""FastAPI 骨架（M0-fix + M1 ingest 端点）：健康检查、status、license、配置化限制、
DB 实体计数查看、Import Bundle 端点（scan / commit / selector）。

M1 ingest 端点在后端做 max_upload_mb 强制流式计数（前端预检/反代/后端三处之一，
仅 cloud_demo；proposal §4.3/§5），且在导入路径写 UsageLedger 并拦截超限
（LicenseState.trial_yearly/monthly_used；年 200 例 / 月 20 例）。
"""

from __future__ import annotations

import io
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from . import __version__
from .agent_context import scan_for_phi
from .config import Settings, load_settings
from .db import init_db
from .license_registry import LicenseRegistry
from .storage import Job, LocalFsStorage, SqliteJobsQueue

settings: Settings = load_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db(settings.database.url)
    yield


app = FastAPI(title="BodyDonor", version=__version__, lifespan=lifespan)

registry = LicenseRegistry(settings)

# 队列与存储（M3：队列状态源切 SQLite jobs 表，M2-REPORT 遗留 7）
queue = SqliteJobsQueue(settings.database.url)
try:
    storage = LocalFsStorage(settings.resolved_data_dir / "storage")
except ValueError:
    # 数据目录配置非法时延迟到首个写请求再报错；API 仍可启动
    storage = None  # type: ignore[assignment]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/status")
def status() -> dict[str, object]:
    return {
        "version": __version__,
        "deployment_mode": settings.deployment_mode,
        "max_upload_mb": settings.max_upload_mb,
        "data_dir": str(settings.resolved_data_dir),
        "queue_backend": settings.queue.backend,
        "queue_size": queue.qsize(),
        "storage_backend": settings.storage.backend,
        "teacher_engine": settings.teacher.engine,
        "allowed_tasks": len(settings.teacher.allowed_tasks),
    }


@app.get("/license")
def license_status() -> dict[str, object]:
    return registry.summary()


@app.get("/limits")
def limits() -> dict[str, object]:
    return {
        "deployment_mode": settings.deployment_mode,
        "max_upload_mb": settings.max_upload_mb,
        "extract_limits": settings.extract_limits.model_dump(),
        "cloud_demo": settings.limits.cloud_demo.model_dump(),
        "on_prem": settings.limits.on_prem.model_dump(),
    }


@app.post("/queue/enqueue")
def enqueue(case_id: str, stage: str) -> dict[str, str]:
    """M0 最小队列触发点；M1 起 ingest 自动入队。"""
    job = Job(job_id=f"{case_id}:{stage}", case_id=case_id, stage=stage)
    queue.enqueue(job)
    return {"job_id": job.job_id, "stage": job.stage, "status": "queued"}


@app.get("/queue/size")
def queue_size() -> dict[str, int]:
    return {"size": queue.qsize()}


@app.get("/license/usage")
def license_usage() -> dict[str, object]:
    """License 试用计数查看。

    M1 起：计数由导入路径维护（usage_counter.record_case_usage，幂等），
    本端点只读。完整 Ed25519 激活流程 M4/M5。多指纹部署（多库/多容器）
    时每库各自有 license_state 行；单机单库默认只有一行。
    """
    from sqlalchemy import select

    from .db import _engine
    from .models import LicenseState

    engine = _engine(settings.database.url)
    try:
        with engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            states = s.execute(select(LicenseState)).scalars().all()
            out = []
            for st in states:
                out.append(
                    {
                        "fingerprint_hash": st.fingerprint_hash[:16] + "...",
                        "license_id": st.license_id,
                        "mode": "licensed" if st.license_id else "trial",
                        "yearly": {"used": st.trial_yearly_used, "limit": st.trial_yearly_limit},
                        "monthly": {"used": st.trial_monthly_used, "limit": st.trial_monthly_limit},
                    }
                )
            s.close()
    finally:
        engine.dispose()
    return {"states": out, "enforcement": "active_on_import"}


@app.post("/agent-context/preview")
def agent_context_preview(case_id: str, series_id: str) -> JSONResponse:
    """演示 AgentContext 白名单构造 + PHI 自检。M2 起供编排层调用。"""
    from .agent_context import build_series_context

    ctx = build_series_context(
        case_id=case_id,
        series_id=series_id,
        series_metadata={
            "modality": "CT",
            "slice_thickness_mm": 1.0,
            "orientation": "axial",
            "num_slices": 300,
            "hu_range": [-1024, 3071],
        },
    )
    prompt = ctx.to_prompt()
    violations = scan_for_phi(prompt)
    if violations:
        raise HTTPException(status_code=500, detail=f"PHI leak: {violations}")
    return JSONResponse({"context": ctx.to_dict(), "prompt": prompt, "phi_violations": []})


# ---------------------------------------------------------------------------
# M1：Import Bundle 端点（scan 自检门 / commit 落库 / selector 勾选）
# ---------------------------------------------------------------------------

# 进程内 scan 结果缓存（scan -> 用户勾选 -> commit 两步的中间态；单进程 M1 足够）
_scan_cache: dict[str, Any] = {}


def _get_storage() -> LocalFsStorage:
    if storage is None:
        raise HTTPException(status_code=500, detail="data_dir 配置非法，存储不可用")
    return storage


@app.post("/ingest/scan")
async def ingest_scan(
    file: UploadFile = File(...),  # noqa: B008
    recursive: bool = Query(True),
) -> JSONResponse:
    """Import Bundle 上传通道（zip）：后端流式计数（100MB 限制三处之一，仅 cloud_demo）。

    流程：流式读取（超限即中断）-> safe_open_zip 三限预检 -> ingest 拆分 ->
    选择器规则 -> 导入报告（自检门）。返回 outcome_id 供 /ingest/commit 引用。
    """
    from .extract_guard import UploadLimitError, read_upload_stream
    from .pipeline import scan

    try:
        data = read_upload_stream(file.file, settings.max_upload_mb)
    except UploadLimitError as e:
        raise HTTPException(status_code=413, detail=str(e)) from None
    try:
        outcome = scan(io.BytesIO(data), settings, source_name=file.filename or "upload.zip")
    except Exception as e:  # noqa: BLE001 — ExtractLimitError/IngestError 统一 422
        raise HTTPException(status_code=422, detail=str(e)) from None

    outcome_id = f"scan-{len(_scan_cache) + 1}"
    _scan_cache[outcome_id] = outcome
    body = outcome.to_dict()
    body["outcome_id"] = outcome_id
    return JSONResponse(body)


@app.post("/ingest/{outcome_id}/commit")
def ingest_commit(
    outcome_id: str,
    include: list[str] | None = Query(  # noqa: B008 — FastAPI Query 默认值写法
        None, description="显式勾选的 series_id"
    ),
) -> JSONResponse:
    """落库（确认门之后）：建 Case/Series、写像素、维护试用计数。

    include：用户勾选的 series_id（勾回被规则排除的序列）；缺省 = 未被排除的全部。
    试用超限 -> 403；outcome_id 未知 -> 404。
    """
    from .pipeline import commit
    from .usage_counter import TrialLimitError

    outcome = _scan_cache.get(outcome_id)
    if outcome is None:
        raise HTTPException(status_code=404, detail=f"unknown outcome_id: {outcome_id}")
    try:
        storage = _get_storage()
        committed = commit(outcome, settings, storage, user_overrides=include)
    except TrialLimitError as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from None
    return JSONResponse(committed.to_dict())


@app.get("/ingest/{outcome_id}/selector")
def ingest_selector(outcome_id: str) -> JSONResponse:
    """Series 选择器列表（M1 CLI/API；Web UI 勾选在 M2）。

    返回规则过滤结果（excluded/exclude_reason/flags），用户据此调用
    /ingest/{outcome_id}/commit?include=...。
    """
    outcome = _scan_cache.get(outcome_id)
    if outcome is None:
        raise HTTPException(status_code=404, detail=f"unknown outcome_id: {outcome_id}")
    return JSONResponse(
        {
            "outcome_id": outcome_id,
            "candidates": [c.to_report() for c in outcome.candidates],
            "default_selected": [c.series_id for c in outcome.candidates if not c.excluded],
        }
    )


# ---------------------------------------------------------------------------
# M2：本体映射端点（三态查询 / 消歧写回 / 逃生通道）
# ---------------------------------------------------------------------------


@app.get("/ontology/resolve")
def ontology_resolve(label: str = Query(..., description="NL 标签")) -> JSONResponse:
    """NL 标签三态查询：resolved / ambiguous（附候选，交用户点选）/ unresolved。"""
    from .ontology import OntologyService

    svc = OntologyService(settings)
    return JSONResponse(svc.resolve(label).to_dict())


@app.post("/ontology/disambiguate")
def ontology_disambiguate(
    label: str = Query(..., description="ambiguous 态的 NL 标签"),
    totalseg_class: str = Query(..., description="用户点选的类别"),
) -> JSONResponse:
    """用户消歧写回（ambiguous -> 用户点选 -> overrides.yaml -> 下次 resolved）。"""
    from .ontology import OntologyError, OntologyService

    svc = OntologyService(settings)
    try:
        chosen = svc.disambiguate(label, totalseg_class)
    except OntologyError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return JSONResponse({"written_back": True, "entry": chosen.to_dict(), "version": svc.version})


@app.get("/ontology/escape-hatch")
def ontology_escape_hatch(label: str = Query(..., description="NL 标签")) -> JSONResponse:
    """实验逃生通道（SegVol/SAT）：默认关闭；开启时结果打 experimental 标记，
    绝不进蒸馏训练集。M2 只落地开关与隔离逻辑。"""
    from .ontology import OntologyService

    svc = OntologyService(settings)
    return JSONResponse(svc.escape_hatch(label))


@app.get("/ontology/summary")
def ontology_summary() -> dict[str, Any]:
    """映射表摘要（版本/条目数/overrides 数/逃生通道开关）。"""
    from .ontology import OntologyService

    svc = OntologyService(settings)
    return svc.summary()


# ---------------------------------------------------------------------------
# M2：监控端点（TUI 轮询数据源）：GPU / 队列 / 流水线状态
# ---------------------------------------------------------------------------

# 进程内流水线阶段注册表（proposal §6 的 8 阶段；worker/编排层更新，TUI 只读）
_pipeline_stages: dict[str, str] = {
    "1_ingest": "idle",
    "2_deid": "idle",
    "3_teacher_inference": "idle",
    "4_auto_qc": "idle",
    "5_manual_review": "idle",
    "6_distill_train": "idle",
    "7_relabel": "idle",
    "8_recheck": "idle",
}


def _gpu_snapshot() -> dict[str, Any]:
    """GPU 显存快照（nvidia-smi；不可用时返回 available=False，不猜数字）。"""
    import shutil
    import subprocess

    exe = shutil.which("nvidia-smi") or "/usr/lib/wsl/lib/nvidia-smi"
    try:
        out = subprocess.run(
            [
                exe,
                "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode != 0:
            return {"available": False, "reason": f"nvidia-smi exit {out.returncode}"}
        fields = [v.strip() for v in out.stdout.strip().split(",")]
        if len(fields) < 3:
            return {"available": False, "reason": "nvidia-smi 输出格式异常"}
        return {
            "available": True,
            "name": fields[0],
            "memory_used_mi_b": int(fields[1]),
            "memory_total_mi_b": int(fields[2]),
            "utilization_pct": int(fields[3]) if len(fields) > 3 and fields[3] else None,
        }
    except FileNotFoundError:
        return {"available": False, "reason": "nvidia-smi 不存在"}
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": "nvidia-smi 超时"}
    except (ValueError, IndexError) as e:
        return {"available": False, "reason": f"nvidia-smi 解析失败：{e}"}


@app.get("/monitor/gpu")
def monitor_gpu() -> dict[str, Any]:
    """GPU 显存/利用率快照（TUI 轮询）。不可用时 available=false（不猜数字）。"""
    return _gpu_snapshot()


@app.get("/monitor/pipeline")
def monitor_pipeline() -> dict[str, Any]:
    """流水线 8 阶段状态（TUI 轮询；队列状态源 = SQLite jobs 表，M3 落库）。"""
    return {
        "stages": dict(_pipeline_stages),
        "queue_size": queue.qsize(),
        "active_jobs": sorted(queue.pending_ids()),
        "job_status_counts": queue.status_counts(),
    }


@app.post("/monitor/pipeline/{stage}")
def monitor_pipeline_set(stage: str, status: str = Query("running")) -> dict[str, Any]:
    """更新流水线阶段状态（worker/编排层调用；TUI 只读不写）。"""
    if stage not in _pipeline_stages:
        raise HTTPException(status_code=404, detail=f"unknown stage: {stage}")
    _pipeline_stages[stage] = status
    return {"stage": stage, "status": status}


# ---------------------------------------------------------------------------
# M3：QC 端点（阶段 4 评分 / 阶段 5 抽检任务 / 抽检决策回流）
# ---------------------------------------------------------------------------


@app.post("/qc/series/{series_id}")
def qc_run_series(series_id: str) -> JSONResponse:
    """阶段 4（自动质控）：对最新 MaskVersion 评分并落 QCReport。"""
    from .qc_pipeline import QCOrchestrationError, qc_series

    try:
        out = qc_series(series_id, settings)
    except QCOrchestrationError as e:
        msg = str(e)
        status = 404 if "unknown" in msg else 422
        raise HTTPException(status_code=status, detail=msg) from None
    return JSONResponse(out)


@app.get("/qc/thresholds")
def qc_thresholds_summary() -> dict[str, Any]:
    """QC 阈值配置（source 注记可见：cold_start | calibrated_on_N）。"""
    from .qc import load_qc_thresholds

    return load_qc_thresholds()


@app.post("/qc/review-tasks")
def qc_review_tasks_create(
    batch_size: int = Query(0, description="0 = 用 config 的 batch_size"),
) -> JSONResponse:
    """阶段 5（人工抽检）：混合采样生成 ReviewTask（flagged 70% / clean 30%）。"""
    from .qc_pipeline import generate_review_tasks

    out = generate_review_tasks(settings, batch_size=batch_size if batch_size > 0 else None)
    return JSONResponse(out)


@app.get("/qc/review-tasks")
def qc_review_tasks_list(status: str = Query("pending")) -> dict[str, Any]:
    """抽检任务列表（审阅台数据源；status 过滤，缺省 pending）。"""
    from sqlalchemy import select

    from . import db
    from .models import MaskVersion, QCReport, ReviewTask

    engine = db._engine(settings.database.url)
    try:
        with engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            rows = s.execute(
                select(ReviewTask, QCReport, MaskVersion)
                .join(QCReport, ReviewTask.qc_report_id == QCReport.qc_report_id)
                .join(
                    MaskVersion,
                    QCReport.mask_version_id == MaskVersion.mask_version_id,
                )
                .where(ReviewTask.status == status)
                .order_by(ReviewTask.created_at.desc())
                .limit(50)
            ).all()
            s.close()
    finally:
        engine.dispose()
    tasks = []
    for rt, qc, mv in rows:
        tasks.append(
            {
                "review_task_id": rt.review_task_id,
                "series_id": rt.series_id,
                "mask_version_id": qc.mask_version_id,
                "mask_version": mv.version,
                "mask_source": mv.source,
                "qc_report_id": qc.qc_report_id,
                "overall_score": qc.overall_score,
                "qc_flags": qc.flags,
                "qc_passed": qc.passed,
                "status": rt.status,
                "notes": rt.notes,
            }
        )
    return {"status": status, "tasks": tasks}


@app.post("/qc/review-tasks/{review_task_id}/decision")
def qc_review_task_decision(
    review_task_id: str,
    decision: str = Query(..., description="accepted | corrected | rejected"),
    notes: str = Query(""),
) -> dict[str, Any]:
    """抽检决策（接受/修正/拒绝）——在线校准的标签回流入口（proposal §7）。

    决策写进 ReviewTask（status/decision），并累计进 qc_thresholds.yaml 的
    calibrated_on 计数（阈值本体由 bodydonor qc-calibrate 重算）。
    """
    from sqlalchemy import select

    from . import db
    from .models import ReviewTask
    from .qc_reflux import record_review_decision

    if decision not in ("accepted", "corrected", "rejected"):
        raise HTTPException(status_code=422, detail="decision 必须是 accepted|corrected|rejected")
    engine = db._engine(settings.database.url)
    try:
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            try:
                rt = s.execute(
                    select(ReviewTask).where(ReviewTask.review_task_id == review_task_id[:60])
                ).scalar_one_or_none()
                if rt is None:
                    raise HTTPException(
                        status_code=404, detail=f"unknown review task: {review_task_id}"
                    )
                rt.status = decision
                rt.decision = decision
                if notes:
                    rt.notes = (rt.notes or "") + (f" | {notes}" if rt.notes else notes)
                s.commit()
            finally:
                s.close()
    finally:
        engine.dispose()
    reflux = record_review_decision(settings, decision)
    return {"review_task_id": review_task_id, "decision": decision, "reflux": reflux}


# ---------------------------------------------------------------------------
# M2：niivue 审阅端点（静态页 + meta/image/mask/seg-meta/save）
# ---------------------------------------------------------------------------

from contextlib import contextmanager as _ctxmgr  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

_STATIC_DIR = _Path(__file__).resolve().parent / "static"


@_ctxmgr
def _nifti_from_bytes(payload: bytes) -> Any:
    """bytes -> nibabel 图像（临时文件，nibabel 5.4.2 load() 只吃文件路径）。

    nibabel 5.x 的 load() 移除了 fileobj 支持（os.stat 校验），落临时 .nii.gz
    读取，用完即删（与 pipeline._save_pixels 同模式）。
    """
    import tempfile

    import nibabel as nib

    with tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False) as tf:
        tmp_name = tf.name
    try:
        _Path(tmp_name).write_bytes(payload)
        yield nib.load(tmp_name)
    finally:
        _Path(tmp_name).unlink(missing_ok=True)


@app.get("/review")
def review_page() -> Any:
    """审阅页（niivue 三正交面 + MIP + mask 逐类开关 + 修正导出）。"""
    from fastapi.responses import HTMLResponse

    return HTMLResponse((_STATIC_DIR / "review.html").read_text(encoding="utf-8"))


@app.get("/static/{filename}")
def static_file(filename: str) -> Any:
    """本地 vendored 静态文件（niivue.umd.js 等；不走 CDN，院内可离线）。"""
    from fastapi.responses import FileResponse

    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=404, detail="invalid filename")
    path = _STATIC_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"not found: {filename}")
    media = "application/javascript" if filename.endswith(".js") else "text/html"
    return FileResponse(path, media_type=media)


@app.post("/segments")
def segments_create(
    series_id: str = Query(..., description="已 ingest 的 Series ID"),
    task: str = Query("total", description="TS 任务（白名单判定）"),
    device: str = Query("gpu", description="gpu | cpu"),
) -> JSONResponse:
    """TotalSegmentator worker 入口（同步）：原图 → 多类 mask → MaskVersion。

    白名单判定（blocked 一票否决）→ 422；<30 层防线 → 422；CLI 未知 → 404。
    """
    from .segmentation import SegmentWorkerError, segment_series

    try:
        result = segment_series(series_id, settings, task=task, device=device)
    except SegmentWorkerError as e:
        msg = str(e)
        status = 404 if "unknown series" in msg else 422
        raise HTTPException(status_code=status, detail=msg) from None
    return JSONResponse(result.to_dict())


def _series_row(series_id: str) -> Any:
    from sqlalchemy import select

    from . import db
    from .models import Series

    engine = db._engine(settings.database.url)
    try:
        with engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            row = s.execute(
                select(Series).where(Series.series_id == series_id[:60])
            ).scalar_one_or_none()
            s.close()
            return row
    finally:
        engine.dispose()


@app.get("/review/{series_id}/meta")
def review_meta(series_id: str) -> dict[str, Any]:
    """序列元数据 + mask 存在性 + license 展示（审阅页首屏；M3 收敛：单一产物）。

    M4 阶段 3 加 license 字段：teacher 权重 Apache-2.0 + nnInteractive 权重
    CC BY-NC-SA 4.0 + 指向 LICENSES-THIRD-PARTY.md；该 series 有 nnInteractive
    修正产物（correction_source.json sidecar）时加 correction_source 提示
    （不进商用蒸馏训练集）。
    """
    row = _series_row(series_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown series: {series_id}")
    st = _get_storage()
    # M3 收敛（遗留 (g)）：combined_mask.nii.gz 不再落盘；has_combined 仅对
    # 历史产物为 true（兼容字段）
    has_combined = bool(row.storage_key) and st.exists(
        row.storage_key.replace("image.nii.gz", "combined_mask.nii.gz")
    )
    has_mask = has_combined or (
        bool(row.storage_key) and st.exists(row.storage_key.replace("image.nii.gz", "mask.nii.gz"))
    )
    out: dict[str, Any] = {
        "series_id": row.series_id,
        "case_id": row.case_id,
        "modality": row.modality,
        "num_slices": row.num_slices,
        "slice_thickness_mm": row.slice_thickness_mm,
        "has_mask": has_mask,
        "has_combined_mask": has_combined,
        "license": {
            "teacher_weights": "Apache-2.0（TotalSegmentator total 任务，任意用途）",
            "nninteractive_weights": (
                "CC BY-NC-SA 4.0（非商业 + ShareAlike）：仅交互式修正辅助；"
                "修正产物不进商用蒸馏训练集"
            ),
            "reference": "LICENSES-THIRD-PARTY.md §2.2/§7/§8",
        },
    }
    # 该 series 有 nnInteractive 修正产物（sidecar 在 series 目录）时提示
    if row.storage_key:
        series_dir = "/".join(row.storage_key.split("/")[:2])
        sidecar_key = f"{series_dir}/nninteractive-correction/correction_source.json"
        if st.exists(sidecar_key):
            try:
                import json as _json

                doc = _json.loads(bytes(st.load(sidecar_key)))
                out["correction_source"] = doc.get("source", "nninteractive_guided")
                out["correction_license_notice"] = (
                    "本序列含 nnInteractive 修正产物（CC BY-NC-SA 4.0）：不进商用蒸馏训练集"
                )
            except Exception:  # noqa: BLE001 — sidecar 损坏不挡 meta
                pass
    return out


@app.get("/review/{series_id}/image.nii.gz")
def review_image(series_id: str) -> Any:
    """序列像素 NIfTI（RAS+ 标准方向，commit 时落盘）。"""
    from fastapi.responses import Response

    row = _series_row(series_id)
    if row is None or not row.storage_key:
        raise HTTPException(status_code=404, detail="no image for series")
    st = _get_storage()
    try:
        data = st.load(row.storage_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="image file missing") from None
    return Response(
        content=data,
        media_type="application/gzip",
        headers={"Content-Disposition": "inline"},
    )


@app.get("/review/{series_id}/mask.nii.gz")
def review_mask(series_id: str) -> Any:
    """最新 MaskVersion 的 mask NIfTI（多类）。

    M2-align 3.5：worker 的合并产物是 combined_mask.nii.gz（uint8 多标签），
    与最新 MaskVersion 的多类标签图同内容；审阅页只加载 1 原图 + 1 合并 mask。
    """
    from fastapi.responses import Response
    from sqlalchemy import select

    from . import db
    from .models import MaskVersion

    st = _get_storage()
    engine = db._engine(settings.database.url)
    try:
        with engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            mv = (
                s.execute(
                    select(MaskVersion)
                    .where(MaskVersion.series_id == series_id[:60])
                    .order_by(MaskVersion.version.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            s.close()
    finally:
        engine.dispose()
    if mv is None:
        # 无 MaskVersion 记录时 fallback：裸 mask.nii.gz（teacher 初版落盘产物；
        # M3 收敛后 combined_mask.nii.gz 不再单独落盘，历史产物仍兼容）
        row = _series_row(series_id)
        if row and row.storage_key:
            mask_key = row.storage_key.replace("image.nii.gz", "mask.nii.gz")
            if st.exists(mask_key):
                return Response(content=st.load(mask_key), media_type="application/gzip")
        raise HTTPException(status_code=404, detail="no mask version for series")
    try:
        data = st.load(mv.storage_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="mask file missing") from None
    return Response(content=data, media_type="application/gzip")


@app.get("/review/{series_id}/labels.json")
def review_labels(series_id: str) -> Any:
    """labels.json（M2-align 3.5）：索引 -> totalseg_class/中英文名/颜色 映射。

    审阅页逐类开关/换色/体素数统计的数据源（不硬编码进页面 JS）。
    """
    from fastapi.responses import Response

    row = _series_row(series_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown series: {series_id}")
    st = _get_storage()
    if not row.storage_key:
        raise HTTPException(status_code=404, detail="no labels for series")
    labels_key = row.storage_key.replace("image.nii.gz", "labels.json")
    if not st.exists(labels_key):
        raise HTTPException(status_code=404, detail="no labels.json（旧版 mask 或无合并产物）")
    return Response(
        content=st.load(labels_key),
        media_type="application/json",
        headers={"Content-Disposition": "inline"},
    )


@app.get("/review/{series_id}/seg-meta")
def review_seg_meta(series_id: str) -> list[dict[str, Any]]:
    """mask 类别列表（class 名 + 体素数），审阅页逐类开关用。

    M2-align 3.5：优先 combined_mask.nii.gz（uint8 多标签）。有合并产物时
    **列出 labels.json 的全部类别**（体素数 0 = 该类在本序列非空为空），
    labels.json 类别数与审阅页列表一致（协议一致性）；无合并产物（旧版 mask）
    时只列 mask 里非空的 label。
    """
    row = _series_row(series_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown series: {series_id}")
    st = _get_storage()
    if not row.storage_key:
        return []
    combined_key = row.storage_key.replace("image.nii.gz", "combined_mask.nii.gz")
    mask_key = row.storage_key.replace("image.nii.gz", "mask.nii.gz")
    key = combined_key if st.exists(combined_key) else mask_key
    if not st.exists(key):
        return []

    # labels.json（TS 输出类别名单，M3 遗留 (d) 衔接）-> index -> totalseg_class/中英文名。
    # labels.json 由 worker 推理落盘（build_labels_json，源自 TS 输出文件名 +
    # 本体映射表）；manual 修正版本沿用同一份（label 值不变），审阅页逐类
    # 开关/换色/体素统计的数据源。
    label_names: dict[int, dict[str, Any]] = {}
    series_dir = "/".join((row.storage_key or "").split("/")[:2])
    labels_key = f"{series_dir}/labels.json"
    if st.exists(labels_key):
        import json as _json

        try:
            doc = _json.loads(bytes(st.load(labels_key)))
            for lb in doc.get("labels", []):
                label_names[int(lb["index"])] = lb
        except Exception:  # noqa: BLE001 — labels.json 损坏不挡 seg-meta
            label_names = {}

    import numpy as np

    with _nifti_from_bytes(st.load(key)) as img:
        arr = np.asarray(img.dataobj)
        # 有 labels.json：列全部类别（labels.json 类别数与列表一致——协议契约）；
        # 无 labels.json（旧版 mask）：只列非空 label
        classes = (
            sorted(label_names)
            if label_names
            else sorted(set(int(v) for v in np.unique(arr)) - {0})
        )
    return [
        {
            "class": f"label-{v}",
            "label_value": v,
            "n_voxels": int((arr == v).sum()),
            **(
                {
                    "totalseg_class": label_names[v]["totalseg_class"],
                    "name_cn": label_names[v].get("name_cn", ""),
                    "name_en": label_names[v].get("name_en", ""),
                    "color": label_names[v].get("color"),
                }
                if v in label_names
                else {}
            ),
        }
        for v in classes
    ]


@app.post("/review/{series_id}/save")
def review_save(series_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """人工修正写回（M2 粒度：整类删除 + 画笔粗修）-> 新 MaskVersion。

    body: {deleted_classes: [class,...], drawing_nifti: [bytes] | null,
           source?: "manual" | "human", provenance?: dict | null}
    - 整类删除：deleted_classes 里的 label 体素清零（走 assert_affine_match 守卫）
    - 画笔粗修：drawing_nifti 非零体素从 mask 中删除（粗修擦除）
    - source（M5-e）：默认 "manual"（legacy 画笔值，兼容旧调用）；前端画笔
      保存传 "human"（M5 规范画笔修正，与 nninteractive_guided 可分别统计）。
      nninteractive_guided 不接受本端点写（走 submit-nni）。
    - provenance（M5-e）：可选血缘 dict，存 MaskVersion.provenance 列。
    写回前 affine 守卫（mask 与原图不一致 raise AffineMismatchError，禁止静默重采样）。
    """
    import nibabel as nib
    import numpy as np

    row = _series_row(series_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown series: {series_id}")
    st = _get_storage()
    img_key = row.storage_key
    mask_key = (row.storage_key or "").replace("image.nii.gz", "mask.nii.gz")
    if not mask_key or not st.exists(mask_key):
        raise HTTPException(status_code=422, detail="no mask to correct")

    from .nifti_geom import AffineMismatchError, assert_affine_match

    try:
        with (
            _nifti_from_bytes(st.load(img_key)) as img,
            _nifti_from_bytes(st.load(mask_key)) as mask,
        ):
            # M1-fix (b)：affine 守卫——不一致硬报错，禁止静默重采样
            assert_affine_match(img, mask, label=f"review_save {series_id}")
            affine = mask.affine
            arr = np.asarray(mask.dataobj).astype(np.int32)

            deleted = body.get("deleted_classes", [])
            for cls in deleted:
                # class 名形如 "label-3"；整类体素清零
                try:
                    label_value = int(str(cls).removeprefix("label-"))
                except ValueError:
                    continue
                arr[arr == label_value] = 0

            drawing = body.get("drawing_nifti")
            if drawing:
                # 画笔粗修：非零体素从 mask 中删除
                with _nifti_from_bytes(bytes(bytearray(drawing))) as draw_img:
                    assert_affine_match(img, draw_img, label=f"review_save drawing {series_id}")
                    draw_arr = np.asarray(draw_img.dataobj)
                arr[draw_arr > 0] = 0
    except AffineMismatchError as e:
        # affine 不一致 → 422（不静默重采样，错误带 affine 详情）
        raise HTTPException(status_code=422, detail=str(e)) from None

    # 新 MaskVersion
    from sqlalchemy import select

    from . import db
    from .models import MaskVersion

    engine = db._engine(settings.database.url)
    try:
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            try:
                last = (
                    s.execute(
                        select(MaskVersion.version)
                        .where(MaskVersion.series_id == series_id[:60])
                        .order_by(MaskVersion.version.desc())
                        .limit(1)
                    )
                    .scalars()
                    .first()
                )
                version = (last or 0) + 1
                # nib.save 需要 .nii.gz 后缀路径（BytesIO 不含 gzip 判定所需后缀语义）
                import tempfile

                with tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False) as tf:
                    tmp_name = tf.name
                try:
                    nib.save(nib.Nifti1Image(arr, affine), tmp_name)
                    payload = _Path(tmp_name).read_bytes()
                finally:
                    _Path(tmp_name).unlink(missing_ok=True)
                key = mask_key.replace("image.nii.gz", f"mask_v{version}.nii.gz")
                st.save(key, payload)
                save_source = str(body.get("source", "manual"))
                if save_source not in ("manual", "human"):
                    raise HTTPException(
                        status_code=422,
                        detail="review_save 只接受 source=manual|human；"
                        "nninteractive_guided 走 /review/{series_id}/submit-nni",
                    )
                provenance = body.get("provenance")
                if provenance is not None and not isinstance(provenance, dict):
                    raise HTTPException(status_code=422, detail="provenance 须为 dict 或 null")
                s.add(
                    MaskVersion(
                        mask_version_id=f"mv-{uuid.uuid4().hex[:8]}",
                        series_id=series_id[:60],
                        version=version,
                        source=save_source,
                        storage_key=key,
                        created_by="review-ui",
                        provenance=provenance,
                    )
                )
                s.commit()
            finally:
                s.close()
    finally:
        engine.dispose()
    return {"version": version, "source": save_source, "key": key}


# ---------------------------------------------------------------------------
# M4 阶段 3：nnInteractive 交互修正端点（与 CLI nninteractive-correct 同一后端）
# ---------------------------------------------------------------------------


@app.post("/review/{series_id}/nninteractive-correct")
def review_nninteractive_correct(series_id: str, body: dict[str, Any]) -> JSONResponse:
    """nnInteractive 交互修正（M4 阶段 3 + M5-e 后接线）：point/bbox/scribble → 修正 mask。

    body 两种形式：
    - 新（M5-e 后）：{prompts: [...], target_label?, device?, job_id?}，prompts 为
      {type: point, x,y,z, include} | {type: bbox, bbox: [[x0,x1],[y0,y1],[z0,z1]]} |
      {type: scribble, points: [[x,y,z],...]} 的列表（与 runner 契约一致）。
    - 旧（M4 兼容保留）：{point: {x,y,z}, negative_point?: {x,y,z}, ...}
      （与 CLI 同一后端函数 correct_image。）
    - GPU 忙（批量推理/训练持锁）→ 409 + 清晰错误，不排队不重试
    - 修正产物落 series 目录 nninteractive-correction/ + sidecar JSON
      （source=nninteractive_guided 规范名 v2，绝不建 MaskVersion 行——
      蒸馏门禁以 sidecar 为事实源；
      provenance={sessionId, baselineTeacherVersion, prompts, acceptedClasses}）
    - 同步跑推理的取舍：单次交互修正秒级~分钟级，API 只接线、
      不做异步队列（队列后置；长推理不适合同步时由调用方用 CLI）
    """
    from .nninteractive_backend import (
        GpuBusyError,
        NnInteractiveError,
        correct_image,
    )

    row = _series_row(series_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown series: {series_id}")
    if not row.storage_key:
        raise HTTPException(status_code=422, detail="series 无像素（commit 未落盘）")
    st = _get_storage()
    image_path = st.root / row.storage_key
    if not image_path.exists():
        raise HTTPException(status_code=422, detail=f"原图文件缺失：{row.storage_key}")

    raw_prompts = body.get("prompts")
    if raw_prompts is not None:
        # 新契约：prompts 列表（point/bbox/scribble），沿用 runner 的 JSON 风格。
        if not isinstance(raw_prompts, list) or not raw_prompts:
            raise HTTPException(status_code=422, detail="prompts 须为非空列表")
        prompts: list[dict[str, Any]] = []
        for i, p in enumerate(raw_prompts):
            if not isinstance(p, dict):
                raise HTTPException(status_code=422, detail=f"prompts[{i}] 须为对象")
            ptype = p.get("type")
            if ptype == "point":
                try:
                    prompts.append(
                        {
                            "type": "point",
                            "x": float(p["x"]),
                            "y": float(p["y"]),
                            "z": float(p["z"]),
                            "include": bool(p.get("include", True)),
                        }
                    )
                except (KeyError, TypeError, ValueError):
                    raise HTTPException(
                        status_code=422, detail=f"prompts[{i}] point 须含数值 x/y/z"
                    ) from None
            elif ptype == "bbox":
                bb = p.get("bbox")
                if not isinstance(bb, list) or len(bb) != 3 or any(
                    not isinstance(ax, list) or len(ax) != 2 for ax in bb
                ):
                    raise HTTPException(
                        status_code=422,
                        detail=f"prompts[{i}] bbox 须为 [[x0,x1],[y0,y1],[z0,z1]]",
                    )
                try:
                    prompts.append(
                        {"type": "bbox", "bbox": [[float(v) for v in ax] for ax in bb]}
                    )
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=422, detail=f"prompts[{i}] bbox 数值非法"
                    ) from None
            elif ptype == "scribble":
                pts = p.get("points")
                if not isinstance(pts, list) or not pts:
                    raise HTTPException(
                        status_code=422, detail=f"prompts[{i}] scribble 须为非空 points 列表"
                    )
                try:
                    prompts.append(
                        {
                            "type": "scribble",
                            "points": [[float(v[0]), float(v[1]), float(v[2])] for v in pts],
                        }
                    )
                except (KeyError, TypeError, ValueError, IndexError):
                    raise HTTPException(
                        status_code=422,
                        detail=f"prompts[{i}] scribble points 须为 [x,y,z] 数值三元组",
                    ) from None
            else:
                raise HTTPException(
                    status_code=422,
                    detail=f"prompts[{i}] type 须为 point|bbox|scribble，收到 {ptype!r}",
                )
    else:
        # 旧契约（M4 兼容保留）：point + negative_point。
        point = body.get("point")
        if not isinstance(point, dict):
            raise HTTPException(
                status_code=422,
                detail="body 须含 prompts 列表，或（旧契约）point {x,y,z} 对象",
            )
        try:
            prompts = [
                {
                    "type": "point",
                    "x": float(point["x"]),
                    "y": float(point["y"]),
                    "z": float(point["z"]),
                    "include": True,
                }
            ]
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=422, detail="point 须含数值 x/y/z") from None
        neg = body.get("negative_point")
        if neg is not None:
            if not isinstance(neg, dict):
                raise HTTPException(status_code=422, detail="negative_point 须为 {x,y,z} 对象")
            try:
                prompts.append(
                    {
                        "type": "point",
                        "x": float(neg["x"]),
                        "y": float(neg["y"]),
                        "z": float(neg["z"]),
                        "include": False,
                    }
                )
            except (KeyError, TypeError, ValueError):
                raise HTTPException(
                    status_code=422, detail="negative_point 须含数值 x/y/z"
                ) from None

    series_dir = "/".join(row.storage_key.split("/")[:2])
    out_dir = st.root / series_dir / "nninteractive-correction"
    try:
        result = correct_image(
            image_path,
            out_dir,
            prompts,
            device=str(body.get("device", "auto")),
            target_label=int(body.get("target_label", 1)),
            settings=settings,
            job_id=str(body.get("job_id", "")),
        )
    except GpuBusyError as e:
        # GPU 忙：409 + 清晰错误（不排队不重试）
        raise HTTPException(status_code=409, detail=str(e)) from None
    except NnInteractiveError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return JSONResponse(result.to_dict())


# ---------------------------------------------------------------------------
# M5：修正传播 + 异常驱动队列
# ---------------------------------------------------------------------------


@app.post("/review/{series_id}/propagate")
def review_propagate(series_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """把 z 层上某类的当前形态传播到相邻层（M5-PLAN §3.1，最优先）。

    body: {slice: int, class_label: int, radius?: int = 1}
    - 修正源 = 该层该类的现有体素（前端先画笔修正本层、再点传播）。
    - 只写相邻层背景体素、不越类（review_session.propagate_correction）。
    - 写回新 MaskVersion source=manual（画笔修正语义；nninteractive_guided
      20 字符是 M5-e 的事，列宽未改前不写该来源）。
    - affine 守卫同 review_save；0 传播时拒绝建空版本（422）。
    """
    import nibabel as nib
    import numpy as np

    from .review_session import propagate_correction

    row = _series_row(series_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown series: {series_id}")
    st = _get_storage()
    img_key = row.storage_key
    mask_key = (row.storage_key or "").replace("image.nii.gz", "mask.nii.gz")
    if not mask_key or not st.exists(mask_key):
        raise HTTPException(status_code=422, detail="no mask to correct")
    try:
        z = int(body.get("slice"))
        label = int(body.get("class_label"))
        radius = int(body.get("radius", 1))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422, detail="body 须含整数 slice / class_label（radius 可选）"
        ) from None
    if radius < 0 or radius > 5:
        raise HTTPException(status_code=422, detail="radius 须在 0..5")

    from .nifti_geom import AffineMismatchError, assert_affine_match

    try:
        with (
            _nifti_from_bytes(st.load(img_key)) as img,
            _nifti_from_bytes(st.load(mask_key)) as mask,
        ):
            assert_affine_match(img, mask, label=f"review_propagate {series_id}")
            affine = mask.affine
            arr = np.asarray(mask.dataobj).astype(np.int32)
            new_arr, total, per_slice = propagate_correction(arr, z, label, radius)
    except AffineMismatchError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    if total == 0:
        raise HTTPException(
            status_code=422,
            detail=f"slice {z} 上 label {label} 无可传播体素（本层无该类或相邻层投影全被占）",
        )

    from sqlalchemy import select

    from . import db
    from .models import MaskVersion

    engine = db._engine(settings.database.url)
    try:
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            try:
                last = (
                    s.execute(
                        select(MaskVersion.version)
                        .where(MaskVersion.series_id == series_id[:60])
                        .order_by(MaskVersion.version.desc())
                        .limit(1)
                    )
                    .scalars()
                    .first()
                )
                version = (last or 0) + 1
                import tempfile

                with tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False) as tf:
                    tmp_name = tf.name
                try:
                    nib.save(nib.Nifti1Image(new_arr, affine), tmp_name)
                    payload = _Path(tmp_name).read_bytes()
                finally:
                    _Path(tmp_name).unlink(missing_ok=True)
                key = mask_key.replace("image.nii.gz", f"mask_v{version}.nii.gz")
                st.save(key, payload)
                s.add(
                    MaskVersion(
                        mask_version_id=f"mv-{uuid.uuid4().hex[:8]}",
                        series_id=series_id[:60],
                        version=version,
                        source="manual",
                        storage_key=key,
                        created_by="review-ui-propagate",
                    )
                )
                s.commit()
            finally:
                s.close()
    finally:
        engine.dispose()
    return {
        "version": version,
        "source": "manual",
        "key": key,
        "slice": z,
        "class_label": label,
        "radius": radius,
        "propagated_voxels": total,
        "per_slice": {str(k): v for k, v in per_slice.items()},
    }


@app.get("/review/{series_id}/risk-queue")
def review_risk_queue(series_id: str) -> dict[str, Any]:
    """异常驱动队列（M5-PLAN §3.3）：逐类 Dice + 多教师一致性 flag → 审阅顺序。

    数据源 = eval/student_holdout_results.json（summary.per_class_dice，
    M4 实测）+ eval/student_teacher_consistency.json（below_080_classes，
    阶段 5）。运行产物不入仓：文件缺失 → 503 + 清晰原因，不编造数据。
    """
    import json

    from .review_session import risk_queue

    row = _series_row(series_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown series: {series_id}")
    repo_root = _Path(__file__).resolve().parents[2]
    holdout_path = repo_root / "eval" / "student_holdout_results.json"
    if not holdout_path.exists():
        raise HTTPException(
            status_code=503,
            detail="学生 hold-out 结果缺失（eval/student_holdout_results.json 不存在）："
            "先跑 eval/evaluate_student_holdout.py，风险队列不编造 Dice",
        )
    try:
        holdout = json.loads(holdout_path.read_text(encoding="utf-8"))
        per_class = (holdout.get("summary") or {}).get("per_class_dice") or (
            holdout.get("gate") or {}
        ).get("per_class_dice")
        if not isinstance(per_class, dict) or not per_class:
            raise ValueError("per_class_dice 缺失或为空")
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=503, detail=f"学生 hold-out 结果不可读：{e}") from None
    flagged: list[str] = []
    consistency_path = repo_root / "eval" / "student_teacher_consistency.json"
    if consistency_path.exists():
        try:
            flagged = list(
                json.loads(consistency_path.read_text(encoding="utf-8")).get(
                    "below_080_classes", []
                )
            )
        except (ValueError, OSError):
            flagged = []
    return {
        "series_id": series_id,
        "queue": risk_queue({k: float(v) for k, v in per_class.items()}, flagged),
        "sources": {
            "per_class_dice": "eval/student_holdout_results.json（M4 实测，15 类）",
            "consistency_flags": (
                "eval/student_teacher_consistency.json below_080_classes（阶段 5）"
                if consistency_path.exists()
                else "缺失：仅按 Dice 排序"
            ),
        },
    }


@app.post("/review/{series_id}/submit-nni")
def review_submit_nni(series_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """提交已确认的 nnInteractive 修正 → 新 MaskVersion（M5-e）。

    body: {target_label: int, prompts: [...], acceptedClasses: [name,...],
           sessionId?: str, baselineTeacherVersion?: int | null}
    - 修正 mask 取 series 目录 nninteractive-correction/correction_mask.nii.gz
      （M4 correct_image 产物，二值 0/1）；sidecar correction_source.json 须
      存在且 source=nninteractive_guided，否则 422（无修正产物不可提交）。
    - 合并：correction>0 的体素写为 target_label（其余沿用基线 mask）。
    - affine 守卫同 review_save；provenance 存 MaskVersion.provenance 列
      （build_provenance 校验 prompts 可重放 + acceptedClasses 非空）。
    - source=nninteractive_guided（20 字符，列宽 String(32)，迁移 e7f3a1c9d204）。
      蒸馏门禁仍拒绝该来源（ontology/distill_data，不进商用蒸馏训练集）。
    """
    import json as _json

    import nibabel as nib
    import numpy as np

    from .review_session import build_provenance

    row = _series_row(series_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown series: {series_id}")
    st = _get_storage()
    if not row.storage_key:
        raise HTTPException(status_code=422, detail="series 无像素（commit 未落盘）")
    try:
        target_label = int(body.get("target_label"))
        prompts = body.get("prompts") or []
        accepted = body.get("acceptedClasses") or []
        session_id = str(body.get("sessionId") or f"submit-{uuid.uuid4().hex[:8]}")
        baseline = body.get("baselineTeacherVersion")
        if baseline is not None:
            baseline = int(baseline)
        if target_label <= 0:
            raise ValueError("target_label 须 > 0")
        if not isinstance(prompts, list) or not isinstance(accepted, list):
            raise ValueError("prompts / acceptedClasses 须为 list")
        provenance = build_provenance(session_id, baseline, prompts, accepted)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=422, detail=f"提交 payload 非法：{e}") from None
    if baseline is None:
        # M5-e：前端未带基线版本时，后端按 M4 口径查最新 MaskVersion 版本号
        # （修正所基于的显示基线；查不到记 null + 原因，不猜数字）。
        from .nninteractive_backend import _lookup_baseline_teacher_version

        baseline, baseline_reason = _lookup_baseline_teacher_version(
            st.root / row.storage_key, settings
        )
        provenance["baselineTeacherVersion"] = baseline
        if baseline is None:
            provenance["baselineTeacherVersionReason"] = baseline_reason

    series_dir = "/".join(row.storage_key.split("/")[:2])
    corr_dir = st.root / series_dir / "nninteractive-correction"
    sidecar_path = corr_dir / "correction_source.json"
    corr_mask_path = corr_dir / "correction_mask.nii.gz"
    if not sidecar_path.exists() or not corr_mask_path.exists():
        raise HTTPException(
            status_code=422,
            detail="无 nnInteractive 修正产物（nninteractive-correction/ 缺失）："
            "先跑点/框提示修正再提交",
        )
    try:
        sidecar = _json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=422, detail=f"修正 sidecar 不可读：{e}") from None
    if sidecar.get("source") != "nninteractive_guided":
        raise HTTPException(
            status_code=422,
            detail=f"修正 sidecar source={sidecar.get('source')!r} 不是 nninteractive_guided",
        )
    if sidecar.get("target_label") is not None and int(sidecar["target_label"]) != target_label:
        raise HTTPException(
            status_code=422,
            detail=(
                f"sidecar target_label={sidecar['target_label']} 与提交 "
                f"target_label={target_label} 不一致"
            ),
        )

    img_key = row.storage_key
    mask_key = (row.storage_key or "").replace("image.nii.gz", "mask.nii.gz")
    if not mask_key or not st.exists(mask_key):
        raise HTTPException(status_code=422, detail="no mask to correct")
    from .nifti_geom import AffineMismatchError, assert_affine_match

    try:
        with (
            _nifti_from_bytes(st.load(img_key)) as img,
            _nifti_from_bytes(st.load(mask_key)) as mask,
            _nifti_from_bytes(corr_mask_path.read_bytes()) as corr,
        ):
            assert_affine_match(img, mask, label=f"review_submit_nni {series_id}")
            assert_affine_match(img, corr, label=f"review_submit_nni correction {series_id}")
            affine = mask.affine
            arr = np.asarray(mask.dataobj).astype(np.int32)
            corr_arr = np.asarray(corr.dataobj)
            n_corr = int((corr_arr > 0).sum())
            if n_corr == 0:
                raise ValueError("修正 mask 全空（无修正体素），拒绝提交空版本")
            arr[corr_arr > 0] = target_label
    except AffineMismatchError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None

    provenance["targetLabel"] = target_label
    provenance["correctionSidecar"] = (
        f"{series_dir}/nninteractive-correction/correction_source.json"
    )

    from sqlalchemy import select

    from . import db
    from .models import MaskVersion

    engine = db._engine(settings.database.url)
    try:
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            try:
                last = (
                    s.execute(
                        select(MaskVersion.version)
                        .where(MaskVersion.series_id == series_id[:60])
                        .order_by(MaskVersion.version.desc())
                        .limit(1)
                    )
                    .scalars()
                    .first()
                )
                version = (last or 0) + 1
                import tempfile

                with tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False) as tf:
                    tmp_name = tf.name
                try:
                    nib.save(nib.Nifti1Image(arr, affine), tmp_name)
                    payload = _Path(tmp_name).read_bytes()
                finally:
                    _Path(tmp_name).unlink(missing_ok=True)
                key = mask_key.replace("image.nii.gz", f"mask_v{version}.nii.gz")
                st.save(key, payload)
                s.add(
                    MaskVersion(
                        mask_version_id=f"mv-{uuid.uuid4().hex[:8]}",
                        series_id=series_id[:60],
                        version=version,
                        source="nninteractive_guided",
                        storage_key=key,
                        created_by="review-ui-submit-nni",
                        provenance=provenance,
                    )
                )
                s.commit()
            finally:
                s.close()
    finally:
        engine.dispose()
    return {
        "version": version,
        "source": "nninteractive_guided",
        "key": key,
        "target_label": target_label,
        "n_corrected_voxels": n_corr,
        "provenance": provenance,
    }
