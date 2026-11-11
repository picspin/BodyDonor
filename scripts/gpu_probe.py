#!/usr/bin/env python3
"""M5 Edge 安装器 GPU 探针（SegMed 模式改写，不抄其驱动门槛）。

流程（SegMed installer/select_torch_backend.py 的模式）：
nvidia-smi 检测 → 排序 → **实跑 CUDA 验算通过才算选中** → 不行 CPU 兜底
→ --sample-file 离线验证。

与 SegMed 的关键区别（M5-PLAN §1.1，用户定）：
SegMed 的硬门槛 CU12.6 需驱动 ≥560.76、CU12.8 需 ≥570.65，会把本机
Quadro RTX 5000（驱动 556.18，算力 7.5）判为 CPU-only。**本探针不设
驱动版本号门槛**：是否选 CUDA 只由实跑验算决定（torch 2.5.1+cu124
在驱动 556.18 上实测可跑，见 M5-REPORT §4）。门槛按实测定，不是抄表。

用法：
    python scripts/gpu_probe.py [--python .venv-nninteractive/bin/python]
    python scripts/gpu_probe.py --sample-file tests/data/nvidia_smi_sample.csv
    python scripts/gpu_probe.py --json

- 默认 CUDA 验算用 --python 指定的解释器（须装 torch；缺省为当前 python）。
- GPU 操作走 gpu_lock（interactive，wait=0）：批量任务占卡时直接报 CPU
  兜底（不排队）；--no-lock 跳过锁（仅离线/单机调试用）。
- 退出码恒为 0（只要产出决策）；--sample-file 不存在时退出 2。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

NSMI = shutil.which("nvidia-smi") or "/usr/lib/wsl/lib/nvidia-smi"

# CUDA 验算程序（子进程执行；只做一次小矩阵乘法 + 同步，不加载模型）。
CUDA_CHECK_PROGRAM = (
    "import json, time, torch; "
    "t0=time.monotonic(); "
    "assert torch.cuda.is_available(), 'torch.cuda.is_available() is False'; "
    "x=torch.randn(1024, 1024, device='cuda'); y=x @ x; torch.cuda.synchronize(); "
    "print(json.dumps({'ok': True, 'torch': torch.__version__, "
    "'cuda_runtime': torch.version.cuda, "
    "'device': torch.cuda.get_device_name(0), "
    "'capability': list(torch.cuda.get_device_capability(0)), "
    "'peak_mib': round(torch.cuda.max_memory_allocated() / 1048576, 1), "
    "'elapsed_s': round(time.monotonic() - t0, 3)}))"
)


def parse_smi_csv(text: str) -> list[dict]:
    """解析 nvidia-smi CSV（--query-gpu=index,name,uuid,driver_version,
    compute_cap,memory.total）。

    产品名可含逗号：从右取 4 个固定字段（uuid / driver_version /
    compute_cap / memory.total），首字段为 index，中间拼回 name；
    uuid 须以 GPU- 开头，否则该行丢弃（不猜）。
    """
    gpus: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("index"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        memory_total = parts[-1]
        compute_cap = parts[-2]
        driver_version = parts[-3]
        uuid = parts[-4]
        index = parts[0]
        name = ", ".join(parts[1:-4])
        if not uuid.startswith("GPU-"):
            continue
        try:
            idx = int(index)
            cap = float(compute_cap)
            total_mib = int(float(memory_total.split()[0]))
        except (ValueError, IndexError):
            continue
        gpus.append(
            {
                "index": idx,
                "name": name,
                "uuid": uuid,
                "driver_version": driver_version,
                "compute_cap": cap,
                "memory_total_mib": total_mib,
            }
        )
    return gpus


def detect_gpus(smi_text: str | None = None) -> list[dict]:
    """nvidia-smi 检测；不可用（无命令/执行失败）返回 []（不猜有卡）。"""
    if smi_text is not None:
        return parse_smi_csv(smi_text)
    exe = NSMI
    if not Path(exe).exists():
        return []
    try:
        out = subprocess.run(  # noqa: S603 — 固定命令固定参数
            [
                exe,
                "--query-gpu=index,name,uuid,driver_version,compute_cap,memory.total",
                "--format=csv",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if out.returncode != 0:
        return []
    return parse_smi_csv(out.stdout)


def select_candidate(gpus: list[dict]) -> dict | None:
    """排序：算力高优先 → 序号小优先；无卡返回 None。"""
    if not gpus:
        return None
    return sorted(gpus, key=lambda g: (-g["compute_cap"], g["index"]))[0]


def run_cuda_check(python_exe: str, timeout: int = 180) -> tuple[bool, dict]:
    """实跑 CUDA 验算：指定解释器里 torch 分配 + 矩阵乘 + 同步。

    返回 (ok, detail)。失败（无 torch / 无 CUDA / 超时 / 报错）返回
    (False, {"error": ...})，不猜数字。
    """
    t0 = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 — 解释器路径由调用方指定
            [python_exe, "-c", CUDA_CHECK_PROGRAM],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, {"error": f"验算进程失败：{type(e).__name__}: {e}"}
    wall = round(time.monotonic() - t0, 3)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, {"error": (err[-1] if err else f"exit {proc.returncode}"), "wall_s": wall}
    try:
        detail = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, {"error": f"验算输出非 JSON：{proc.stdout[:200]!r}", "wall_s": wall}
    detail["wall_s"] = wall
    return bool(detail.get("ok")), detail


def probe(
    python_exe: str | None = None,
    sample_file: str | Path | None = None,
    cuda_check_fn=None,
) -> dict:
    """一次探测，返回决策 dict（总有 backend 字段，绝不抛异常出决策）。

    - sample_file：离线验证（只校验 nvidia-smi 解析 + 选择逻辑，不实跑
      CUDA，backend 恒为 cpu，reason 写明未验算）。
    - cuda_check_fn：验算函数注入点（默认 run_cuda_check；测试用）。
    """
    python_exe = python_exe or sys.executable
    if sample_file is not None:
        try:
            text = Path(sample_file).read_text(encoding="utf-8")
        except OSError as e:
            return {
                "backend": "cpu",
                "candidate": None,
                "cuda_check": {"ran": False, "ok": False, "error": str(e)},
                "reason": f"sample-file 不可读：{e}",
            }
        candidate = select_candidate(parse_smi_csv(text))
        return {
            "backend": "cpu",
            "candidate": candidate,
            "cuda_check": {"ran": False, "ok": False, "error": "离线 sample：未实跑 CUDA"},
            "reason": "离线验证模式：仅校验选择逻辑，未实跑 CUDA，不宣称 CUDA 可用",
        }

    gpus = detect_gpus()
    candidate = select_candidate(gpus)
    if candidate is None:
        return {
            "backend": "cpu",
            "candidate": None,
            "cuda_check": {"ran": False, "ok": False, "error": "未检测到 NVIDIA GPU"},
            "reason": "nvidia-smi 未检测到 GPU（命令缺失/执行失败/无卡），CPU 兜底",
        }
    check = cuda_check_fn or (lambda: run_cuda_check(python_exe))
    ok, detail = check()
    if ok:
        return {
            "backend": "cuda",
            "candidate": candidate,
            "cuda_check": {"ran": True, "ok": True, **detail},
            "reason": (
                f"实跑 CUDA 验算通过（{candidate['name']}，驱动 "
                f"{candidate['driver_version']}，算力 {candidate['compute_cap']}）"
            ),
        }
    return {
        "backend": "cpu",
        "candidate": candidate,
        "cuda_check": {"ran": True, "ok": False, **detail},
        "reason": f"CUDA 验算失败（{detail.get('error', '未知')}），CPU 兜底",
    }


def main(argv: list[str] | None = None) -> int:
    """CLI：GPU 锁环绕实跑验算；恒输出决策 JSON（--json）或可读文本。"""
    ap = argparse.ArgumentParser(description="BodyDonor M5 GPU 探针（检测→排序→实跑验算→CPU 兜底）")
    ap.add_argument("--python", default=sys.executable, help="跑 CUDA 验算的解释器（须装 torch）")
    ap.add_argument("--sample-file", default=None, help="离线验证：录好的 nvidia-smi 输出")
    ap.add_argument("--json", action="store_true", help="只输出决策 JSON")
    ap.add_argument("--no-lock", action="store_true", help="跳过 GPU 锁（仅离线/单机调试）")
    args = ap.parse_args(argv)

    if args.sample_file is not None and not Path(args.sample_file).exists():
        print(f"sample-file 不存在：{args.sample_file}", file=sys.stderr)
        return 2

    lock = None
    lock_error: str | None = None
    if args.sample_file is None and not args.no_lock:
        try:
            from bodydonor.config import load_settings
            from bodydonor.gpu_lock import acquire

            settings = load_settings()
            lock = acquire(
                settings.resolved_data_dir / ".gpu.lock",
                reason="interactive",
                task="gpu-probe",
                job_id="gpu-probe",
                wait=0,
            )
        except Exception as e:  # noqa: BLE001 — 锁模块不可用时记下，不挡探测
            lock_error = f"gpu_lock 不可用（{type(e).__name__}: {e}），未持锁验算"
        if lock is None and lock_error is None:
            # acquire 返回 None = 锁被批量任务持有（fast-fail，不排队）
            decision = {
                "backend": "cpu",
                "candidate": select_candidate(detect_gpus()),
                "cuda_check": {"ran": False, "ok": False, "error": "GPU 锁被占"},
                "reason": "GPU 被批量推理/训练任务占用（锁），未实跑验算，CPU 兜底",
            }
            print(
                json.dumps(decision, ensure_ascii=False, indent=1)
                if args.json
                else _pretty(decision)
            )
            return 0
    try:
        decision = probe(python_exe=args.python, sample_file=args.sample_file)
    finally:
        if lock is not None:
            lock.release()
    if lock_error is not None:
        decision["lock_note"] = lock_error
    if args.json:
        print(json.dumps(decision, ensure_ascii=False, indent=1))
    else:
        print(_pretty(decision))
        if lock_error is not None:
            print(f"lock_note: {lock_error}")
    return 0


def _pretty(decision: dict) -> str:
    lines = [f"backend: {decision['backend']}"]
    cand = decision.get("candidate")
    if cand:
        lines.append(
            f"candidate: [{cand['index']}] {cand['name']} "
            f"(driver {cand['driver_version']}, compute_cap {cand['compute_cap']}, "
            f"{cand['memory_total_mib']} MiB)"
        )
    chk = decision.get("cuda_check") or {}
    lines.append(f"cuda_check: ran={chk.get('ran')} ok={chk.get('ok')}")
    for k in ("torch", "cuda_runtime", "device", "capability", "peak_mib", "elapsed_s", "error"):
        if chk.get(k) is not None:
            lines.append(f"  {k}: {chk[k]}")
    lines.append(f"reason: {decision['reason']}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
