"""TotalSegmentator worker（M2 + M2-align 3.2/3.3/3.6，proposal §11 M2 / Q7 隔离）。

架构：

- **独立 venv**：TotalSegmentator 及其 torch/nnU-Net 依赖装在 `.venv-totalseg`
  （uv 管理 + lock），**绝不进主 .venv**，绝不与 nnInteractive/nnU-Net 共享环境。
  主进程以 **JSON 任务**（stdin）调 TS venv 内的 runner（`ts_runner.py`）、
  以 **JSON 结果**（stdout，纯 JSON，日志走 stderr）收它（M2-align 3.2 契约）。
- **任务白名单**：worker 分发层用 license_registry.check_task 判定（blocked 一票
  否决）；runner 侧再查一次（纵深）；任何代码路径不许绕过白名单（CI 负测试锁定）。
- **GPU 文件锁**：批量推理全程持锁（`gpu_lock.py`，数据目录 `.gpu.lock`）；
  交互会话 fast-fail 不排队（M2-align 3.3）。
- **guard_min_slices 防线**：<30 层的序列绝不能进 TS（定位像残片 3D 卷积报错），
  走 pipeline.guard_min_slices（用户显式 --include 勾选才放行）。
- **mask 落盘**：多类别 NIfTI 落 case_id/series_id/ 目录（与现有存储约定一致），
  读写过 nifti_geom.assert_affine_match（M1-fix (b) 守卫）。
- **显存峰值（M2-align 3.6）**：主值 = runner 内 torch.cuda.max_memory_allocated()
  （分配器口径，结果 JSON 的 peak_vram_mb）；nvidia-smi 轮询是交叉核对值
  （全局口径）。两者都记录；任一不可用填 None（不猜数字）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .license_registry import MODALITY_ALIASES, LicenseRegistry

# TotalSegmentator 独立 venv（Q7：隔离环境）
DEFAULT_TS_VENV = Path.home() / "work" / "BodyDonor" / ".venv-totalseg"
TS_RUNNER = "ts_runner.py"  # TS venv 内执行的 JSON 任务 runner（ts_runner.py）
TS_RUNNER_TIMEOUT_SECONDS = 7200


class SegmentWorkerError(Exception):
    """TS worker 失败（白名单拒绝、层数防线、TS 退出非零、affine 不一致等）。"""


@dataclass
class SegmentationResult:
    """一次 total 任务的产物。"""

    series_id: str
    case_id: str
    task: str
    mask_key: str  # storage key（case_id/series_id/mask.nii.gz）
    n_classes: int
    class_names: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    peak_memory_mi_b: float | None = None  # runner torch 分配器实测（主值，MiB）
    peak_memory_nvidia_smi_mi_b: float | None = None  # nvidia-smi 轮询（交叉核对值）
    combined_mask_key: str = ""  # M3 收敛后与 mask_key 同值（兼容字段，不再单独落盘）
    labels_json_key: str = ""  # labels.json（索引->类名/中英名/颜色，M2-align 3.5）
    tmp_output: str = ""  # TS 原始输出目录（调试用）

    def to_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "case_id": self.case_id,
            "task": self.task,
            "mask_key": self.mask_key,
            "n_classes": self.n_classes,
            "class_names": list(self.class_names),
            "elapsed_s": round(self.elapsed_s, 2),
            "peak_vram_mb": self.peak_memory_mi_b,
            "peak_vram_nvidia_smi_mb": self.peak_memory_nvidia_smi_mi_b,
            "combined_mask_key": self.combined_mask_key,
            "labels_json_key": self.labels_json_key,
        }


def check_worker_task(task: str, settings: Settings, modality: str | None = None) -> None:
    """worker 分发层白名单判定（license_registry.check_task；不许绕过）。

    modality 给出时注册表按 (task, modality) 门控（S3 B1：MR series 不可派发
    CT-only 任务）。
    """
    ok, msg = LicenseRegistry(settings).check_task(task, modality=modality)
    if not ok:
        raise SegmentWorkerError(f"任务白名单拒绝：{msg}")


def ts_venv_cli(settings: Settings) -> str:
    """TS venv python 路径：`.venv-totalseg/bin/python`（Q7 隔离）。"""
    venv = DEFAULT_TS_VENV
    exe = venv / "bin" / "python"
    if not exe.exists():
        raise SegmentWorkerError(
            f"TotalSegmentator 独立 venv 不存在：{exe}（先建独立 venv 并安装 "
            "totalsegmentator==2.10.0，见 M2-REPORT 交付清单）"
        )
    return str(exe)


def runner_module_path() -> Path:
    """ts_runner.py 模块路径（主仓 src 内，由 TS venv python 执行）。"""
    return Path(__file__).resolve().parent / TS_RUNNER


def build_runner_cmd() -> list[str]:
    """JSON runner 命令：`.venv-totalseg/bin/python ts_runner.py`（stdin/stdout JSON 契约）。"""
    return [ts_venv_cli(Settings()), str(runner_module_path())]


def gpu_peak_memory_mi_b(pid: int | None = None) -> float | None:
    """nvidia-smi 实测显存（MiB）。不可用时返回 None（不猜数字）。

    M2-align 3.6：WSL2 的 --query-compute-apps 对部分进程返回 [N/A]（M2-REPORT
    §3 已实测），本函数改用 --query-gpu=memory.used 全局值（与基线脚本同口径）；
    交叉核对值 = 推理期间全局峰值 − 推理前基线。
    """
    exe = shutil.which("nvidia-smi") or "/usr/lib/wsl/lib/nvidia-smi"
    if not Path(exe).exists():
        return None
    try:
        # WSL2 的 nvidia-smi 不认 nounits（返回 "841 MiB"，本函数取数字部分）
        out = subprocess.run(
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


@dataclass
class RunnerResult:
    """JSON runner 结果（M2-align 3.2 契约的 Python 侧映射）。"""

    status: str  # ok | error
    exit_code: int
    mask_path: str | None  # TS 输出目录（每类一个 NIfTI）
    classes: list[str] = field(default_factory=list)
    wall_seconds: float = 0.0
    peak_vram_mb: float | None = None  # runner 内 torch 分配器实测（主值）
    error: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunnerResult:
        return cls(
            status=str(d.get("status", "error")),
            exit_code=int(d.get("exit_code", 1)),
            mask_path=d.get("mask_path"),
            classes=[str(c) for c in d.get("classes", [])],
            wall_seconds=float(d.get("wall_seconds", 0.0)),
            peak_vram_mb=d.get("peak_vram_mb"),
            error=str(d.get("error", "")),
        )


# c 档唯一「数据集即 CT」对照表：a) 属性 b) DICOM 标签都无来源时兜底。
# 每项依据：AMOS22 子集取 CT 部分（amos22 CT 子集切分）、ts_public 是
# TotalSegmentator 公开 CT 集、ct_lite 即 CT 数据集。后续若要求每 case
# 实测来源，需改 holdout.txt/数据清单格式（本轮不改）。
DATASET_MODALITY: dict[str, str] = {
    "amos": "CT",
    "ts_public": "CT",
    "ct_lite": "CT",
}


def _dicom_modality(path: Path) -> str | None:
    """DICOM 文件内 Modality 标签（ingest.py W4 口径：缺失/空/字面 None 一律当缺失）。"""
    import pydicom

    try:
        ds = pydicom.dcmread(str(path), force=False, stop_before_pixels=True)
    except Exception:
        return None
    raw = getattr(ds, "Modality", None)
    if raw is None:
        return None
    m = str(raw).strip()
    if not m or m.upper() == "NONE":
        return None
    return m


def resolve_modality(series: Any) -> str:
    """三档解析模态（fail-closed，绝不默认 CT）。

    a) 对象 .modality 属性（strip+upper，MODALITY_ALIASES 归一 PT→PET 等）；
    b) 入参是 DICOM 文件/目录时读 Modality 标签（W4 口径）；
    c) 数据集对照表 DATASET_MODALITY（对象 .source 属性或裸数据集名字符串）；
    d) 三档皆无 -> ValueError（不猜）。
    """
    # a) 属性
    raw = getattr(series, "modality", None)
    if isinstance(raw, str) and raw.strip():
        m = raw.strip().upper()
        return MODALITY_ALIASES.get(m, m)
    # b) DICOM 标签（入参本身是路径时）
    p = Path(series) if isinstance(series, (str, Path)) else None
    if p is not None and p.exists():
        m = _dicom_modality(p if p.is_file() else next(iter(sorted(p.glob("*"))), p))
        if m:
            mm = m.strip().upper()
            return MODALITY_ALIASES.get(mm, mm)
    # c) 数据集对照表
    source = getattr(series, "source", None)
    if isinstance(source, str) and source in DATASET_MODALITY:
        return DATASET_MODALITY[source]
    if isinstance(series, str) and series in DATASET_MODALITY:
        return DATASET_MODALITY[series]
    raise ValueError(
        f"modality 无来源（属性/DICOM 标签/数据集对照表皆无）：{series!r}，拒绝猜测"
    )


def run_total(
    image_path: Path,
    out_dir: Path,
    task: str = "total",
    device: str = "gpu",
    settings: Settings | None = None,
    job_id: str = "",
    *,
    modality: str,
) -> RunnerResult:
    """以 JSON 契约调 TS runner（M2-align 3.2）：任务 JSON stdin -> 结果 JSON stdout。

    - 白名单判定（check_worker_task）在调用前——任何代码路径不许绕过；
      modality 给出时注册表按 (task, modality) 门控（S3 B1）
    - device: gpu | cpu（M2 用真卡；CPU 只做冒烟）
    - stdout 是纯 JSON（runner 日志走 stderr，不混入）；结果 JSON 含
      status/exit_code/mask_path/classes/wall_seconds/peak_vram_mb/error
    - peak_vram_mb 是 runner 内 torch.cuda.max_memory_allocated() 实测（主值）；
      不可用（CPU 回退等）时为 None + error/reason 注明（不猜数字）
    """
    settings = settings or _default_settings()
    check_worker_task(task, settings, modality=modality)  # 白名单一票否决
    out_dir.mkdir(parents=True, exist_ok=True)
    task_json = {
        "input_nii": str(image_path),
        "task": task,
        "device": device,
        "output_dir": str(out_dir),
        "job_id": job_id,
    }
    cmd = build_runner_cmd()
    proc = subprocess.run(  # noqa: S603 — 命令由 repo 自身 venv 路径组装
        cmd,
        input=json.dumps(task_json),
        capture_output=True,
        text=True,
        timeout=TS_RUNNER_TIMEOUT_SECONDS,
    )
    # stdout 是纯 JSON 契约；解析失败如实报（不猜结果）
    try:
        result_json = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise SegmentWorkerError(
            f"TS runner 结果 JSON 解析失败（exit {proc.returncode}）：{e}；"
            f"stdout[:500]={proc.stdout[:500]!r}；stderr[-1000:]={proc.stderr[-1000:]!r}"
        ) from e
    result = RunnerResult.from_dict(result_json)
    if result.status != "ok" or result.exit_code != 0:
        raise SegmentWorkerError(
            f"TS runner 失败（exit {result.exit_code}）：{result.error or proc.stderr[-1000:]}"
        )
    return result


def _default_settings() -> Settings:
    from .config import load_settings

    return load_settings()


def merge_ts_output(
    out_dir: Path,
    image_path: Path,
    label: str = "",
) -> tuple[Any, list[str]]:
    """TS 输出（每类一个 NIfTI）合并为多类标签图，并过 affine 守卫。

    返回 (多类 nibabel 图, [类名])。合并前每类 mask 与原图 affine 一致性校验
    （nifti_geom.assert_affine_match；不一致 raise，不静默重采样）。

    M2-align 3.5：合并图 dtype 收敛为 **uint8**（0=背景，1..N=类别索引；
    117 类 < 255 足够）；类别数 > 255 即报错，不静默截断。
    """
    import nibabel as nib
    import numpy as np

    from .nifti_geom import assert_affine_match

    img = nib.load(str(image_path))
    seg_files = sorted(out_dir.glob("*.nii.gz"))
    if not seg_files:
        raise SegmentWorkerError(f"TS 输出目录无 mask：{out_dir}")
    if len(seg_files) > 255:
        # uint8 溢出检查：类别数 >255 报错，不静默截断（M2-align 3.5）
        raise SegmentWorkerError(
            f"类别数 {len(seg_files)} > 255，uint8 合并图放不下（不静默截断）"
        )

    merged = np.zeros(img.shape, dtype=np.uint8)
    class_names: list[str] = []
    for i, f in enumerate(seg_files, start=1):
        mask = nib.load(str(f))
        # M1-fix (b)：每类 mask 与原图 affine 一致性守卫
        assert_affine_match(img, mask, label=f"merge {f.name}（{label}）")
        arr = np.asarray(mask.dataobj) > 0
        merged[arr] = i
        class_names.append(f.name.removesuffix(".nii.gz"))
    out = nib.Nifti1Image(merged, img.affine)
    return out, class_names


# labels.json 默认配色（M2-align 3.5：可区分配色，写进 labels.json，
# 不硬编码进审阅页 JS；周期取色保证 117 类相邻可辨）
LABEL_PALETTE_RGB = [
    (230, 90, 90), (90, 160, 230), (110, 200, 110), (235, 190, 70),
    (185, 120, 225), (75, 200, 200), (235, 110, 180), (165, 165, 70),
    (105, 150, 75), (215, 140, 90), (145, 95, 200), (75, 120, 195),
    (200, 80, 80), (80, 190, 145), (190, 120, 60), (120, 90, 190),
]


def build_labels_json(
    class_names: list[str],
    ontology_dir: Path | None = None,
) -> dict[str, Any]:
    """labels.json 结构（M2-align 3.5）：索引 -> totalseg_class/中文名/英文名/颜色。

    - 颜色取 LABEL_PALETTE_RGB 周期配色（labels.json 承载，不硬编码进页面 JS）
    - 中文名/英文名查本体映射表（ontology map.yaml 的 nl_label/synonyms）；
      查不到的英文名 = totalseg_class 本身（下划线转空格），中文名填空
      （报告"待核对中文名清单"，不编造）
    """
    from .ontology import load_map

    # 反查表：totalseg_class -> (nl_label 中文名, synonyms 里的英文别名)
    class_to_cn: dict[str, str] = {}
    class_to_en: dict[str, str] = {}
    try:
        for e in load_map(ontology_dir=ontology_dir):
            class_to_cn.setdefault(e.totalseg_class, e.nl_label)
            en_syn = next(
                (s for s in e.synonyms if s.isascii()), None
            )
            if en_syn:
                class_to_en.setdefault(e.totalseg_class, en_syn)
    except Exception:  # noqa: BLE001 — 本体表缺失不挡 labels.json 生成
        pass

    labels: list[dict[str, Any]] = []
    for i, cls in enumerate(class_names, start=1):
        r, g, b = LABEL_PALETTE_RGB[(i - 1) % len(LABEL_PALETTE_RGB)]
        en = class_to_en.get(cls) or cls.replace("_", " ")
        cn = class_to_cn.get(cls, "")
        labels.append(
            {
                "index": i,
                "totalseg_class": cls,
                "name_en": en,
                "name_cn": cn,
                "color": [r, g, b],
            }
        )
    return {
        "n_classes": len(class_names),
        "labels": labels,
    }


def segment_series(
    series_id: str,
    settings: Settings,
    task: str = "total",
    device: str = "gpu",
) -> SegmentationResult:
    """对已 ingest 的 Series 跑 total 任务：原图 → TS（JSON runner）→ 多类 mask → MaskVersion。

    - 输入：Series.storage_key（commit 落盘的 RAS+ NIfTI）
    - 防线：guard_min_slices 语义（<30 层拒绝）在 series 表的 num_slices 上复查
    - GPU 锁：批量推理全程持锁（M2-align 3.3；排队等待，超时报清晰错误）
    - 落盘：case_id/series_id/mask.nii.gz + combined_mask.nii.gz + labels.json
      （M2-align 3.5）+ MaskVersion 记录
    - 显存峰值（M2-align 3.6）：主值 = runner torch 分配器；nvidia-smi 轮询交叉核对
    """
    import time as _time

    from sqlalchemy import select

    from . import db
    from .gpu_lock import DEFAULT_BATCH_WAIT_SECONDS, acquire
    from .models import MaskVersion, Series
    from .storage import LocalFsStorage

    url = settings.database.url
    engine = db._engine(url)
    try:
        with engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            row = s.execute(
                select(Series).where(Series.series_id == series_id[:60])
            ).scalar_one_or_none()
            s.close()
    finally:
        engine.dispose()
    if row is None:
        raise SegmentWorkerError(f"unknown series: {series_id}")
    # 白名单一票否决（分发层）：Series.modality 优先，NULL 时 DICOM 标签回退、
    # 再无则 raise（S3P2 ② fail-closed，闭合 S3P 未决 #4：NULL 模态不再退化为仅 task 门控）
    modality = resolve_modality(row)
    check_worker_task(task, settings, modality=modality)
    if not row.storage_key:
        raise SegmentWorkerError(f"series {series_id} 无像素（commit 未落盘）")

    # guard_min_slices 语义复查（<30 层不进 TS；防线纵深，选择器已排除一次）
    if (row.num_slices or 0) < _min_slices(settings):
        raise SegmentWorkerError(
            f"series {series_id} 层数 {row.num_slices} < {_min_slices(settings)}，"
            "拒绝交给 TotalSegmentator（定位像/残片会导致 3D 卷积报错）"
        )

    st = LocalFsStorage(settings.resolved_data_dir / "storage")
    image_path = st.root / row.storage_key
    if not image_path.exists():
        raise SegmentWorkerError(f"原图文件缺失：{row.storage_key}")

    # GPU 文件锁（M2-align 3.3）：批量推理排队等待，全程持锁直至推理结束
    lock_path = settings.resolved_data_dir / ".gpu.lock"
    job_id = f"seg-{series_id[:20]}-{uuid.uuid4().hex[:6]}"
    gpu_lock_obj = acquire(
        lock_path,
        reason="batch",
        task=task,
        job_id=job_id,
        wait=DEFAULT_BATCH_WAIT_SECONDS,
    )
    if gpu_lock_obj is None:  # 理论不可达（batch 模式排队或抛 TimeoutError）
        raise SegmentWorkerError("GPU 锁获取失败")

    with gpu_lock_obj, tempfile.TemporaryDirectory(
        dir=settings.resolved_data_dir / "tmp", prefix="ts-"
    ) as td:
        out_dir = Path(td) / "ts_out"
        t0 = _time.monotonic()
        # 交叉核对：推理期间 nvidia-smi 轮询（后台线程，不挡主流程）
        smi_samples: list[float] = []
        import threading as _threading

        # 推理前基线（桌面残留），交叉核对值 = 轮询峰值 − 基线（与基线脚本同口径）
        smi_baseline = gpu_peak_memory_mi_b()
        _poll_stop = _threading.Event()

        def _poll_smi() -> None:
            while not _poll_stop.wait(1.0):
                v = gpu_peak_memory_mi_b()
                if v is not None:
                    smi_samples.append(v)

        poller = _threading.Thread(target=_poll_smi, daemon=True)
        poller.start()
        try:
            runner = run_total(
                image_path,
                out_dir,
                task=task,
                device=device,
                settings=settings,
                job_id=job_id,
                modality=modality,
            )
        finally:
            _poll_stop.set()
            poller.join(timeout=5)
        elapsed = _time.monotonic() - t0
        # 主值 = runner 内 torch.cuda.max_memory_allocated()（分配器口径）
        peak = runner.peak_vram_mb
        # 交叉核对值 = nvidia-smi 轮询峰值 − 推理前基线（全局口径；两者都记录）
        if smi_samples:
            peak_smi = max(smi_samples) - (smi_baseline or 0.0)
            if peak_smi <= 0:
                peak_smi = None
        else:
            peak_smi = None  # 轮询无样本（WSL2 偶发），不猜数字

        merged, class_names = merge_ts_output(out_dir, image_path, label=series_id)

    # 多类 mask + 合并产物落盘（case_id/series_id/ 目录结构，与现有存储约定一致）
    import nibabel as nib

    from .nifti_geom import assert_affine_match

    # 读回校验：落盘 mask 与原图 affine 一致（M1-fix (b) 守卫）
    orig = nib.load(str(image_path))
    assert_affine_match(orig, merged, label=f"segment_series {series_id}")

    mask_key = row.storage_key.replace("image.nii.gz", "mask.nii.gz")
    labels_key = row.storage_key.replace("image.nii.gz", "labels.json")
    buf_path = image_path.parent / "mask.nii.gz"
    nib.save(merged, str(buf_path))
    try:
        payload = buf_path.read_bytes()
    finally:
        buf_path.unlink(missing_ok=True)
    st.save(mask_key, payload)

    # 合并产物（M3 收敛，遗留 (g)）：单一产物 mask.nii.gz（uint8 多标签，
    # 0=背景，1..N=类别索引）+ labels.json（TS 类别名单）。
    # combined_mask.nii.gz 不再落盘——与 mask.nii.gz 逐体素完全一致（diff=0
    # 实测），双落盘只浪费空间且构成两套产物；审阅页 fallback 直接读
    # mask.nii.gz（app.py 已收敛）。
    labels_doc = build_labels_json(class_names)
    import json as _json

    st.save(labels_key, _json.dumps(labels_doc, ensure_ascii=False).encode("utf-8"))

    # MaskVersion 记录（version 递增，source=teacher，task 打标）
    engine = db._engine(url)
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
                s.add(
                    MaskVersion(
                        mask_version_id=f"mv-{uuid.uuid4().hex[:8]}",
                        series_id=series_id[:60],
                        version=version,
                        source="teacher",
                        storage_key=mask_key,
                        created_by=f"totalsegmentator-{task}",
                    )
                )
                s.commit()
            finally:
                s.close()
    finally:
        engine.dispose()

    return SegmentationResult(
        series_id=row.series_id,
        case_id=row.case_id,
        task=task,
        mask_key=mask_key,
        n_classes=len(class_names),
        class_names=class_names,
        elapsed_s=elapsed,
        peak_memory_mi_b=peak,
        peak_memory_nvidia_smi_mi_b=peak_smi,
        combined_mask_key=mask_key,  # M3 收敛：与 mask.nii.gz 同一产物
        labels_json_key=labels_key,
    )


def _min_slices(settings: Settings) -> int:
    """min slices 阈值（与 series_selector.MIN_SLICES 同源；config 化后置）。"""
    from .series_selector import MIN_SLICES

    return MIN_SLICES


__all__ = [
    "DEFAULT_TS_VENV",
    "RunnerResult",
    "SegmentWorkerError",
    "SegmentationResult",
    "build_labels_json",
    "build_runner_cmd",
    "check_worker_task",
    "gpu_peak_memory_mi_b",
    "resolve_modality",
    "merge_ts_output",
    "run_total",
    "segment_series",
    "ts_venv_cli",
]
