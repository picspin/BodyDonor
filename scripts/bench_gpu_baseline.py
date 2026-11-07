"""M2 GPU 基线测量脚本：TotalSegmentator total 任务在 Quadro RTX 5000 上实测。

测量项（工作指令 §M2：只报实测数）：
- 显存峰值（MiB）：nvidia-smi --query-compute-apps 对 TS 子进程 PID 轮询
- 墙钟耗时：TS 子进程起止
- 体素尺寸与层数：nibabel 读原图 header
- mask 类别数：TS 输出目录 *.nii.gz 计数

用法：
    uv run python scripts/bench_gpu_baseline.py [image.nii.gz] [out_dir]
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

TS_CLI = Path.home() / "work" / "BodyDonor" / ".venv-totalseg" / "bin" / "TotalSegmentator"
NSMI = "/usr/lib/wsl/lib/nvidia-smi"


def poll_peak_mem(pid: int, stop: threading.Event, samples: list[float]) -> None:
    """轮询 nvidia-smi 全局显存 used（MiB），记录峰值。

    WSL2 nvidia-smi --query-compute-apps 对部分进程返回 [N/A]（PID 过滤不可靠），
    改用 --query-gpu=memory.used 全局值；峰值减去推理前基线即推理增量。
    """
    while not stop.is_set():
        try:
            out = subprocess.run(
                [NSMI, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            val = out.stdout.strip().splitlines()[0].strip()
            try:
                samples.append(float(val))
            except ValueError:
                continue  # 偶发 [N/A]，跳过该样本
        except (subprocess.TimeoutExpired, OSError, IndexError):
            pass
        stop.wait(1.0)


def main() -> None:
    default_img = ("data/BodyDonor/downloads/amos22_subset/train"
                   "/imagesTr/amos_0004.nii.gz")
    image = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / default_img)
    out_dir = Path(sys.argv[2] if len(sys.argv) > 2
                   else Path.home() / "data/BodyDonor/tmp/ts-baseline-out")

    import nibabel as nib

    img = nib.load(str(image))
    shape = tuple(int(v) for v in img.shape)
    zooms = tuple(round(float(z), 3) for z in img.header.get_zooms()[:3])
    n_slices = shape[2]

    print(f"image: {image}")
    print(f"voxel_shape (x,y,z): {shape}")
    print(f"voxel_size (mm): {zooms}")
    print(f"num_slices (z): {n_slices}")
    print("task: total, device: gpu")
    print(f"cli: {TS_CLI}")
    print("---")

    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(TS_CLI), "-i", str(image), "-o", str(out_dir),
           "-t", "total", "-d", "gpu", "-q"]

    t0 = time.monotonic()
    # 推理前全局显存基线（桌面残留等），峰值减基线为推理增量
    base_out = subprocess.run(
        [NSMI, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=5,
    )
    try:
        baseline_mi_b = float(base_out.stdout.strip().splitlines()[0].strip())
    except (ValueError, IndexError):
        baseline_mi_b = None
    print(f"baseline_vram_mi_b: {baseline_mi_b:.0f}" if baseline_mi_b is not None
          else "baseline_vram_mi_b: <unavailable>")

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    samples: list[float] = []
    stop = threading.Event()
    th = threading.Thread(target=poll_peak_mem, args=(proc.pid, stop, samples), daemon=True)
    th.start()

    stdout, stderr = proc.communicate()
    stop.set()
    th.join(timeout=2)
    elapsed = time.monotonic() - t0

    rc = proc.returncode
    print(f"exit_code: {rc}")
    if rc != 0:
        print("STDERR tail:")
        print(stderr[-3000:])
        sys.exit(1)

    seg_files = sorted(out_dir.glob("*.nii.gz"))
    peak = max(samples) if samples else None
    if peak is not None and baseline_mi_b is not None:
        print(f"peak_vram_total_mi_b: {peak:.0f}")
        print(f"peak_vram_infer_delta_mi_b: {peak - baseline_mi_b:.0f}")
    print(f"wall_time_s: {elapsed:.1f}")
    print(f"vram_samples_n: {len(samples)}")
    print(f"mask_classes_n: {len(seg_files)}")
    print("---")
    print("classes (first 20):")
    for f in seg_files[:20]:
        print(f"  {f.stem.removesuffix('.nii')}")
    if len(seg_files) > 20:
        print(f"  … 共 {len(seg_files)} 类")


if __name__ == "__main__":
    main()
