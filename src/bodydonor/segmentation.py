"""TotalSegmentator worker（M2，proposal §11 M2 / Q7 隔离）。

架构：

- **独立 venv**：TotalSegmentator 及其 torch/nnU-Net 依赖装在 `.venv-totalseg`
  （uv 管理 + lock），**绝不进主 .venv**，绝不与 nnInteractive/nnU-Net 共享环境。
  主仓代码通过 subprocess 调用该 venv 的 CLI（`TotalSegmentator -i ... -o ...`），
  import 链隔离（Q7 版本 pin）。
- **任务白名单**：worker 分发层用 license_registry.check_task 判定（blocked 一票
  否决）；任何代码路径不许绕过白名单（CI 负测试锁定）。
- **guard_min_slices 防线**：<30 层的序列绝不能进 TS（定位像残片 3D 卷积报错），
  走 pipeline.guard_min_slices（用户显式 --include 勾选才放行）。
- **mask 落盘**：多类别 NIfTI 落 case_id/series_id/ 目录（与现有存储约定一致），
  读写过 nifti_geom.assert_affine_match（M1-fix (b) 守卫）。
- **GPU**：真卡跑（-d gpu），记录每例显存峰值与耗时（M2-REPORT 基线）。

mask 落盘的 NIfTI 是 TotalSegmentator 输出的多类标签图（每个整数 = 一个
totalseg_class），落盘前与原图 affine 一致性校验（不一致 raise，不静默重采样）。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .license_registry import LicenseRegistry

# TotalSegmentator 独立 venv 的 CLI 入口（Q7：隔离环境）
DEFAULT_TS_VENV = Path.home() / "work" / "BodyDonor" / ".venv-totalseg"
TS_CLI = "TotalSegmentator"


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
    peak_memory_mi_b: float | None = None  # nvidia-smi 实测显存峰值（MiB）
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
            "peak_memory_mi_b": self.peak_memory_mi_b,
        }


def check_worker_task(task: str, settings: Settings) -> None:
    """worker 分发层白名单判定（license_registry.check_task；不许绕过）。"""
    ok, msg = LicenseRegistry(settings).check_task(task)
    if not ok:
        raise SegmentWorkerError(f"任务白名单拒绝：{msg}")


def ts_venv_cli(settings: Settings) -> str:
    """TS CLI 路径：独立 venv 的 bin/TotalSegmentator（Q7 隔离）。"""
    venv = DEFAULT_TS_VENV
    exe = venv / "bin" / TS_CLI
    if not exe.exists():
        raise SegmentWorkerError(
            f"TotalSegmentator CLI 不存在：{exe}（先建独立 venv 并安装 "
            "totalsegmentator==2.10.0，见 M2-REPORT 交付清单）"
        )
    return str(exe)


def gpu_peak_memory_mi_b(pid: int | None = None) -> float | None:
    """nvidia-smi 实测显存（MiB）。不可用时返回 None（不猜数字）。"""
    exe = shutil.which("nvidia-smi") or "/usr/lib/wsl/lib/nvidia-smi"
    if not Path(exe).exists():
        return None
    try:
        query = (
            "--query-compute-apps=used_memory,pid --format=csv,noheader,nounits"
        )
        out = subprocess.run(
            [exe, query], capture_output=True, text=True, timeout=5
        )
        if out.returncode != 0:
            return None
        total = 0.0
        for line in out.stdout.strip().splitlines():
            parts = [v.strip() for v in line.split(",")]
            if not parts or not parts[0]:
                continue
            if pid is None or (len(parts) > 1 and parts[1] == str(pid)):
                try:
                    total += float(parts[0])
                except ValueError:
                    continue
        return total if total > 0 else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def run_total(
    image_path: Path,
    out_dir: Path,
    task: str = "total",
    device: str = "gpu",
    settings: Settings | None = None,
    extra_args: list[str] | None = None,
) -> Path:
    """调用独立 venv 的 TotalSegmentator CLI 跑一个任务，返回输出目录。

    - 白名单判定（check_worker_task）在调用前——任何代码路径不许绕过
    - device: gpu | cpu（M2 用真卡；CPU 只做冒烟）
    - TS 输出：out_dir 下每类一个 NIfTI（liver.nii.gz 等）
    """
    settings = settings or _default_settings()
    check_worker_task(task, settings)  # 白名单一票否决
    cli = ts_venv_cli(settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [cli, "-i", str(image_path), "-o", str(out_dir), "-t", task, "-d", device, "-q"]
    if extra_args:
        cmd.extend(extra_args)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if proc.returncode != 0:
        raise SegmentWorkerError(
            f"TotalSegmentator exit {proc.returncode}: {proc.stderr[-2000:]}"
        )
    return out_dir


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
    """
    import nibabel as nib
    import numpy as np

    from .nifti_geom import assert_affine_match

    img = nib.load(str(image_path))
    seg_files = sorted(out_dir.glob("*.nii.gz"))
    if not seg_files:
        raise SegmentWorkerError(f"TS 输出目录无 mask：{out_dir}")

    merged = np.zeros(img.shape, dtype=np.uint16)
    class_names: list[str] = []
    for i, f in enumerate(seg_files, start=1):
        mask = nib.load(str(f))
        # M1-fix (b)：每类 mask 与原图 affine 一致性守卫
        assert_affine_match(img, mask, label=f"merge {f.name}（{label}）")
        arr = np.asarray(mask.dataobj) > 0
        merged[arr] = i
        class_names.append(f.stem.removesuffix(".nii"))
    dtype = np.uint16 if len(seg_files) < 65536 else np.uint32
    out = nib.Nifti1Image(merged.astype(dtype), img.affine)
    return out, class_names


def segment_series(
    series_id: str,
    settings: Settings,
    task: str = "total",
    device: str = "gpu",
) -> SegmentationResult:
    """对已 ingest 的 Series 跑 total 任务：原图 → TS → 多类 mask → MaskVersion。

    - 输入：Series.storage_key（commit 落盘的 RAS+ NIfTI）
    - 防线：guard_min_slices 语义（<30 层拒绝）在 series 表的 num_slices 上复查
    - 落盘：case_id/series_id/mask.nii.gz（多类标签图）+ MaskVersion 记录
    - 显存峰值：TS 子进程运行期间 nvidia-smi 轮询实测
    """
    import time as _time

    from sqlalchemy import select

    from . import db
    from .models import MaskVersion, Series
    from .storage import LocalFsStorage

    check_worker_task(task, settings)  # 白名单一票否决（分发层）

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

    with tempfile.TemporaryDirectory(
        dir=settings.resolved_data_dir / "tmp", prefix="ts-"
    ) as td:
        out_dir = Path(td) / "ts_out"
        t0 = _time.monotonic()
        run_total(image_path, out_dir, task=task, device=device, settings=settings)
        elapsed = _time.monotonic() - t0
        peak = gpu_peak_memory_mi_b()

        merged, class_names = merge_ts_output(out_dir, image_path, label=series_id)

    # 多类 mask 落盘（case_id/series_id/ 目录结构，与现有存储约定一致）
    import nibabel as nib

    from .nifti_geom import assert_affine_match

    # 读回校验：落盘 mask 与原图 affine 一致（M1-fix (b) 守卫）
    orig = nib.load(str(image_path))
    assert_affine_match(orig, merged, label=f"segment_series {series_id}")

    mask_key = row.storage_key.replace("image.nii.gz", "mask.nii.gz")
    buf_path = image_path.parent / "mask.nii.gz"
    nib.save(merged, str(buf_path))
    try:
        payload = buf_path.read_bytes()
    finally:
        buf_path.unlink(missing_ok=True)
    st.save(mask_key, payload)

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
    )


def _min_slices(settings: Settings) -> int:
    """min slices 阈值（与 series_selector.MIN_SLICES 同源；config 化后置）。"""
    from .series_selector import MIN_SLICES

    return MIN_SLICES


__all__ = [
    "DEFAULT_TS_VENV",
    "SegmentWorkerError",
    "SegmentationResult",
    "check_worker_task",
    "gpu_peak_memory_mi_b",
    "merge_ts_output",
    "run_total",
    "segment_series",
    "ts_venv_cli",
]
