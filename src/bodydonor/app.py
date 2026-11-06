"""FastAPI 骨架（M0-fix）：健康检查、status、license、配置化限制、DB 实体计数查看。

M1 起 ingest 端点在 router 层加 max_upload_mb 强制流式计数（前端预检/反代/后端三处之一），
且在导入路径写 UsageLedger 并拦截超限（LicenseState.trial_yearly/monthly_used）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
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

settings: Settings = load_settings()
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
    """License 试用计数查看（只读，不强制执行）。

    M0-fix：LicenseState/UsageLedger 计数基础的数据模型落地；强制执行 M1 起
    （导入路径计数拦截），完整 Ed25519 激活流程 M4/M5。多指纹部署（多库/多容器）
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
    return {"states": out, "enforcement": "deferred_to_m1"}


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
