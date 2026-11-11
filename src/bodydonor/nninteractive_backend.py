"""nnInteractive 交互修正后端（M4 阶段 3）：组装 runner 命令 + 调子进程 + 修正产物落盘。

架构（照抄 segmentation.py 的 TS worker 模式，M2-align 3.2/3.3 契约）：

- **独立 venv**：nnInteractive 及其 torch 依赖装在 `.venv-nninteractive`
  （uv 管理），**绝不进主 .venv**，绝不与 .venv-totalseg / .venv-nnunet 共享。
  本模块（主 .venv import）**不 import torch / nnInteractive**——推理全在
  nni_runner.py（由 .venv-nninteractive/bin/python 执行）内。
- **JSON 契约**：任务 JSON 经 stdin 给 runner，结果 JSON（纯 JSON，日志走
  stderr）经 stdout 收（与 ts_runner.py 同一契约）。
- **GPU 文件锁（短会话模式）**：每次修正 acquire(reason="interactive",
  wait=0) → 加载+推理+保存 → release（保存后立即释放）。拿不到锁立即明确报错
  （有批量推理/训练任务在跑），不排队、不重试。release_on_idle(600) 作兜底：
  进程意外存活时闲置 10 分钟自动放锁（闲置时长从锁文件 acquired_at 起算）。
- **显存峰值**：主值 = runner 内 torch.cuda.max_memory_allocated()（分配器
  口径）；nvidia-smi 全局轮询是交叉核对值（两口径都记录，任一不可用填 None
  不猜数字）。CPU 模式 peak_vram_mb=null（不猜数字）。
- **许可约束（LICENSES-THIRD-PARTY.md §2.2 / §8.3）**：修正产物 sidecar JSON
  必须带 source="nninteractive_guided" + license="CC BY-NC-SA 4.0"
  （权重传染标记）；蒸馏训练集门禁（distill_data.REJECTED_SOURCES / ontology.
  mask_allowed_in_distill_training）拒绝该来源。M4 时 MaskVersion.source 列
  是 String(16)，20 字符塞不进，故修正产物不建行、来源事实以 sidecar JSON
  为准；M5-e 列宽已改 String(32)（迁移 e7f3a1c9d204），提交走
  app.review_submit_nni 建行（sidecar 仍是来源事实源之一）。
  历史字符串 manual-correction-nninteractive（29 字符，阶段 2a sidecar 用过）
  与 nninteractive 是 legacy 别名：永不再由新代码写出，但门禁拒绝清单保留
  （删拒绝是安全倒退）。
- **M5-e**：MaskVersion.source 列宽已改为 String(32)（迁移 e7f3a1c9d204），
  修正产物提交走 app.review_submit_nni 建 MaskVersion 行
  （source=nninteractive_guided + provenance 列）；本后端仍只写 sidecar，
  不直接建行（提交时合并修正 mask 进基线 mask 是端点职责）。
- **provenance（可追溯，用户 2026-11-09 三态决定）**：每个修正产物的 sidecar
  JSON 必须含 provenance={sessionId, baselineTeacherVersion, prompts[],
  acceptedClasses[]}——sessionId 每次修正运行新生成 uuid；baselineTeacherVersion
  是该 series 基线 teacher mask 的 MaskVersion 版本号（查不到写 null + 注明
  原因）；prompts 全量提示序列（可重放）；acceptedClasses=[target_label]。
- **affine 守卫**：修正 mask 与原图 affine 一致性走 nifti_geom.assert_affine_match
  （runner 内一次 + 落盘读回一次，与 review_save 同语义）。

server 模式（nninteractive-server / RemoteInferenceSession）本阶段不接：
本地后端优先、单机单卡 + 锁互斥已够用，server 常驻会话与短会话锁模式冲突
（遗留项见 M4-STAGE3-REPORT）。
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings

# nnInteractive 独立 venv（Q7：隔离环境；与 .venv-totalseg / .venv-nnunet 互斥）
DEFAULT_NNI_VENV = Path.home() / "work" / "BodyDonor" / ".venv-nninteractive"
NNI_RUNNER = "nni_runner.py"  # nni venv 内执行的 JSON 任务 runner
NNI_RUNNER_TIMEOUT_SECONDS = 1800  # 单次交互修正上限 30 分钟（短会话模式）
# 模型权重落盘目录（与项目数据目录一致；默认 ~/.nninteractive 不用）
NNINTERACTIVE_MODEL_DIR = Path.home() / "data" / "BodyDonor" / "nninteractive-models"
# 修正产物来源标签（sidecar JSON 的 source 字段；不进 MaskVersion.source 列
# ——String(16) 放不下 20 字符的 nninteractive_guided，蒸馏门禁以 sidecar 为
# 事实源）。规范名 nninteractive_guided（用户 2026-11-09 三态：teacher /
# nninteractive_guided / human）；manual-correction-nninteractive 与
# nninteractive 是 legacy 别名（阶段 2a sidecar 用过旧名），永不再写出，
# 但蒸馏门禁保留拒绝（删拒绝是安全倒退）。
CORRECTION_SOURCE = "nninteractive_guided"
# legacy 别名（仅门禁拒绝清单引用，新代码绝不写出）
CORRECTION_SOURCE_LEGACY_ALIASES = ("manual-correction-nninteractive", "nninteractive")
CORRECTION_LICENSE = "CC BY-NC-SA 4.0"


class NnInteractiveError(Exception):
    """nnInteractive 修正失败（venv 缺失、GPU 锁被占、推理失败、affine 不一致等）。"""


class GpuBusyError(NnInteractiveError):
    """GPU 被批量推理/训练任务占用（交互模式 fast-fail，不排队）。"""


@dataclass
class CorrectionResult:
    """一次 nnInteractive 交互修正的产物。"""

    job_id: str
    image_path: str
    mask_path: str  # 修正 mask 落盘路径（含 target_label 标签值）
    sidecar_path: str  # sidecar JSON（source/license/model_id/prompts/耗时/显存）
    load_seconds: float = 0.0
    inference_seconds: float = 0.0
    wall_seconds: float = 0.0
    peak_vram_mb: float | None = None  # runner torch 分配器实测（主值，MiB）
    peak_vram_nvidia_smi_mb: float | None = None  # nvidia-smi 轮询（交叉核对值）
    license: str = ""
    n_target_voxels: int = 0
    prompts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "image_path": self.image_path,
            "mask_path": self.mask_path,
            "sidecar_path": self.sidecar_path,
            "source": CORRECTION_SOURCE,
            "license": self.license or CORRECTION_LICENSE,
            "load_seconds": round(self.load_seconds, 2),
            "inference_seconds": round(self.inference_seconds, 2),
            "wall_seconds": round(self.wall_seconds, 2),
            "peak_vram_mb": self.peak_vram_mb,
            "peak_vram_nvidia_smi_mb": self.peak_vram_nvidia_smi_mb,
            "n_target_voxels": self.n_target_voxels,
            "prompts": list(self.prompts),
        }


def nni_venv_cli() -> str:
    """nnInteractive venv python 路径：`.venv-nninteractive/bin/python`（Q7 隔离）。"""
    exe = DEFAULT_NNI_VENV / "bin" / "python"
    if not exe.exists():
        raise NnInteractiveError(
            f"nnInteractive 独立 venv 不存在：{exe}（先建 venv 并安装 "
            "torch cu124 + nnInteractive，见 M4-STAGE3-REPORT 交付清单）"
        )
    return str(exe)


def runner_module_path() -> Path:
    """nni_runner.py 模块路径（主仓 src 内，由 nni venv python 执行）。"""
    return Path(__file__).resolve().parent / NNI_RUNNER


def build_runner_cmd() -> list[str]:
    """JSON runner 命令：`.venv-nninteractive/bin/python nni_runner.py`。"""
    return [nni_venv_cli(), str(runner_module_path())]


def gpu_memory_used_mi_b() -> float | None:
    """nvidia-smi 实测全局显存（MiB）。不可用时返回 None（不猜数字）。

    WSL2 口径（segmentation.gpu_peak_memory_mi_b 同源）：--query-gpu=memory.used
    全局值；交叉核对值 = 推理期间全局峰值 − 推理前基线。
    """
    import shutil

    exe = shutil.which("nvidia-smi") or "/usr/lib/wsl/lib/nvidia-smi"
    if not Path(exe).exists():
        return None
    try:
        out = subprocess.run(  # noqa: S603 — 固定路径固定参数
            [exe, "--query-gpu=memory.used", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode != 0:
            return None
        val = out.stdout.strip().splitlines()[0].strip()
        return float(val.split()[0])
    except (subprocess.TimeoutExpired, OSError, ValueError, IndexError):
        return None


def correct_image(
    image_path: Path,
    output_dir: Path,
    prompts: list[dict[str, Any]],
    *,
    device: str = "auto",
    target_label: int = 1,
    settings: Settings | None = None,
    job_id: str = "",
    model_dir: Path | None = None,
) -> CorrectionResult:
    """对单张 NIfTI 跑一次 nnInteractive 交互修正（短会话：锁 → 推理 → 落盘 → 放锁）。

    - device: auto | cuda | cpu（auto = CUDA 可用则 cuda，否则 cpu）
    - prompts: [{"type": "point", "x","y","z", "include": bool}] 或
      [{"type": "bbox", "bbox": [[x0,x1],[y0,y1],[z0,z1]]}]
    - GPU 锁：interactive fast-fail（wait=0）——被占时 GpuBusyError，不排队
    - 落盘：output_dir/correction_mask.nii.gz + correction_source.json（sidecar，
      含 source/license/model_id/prompts/耗时/显存/affine 校验）
    - 修正产物**绝不建蒸馏训练集准入记录**（sidecar source 标签 + 蒸馏门禁强制）
    """
    from .gpu_lock import DEFAULT_IDLE_SECONDS, acquire

    settings = settings or _default_settings()
    image_path = Path(image_path)
    output_dir = Path(output_dir)
    if not image_path.exists():
        raise NnInteractiveError(f"原图不存在：{image_path}")
    if not prompts:
        raise NnInteractiveError("prompts 为空：至少一个 point 或 bbox 提示")

    # device 解析（auto：CUDA 可用则 cuda；探测不加载模型到 GPU，安全）
    if device == "auto":
        device = _detect_device()

    lock_path = settings.resolved_data_dir / ".gpu.lock"
    job_id = job_id or f"nni-{uuid.uuid4().hex[:8]}"
    # provenance sessionId：每次修正运行新生成 uuid（与 job_id 不同——job_id
    # 可由调用方指定复用，sessionId 每次运行唯一、可重放审计）
    session_id = str(uuid.uuid4())
    model_dir = model_dir or NNINTERACTIVE_MODEL_DIR
    # provenance baselineTeacherVersion：该 series 基线 teacher mask 的
    # MaskVersion 版本号（查不到写 None 并注明原因，不猜数字）
    baseline_teacher_version, baseline_reason = _lookup_baseline_teacher_version(
        image_path, settings
    )

    # GPU 锁：只对 CUDA 推理生效（CPU 推理不占显存，不与 GPU 批量任务互斥——
    # 训练占卡期间 CPU 冒烟/小 patch 验证正是靠这条路径跑的）。交互模式
    # acquire(wait=0) 立即失败返回 None（不排队不重试）。
    lock = None
    if device == "cuda":
        lock = acquire(
            lock_path,
            reason="interactive",
            task="nninteractive",
            job_id=job_id,
            wait=0,
        )
        if lock is None:
            holder = _lock_holder_desc(lock_path)
            raise GpuBusyError(
                f"有批量推理/训练任务在跑（{holder}），交互修正 fast-fail"
                "（不排队、不重试；等批量任务结束再试）"
            )
        # 双保险（持锁后第二道）：nvidia-smi 实占检查——锁文件缺失/损坏但有
        # 真实训练占卡（如外部训练进程未走锁）时兜底拒绝（训练占卡时拒绝起跑）
        used = gpu_memory_used_mi_b()
        total = _gpu_total_mi_b()
        if used is not None and total is not None and used > total * 0.5:
            lock.release()
            raise GpuBusyError(
                f"GPU 已被占用（nvidia-smi 实测 {used:.0f}/{total:.0f} MiB，"
                "超过 50%）：疑似批量推理/训练任务在跑（可能未走 GPU 锁），"
                "交互修正拒绝起跑（不排队不重试）"
            )

    try:
        return _run_locked(
            image_path,
            output_dir,
            prompts,
            device=device,
            target_label=target_label,
            job_id=job_id,
            model_dir=model_dir,
            lock=lock,
            idle_seconds=DEFAULT_IDLE_SECONDS if lock is not None else 0,
            session_id=session_id,
            baseline_teacher_version=baseline_teacher_version,
            baseline_teacher_reason=baseline_reason,
        )
    finally:
        # 短会话模式：无论成功失败，退出即放锁（release 幂等；CPU 模式无锁）
        if lock is not None:
            lock.release()


def _run_locked(
    image_path: Path,
    output_dir: Path,
    prompts: list[dict[str, Any]],
    *,
    device: str,
    target_label: int,
    job_id: str,
    model_dir: Path,
    lock: Any,
    idle_seconds: int,
    session_id: str,
    baseline_teacher_version: int | None,
    baseline_teacher_reason: str,
) -> CorrectionResult:
    """持锁执行：组装 runner 命令 → 子进程推理 → nvidia-smi 交叉核对 → 落盘 sidecar。

    lock 传进来只为在注释/兜底语义上显式（release 由调用方 finally 保证）；
    release_on_idle(idle_seconds) 是兜底语义：本进程意外长存活（例如被嵌进
    常驻服务）时闲置超时自动放锁——闲置时长从锁文件 acquired_at 起算，不是
    "最后一次操作"，长会话须在每次交互后刷新 acquired_at 或改用短会话模式
    （本后端选择短会话：每次修正 acquire→跑→存→release，见 correct_image）。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    task_json = {
        "image_nii": str(image_path),
        "prompts": prompts,
        "target_label": target_label,
        "device": device,
        "output_dir": str(output_dir),
        "job_id": job_id,
        "model_dir": str(model_dir),
    }
    cmd = build_runner_cmd()

    # 交叉核对：推理期间 nvidia-smi 轮询（后台线程，不挡主流程）；
    # 交叉核对值 = 轮询峰值 − 推理前基线（与 segmentation.py 同口径）
    smi_baseline = gpu_memory_used_mi_b() if device == "cuda" else None
    smi_samples: list[float] = []
    poll_stop = threading.Event()

    def _poll_smi() -> None:
        while not poll_stop.wait(1.0):
            v = gpu_memory_used_mi_b()
            if v is not None:
                smi_samples.append(v)

    poller = threading.Thread(target=_poll_smi, daemon=True) if device == "cuda" else None
    if poller is not None:
        poller.start()

    t0 = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 — 命令由 repo 自身 venv 路径组装
            cmd,
            input=json.dumps(task_json),
            capture_output=True,
            text=True,
            timeout=NNI_RUNNER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        raise NnInteractiveError(
            f"nnInteractive runner 超时（>{NNI_RUNNER_TIMEOUT_SECONDS}s）："
            f"stderr[-1000:]={(e.stderr or '')[-1000:]!r}"
        ) from None
    finally:
        poll_stop.set()
        if poller is not None:
            poller.join(timeout=5)
    wall = time.monotonic() - t0

    # stdout 是纯 JSON 契约；解析失败如实报（不猜结果）
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise NnInteractiveError(
            f"nni_runner 结果 JSON 解析失败（exit {proc.returncode}）：{e}；"
            f"stdout[:500]={proc.stdout[:500]!r}；stderr[-1000:]={proc.stderr[-1000:]!r}"
        ) from e
    if result.get("status") != "ok" or result.get("exit_code") != 0:
        raise NnInteractiveError(
            f"nni_runner 失败（exit {result.get('exit_code')}）："
            f"{result.get('error') or proc.stderr[-1000:]}"
        )

    mask_path = Path(str(result["mask_path"]))
    if not mask_path.exists():
        raise NnInteractiveError(f"runner 报成功但 mask 不存在：{mask_path}")

    # 交叉核对值 = nvidia-smi 轮询峰值 − 推理前基线（全局口径；主值=runner torch
    # 分配器口径；两者都记录，任一不可用填 None 不猜数字）
    peak_smi: float | None = None
    if device == "cuda" and smi_samples:
        peak_smi = max(smi_samples) - (smi_baseline or 0.0)
        if peak_smi <= 0:
            peak_smi = None

    # sidecar JSON：来源事实源（distill_data provenance 同一约定：旁挂 json）
    # provenance（用户 2026-11-09 三态决定）：sessionId 每次运行新生成 uuid；
    # baselineTeacherVersion 查不到时写 null 并注明原因（不猜数字）；
    # prompts 全量提示序列（可重放）；acceptedClasses=[target_label]
    provenance: dict[str, Any] = {
        "sessionId": session_id,
        "baselineTeacherVersion": baseline_teacher_version,
        "prompts": list(prompts),
        "acceptedClasses": [target_label],
    }
    if baseline_teacher_version is None:
        provenance["baselineTeacherVersionReason"] = (
            baseline_teacher_reason
            or "无 MaskVersion 记录且无 fallback mask.nii.gz（查不到基线版本，不猜数字）"
        )
    sidecar_path = output_dir / "correction_source.json"
    sidecar = {
        "source": CORRECTION_SOURCE,
        "license": CORRECTION_LICENSE,
        "license_from_session": result.get("license", ""),
        # 真实 model_id 优先（runner _resolve_model_folder 实测：manifest 或
        # 目录名）；runner 未返回时退回目录名推导（_model_id，不猜）
        "model_id": result.get("model_id") or _model_id(model_dir),
        "model_dir": str(model_dir),
        "job_id": job_id,
        "provenance": provenance,
        "image_path": str(image_path),
        "mask_path": str(mask_path),
        "target_label": target_label,
        "prompts": prompts,
        "device": device,
        "load_seconds": result.get("load_seconds"),
        "inference_seconds": result.get("inference_seconds"),
        "wall_seconds": result.get("wall_seconds"),
        "peak_vram_mb": result.get("peak_vram_mb"),
        "peak_vram_nvidia_smi_mb": peak_smi,
        "n_target_voxels": result.get("n_target_voxels"),
        "affine_check": "assert_affine_match（runner 内 + 落盘读回，两道守卫）",
        "distill_training": "REJECTED（nnInteractive 修正产物不进商用蒸馏训练集，"
        "LICENSES-THIRD-PARTY.md §2.2/§8.3；distill_data.check_distill_source 拒绝本 source）",
        "gpu_lock": {
            "mode": "interactive_short_session",
            "idle_release_fallback_seconds": idle_seconds,
            "note": "短会话：acquire→跑→存→release；release_on_idle(600) 仅兜底",
        },
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=1), encoding="utf-8")

    return CorrectionResult(
        job_id=job_id,
        image_path=str(image_path),
        mask_path=str(mask_path),
        sidecar_path=str(sidecar_path),
        load_seconds=float(result.get("load_seconds") or 0.0),
        inference_seconds=float(result.get("inference_seconds") or 0.0),
        wall_seconds=float(result.get("wall_seconds") or wall),
        peak_vram_mb=result.get("peak_vram_mb"),
        peak_vram_nvidia_smi_mb=peak_smi,
        license=str(result.get("license") or ""),
        n_target_voxels=int(result.get("n_target_voxels") or 0),
        prompts=prompts,
    )


def _lookup_baseline_teacher_version(
    image_path: Path, settings: Settings | None = None
) -> tuple[int | None, str]:
    """查该 series 基线 teacher mask 的 MaskVersion 版本号（provenance 用）。

    series_id 从原图路径推出（storage_key 形如 <case>/<series>/image.nii.gz，
    series_id 截断到 60 字符与 MaskVersion.series_id 列一致——app.py 同约定）。
    返回 (版本号 | None, 原因说明)。查不到（无 DB / 无行 / DB 异常）返回
    (None, 原因)，**不猜数字**；DB 只读（SELECT），不写不锁。
    """
    from .db import _engine
    from .models import MaskVersion

    try:
        parts = Path(image_path).resolve().parts
        # <root>/<case_id>/<series_id>/image.nii.gz → series_id 倒数第二段
        series_id = parts[-2]
    except (IndexError, ValueError):
        return None, f"series_id 无法从路径推出：{image_path}"
    if not series_id:
        return None, f"series_id 无法从路径推出：{image_path}"
    try:
        settings = settings or _default_settings()
        engine = _engine(settings.database.url)
    except Exception as e:  # noqa: BLE001 — DB 不可达不挡修正，版本号填 null
        return None, f"DB 不可达（{type(e).__name__}: {e}），基线版本查不到"
    try:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        with engine.connect() as conn:
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
    except Exception as e:  # noqa: BLE001 — 查询失败不挡修正
        return None, f"MaskVersion 查询失败（{type(e).__name__}: {e}）"
    finally:
        engine.dispose()
    if mv is None:
        return None, f"series {series_id[:60]} 无 MaskVersion 记录（基线版本查不到）"
    return int(mv.version), ""


def _lock_holder_desc(lock_path: Path) -> str:
    """锁持有者描述（报错用：pid/task/job）。"""
    from .gpu_lock import lock_status

    st = lock_status(lock_path)
    info = st.get("info") or {}
    if info:
        return f"pid={info.get('pid')} task={info.get('task')} job={info.get('job_id')}"
    return "未知持有者"


def _detect_device() -> str:
    """auto：CUDA 可用则 cuda（只查可用性，不加载模型），否则 cpu。"""
    exe = nni_venv_cli()
    try:
        out = subprocess.run(  # noqa: S603 — 固定命令
            [exe, "-c", "import torch; print(int(torch.cuda.is_available()))"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return "cuda" if out.stdout.strip().endswith("1") else "cpu"
    except (subprocess.TimeoutExpired, OSError):
        return "cpu"


def _gpu_total_mi_b() -> float | None:
    """nvidia-smi 全局显存总量（MiB）。不可用返回 None。"""
    import shutil

    exe = shutil.which("nvidia-smi") or "/usr/lib/wsl/lib/nvidia-smi"
    if not Path(exe).exists():
        return None
    try:
        out = subprocess.run(  # noqa: S603 — 固定路径固定参数
            [exe, "--query-gpu=memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode != 0:
            return None
        return float(out.stdout.strip().splitlines()[0].strip().split()[0])
    except (subprocess.TimeoutExpired, OSError, ValueError, IndexError):
        return None


def _model_id(model_dir: Path) -> str:
    """模型 id = 落盘目录名（与 model_management 落盘路径一致；主 .venv 不 import
    nnInteractive，真实 model_id 由 runner 侧 session.license/目录实测并写 sidecar）。"""
    return model_dir.name or str(model_dir)


def _default_settings() -> Settings:
    from .config import load_settings

    return load_settings()


__all__ = [
    "CORRECTION_LICENSE",
    "CORRECTION_SOURCE",
    "CORRECTION_SOURCE_LEGACY_ALIASES",
    "CorrectionResult",
    "DEFAULT_NNI_VENV",
    "GpuBusyError",
    "NNI_RUNNER_TIMEOUT_SECONDS",
    "NNINTERACTIVE_MODEL_DIR",
    "NnInteractiveError",
    "build_runner_cmd",
    "correct_image",
    "gpu_memory_used_mi_b",
    "nni_venv_cli",
    "runner_module_path",
]
