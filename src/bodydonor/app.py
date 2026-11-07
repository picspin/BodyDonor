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
from .storage import InProcessQueue, Job, LocalFsStorage

settings: Settings = load_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db(settings.database.url)
    yield


app = FastAPI(title="BodyDonor", version=__version__, lifespan=lifespan)

registry = LicenseRegistry(settings)

# 队列与存储（M0 默认组合）
queue = InProcessQueue()
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
            "default_selected": [
                c.series_id for c in outcome.candidates if not c.excluded
            ],
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
    return JSONResponse(
        {"written_back": True, "entry": chosen.to_dict(), "version": svc.version}
    )


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
    """流水线 8 阶段状态（TUI 轮询；worker/编排层更新，单进程内存态）。"""
    return {
        "stages": dict(_pipeline_stages),
        "queue_size": queue.qsize(),
        "active_jobs": sorted(queue.pending_ids()),
    }


@app.post("/monitor/pipeline/{stage}")
def monitor_pipeline_set(stage: str, status: str = Query("running")) -> dict[str, Any]:
    """更新流水线阶段状态（worker/编排层调用；TUI 只读不写）。"""
    if stage not in _pipeline_stages:
        raise HTTPException(status_code=404, detail=f"unknown stage: {stage}")
    _pipeline_stages[stage] = status
    return {"stage": stage, "status": status}


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
    """序列元数据 + mask 存在性（审阅页首屏）。"""
    row = _series_row(series_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown series: {series_id}")
    st = _get_storage()
    has_mask = bool(row.storage_key) and st.exists(
        row.storage_key.replace("image.nii.gz", "mask.nii.gz")
    )
    return {
        "series_id": row.series_id,
        "case_id": row.case_id,
        "modality": row.modality,
        "num_slices": row.num_slices,
        "slice_thickness_mm": row.slice_thickness_mm,
        "has_mask": has_mask,
    }


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
    """最新 MaskVersion 的 mask NIfTI（多类）。"""
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
        # 无 MaskVersion 记录时 fallback：裸 mask.nii.gz（teacher 初版直接落盘）
        row = _series_row(series_id)
        mask_key = (row.storage_key or "").replace("image.nii.gz", "mask.nii.gz") if row else ""
        if mask_key and st.exists(mask_key):
            return Response(content=st.load(mask_key), media_type="application/gzip")
        raise HTTPException(status_code=404, detail="no mask version for series")
    try:
        data = st.load(mv.storage_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="mask file missing") from None
    return Response(content=data, media_type="application/gzip")


@app.get("/review/{series_id}/seg-meta")
def review_seg_meta(series_id: str) -> list[dict[str, Any]]:
    """mask 类别列表（class 名 + 体素数），审阅页逐类开关用。"""
    row = _series_row(series_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown series: {series_id}")
    st = _get_storage()
    mask_key = (row.storage_key or "").replace("image.nii.gz", "mask.nii.gz")
    if not mask_key or not st.exists(mask_key):
        return []
    import numpy as np

    with _nifti_from_bytes(st.load(mask_key)) as img:
        arr = np.asarray(img.dataobj)
        classes = sorted(set(int(v) for v in np.unique(arr)) - {0})
    return [
        {
            "class": f"label-{v}",
            "label_value": v,
            "n_voxels": int((arr == v).sum()),
        }
        for v in classes
    ]


@app.post("/review/{series_id}/save")
def review_save(series_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """人工修正写回（M2 粒度：整类删除 + 画笔粗修）-> 新 MaskVersion。

    body: {deleted_classes: [class,...], drawing_nifti: [bytes] | null}
    - 整类删除：deleted_classes 里的 label 体素清零（走 assert_affine_match 守卫）
    - 画笔粗修：drawing_nifti 非零体素从 mask 中删除（粗修擦除）
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
                    assert_affine_match(
                        img, draw_img, label=f"review_save drawing {series_id}"
                    )
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
                s.add(
                    MaskVersion(
                        mask_version_id=f"mv-{uuid.uuid4().hex[:8]}",
                        series_id=series_id[:60],
                        version=version,
                        source="manual",
                        storage_key=key,
                        created_by="review-ui",
                    )
                )
                s.commit()
            finally:
                s.close()
    finally:
        engine.dispose()
    return {"version": version, "source": "manual", "key": key}
