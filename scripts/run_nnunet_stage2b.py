#!/usr/bin/env python3
"""M4 阶段 2b：nnU-Net 训练包装（预处理 / 短训 / 正式训练三阶段，全程持 GPU 锁）。

- GPU 锁：包装进程持有（gpu_lock.acquire batch 模式，任务名 nnunet），子进程
  （nnUNetv2_*）结束即释放；nohup 后台运行时锁同样由本进程全程持有——
  文件锁按 pid 存活判定，包装进程活着锁就有效。
- 显存实测（复用 M2-align 3.6 口径）：nvidia-smi 全局轮询峰值 − 启动前基线
  （交叉核对口径，segmentation.gpu_peak_memory_mi_b）；子进程内的 torch
  分配器口径不可从包装进程读，训练内主值以 nnU-Net 自身日志为准（本包装
  只记 nvidia-smi 口径，两个口径不混写）。
- nnUNet 环境变量：无既有 shell profile 约定（实测 ~/.bashrc 等 grep 无
  nnUNet_*），按 stage2b 指令设 ~/data/BodyDonor/nnUNet_{raw,preprocessed,
  results}，**先设变量再 import nnunetv2**（paths 是模块级常量，import 时读）。
- 训练输入只用 Dataset501（TS total 教师 mask + 纯人工修正，许可门禁在
  distill_data.prepare_dataset 预检强制）；学生输出不回灌（本脚本只训，
  不做推理回写）。
- 训练期间绝不同时跑 TotalSegmentator 推理（GPU 锁互斥保证：本包装持锁
  期间 TS worker acquire 会排队/失败）。

用法：
    uv run python scripts/run_nnunet_stage2b.py preprocess   # 3d_lowres 预处理
    uv run python scripts/run_nnunet_stage2b.py short-train  # 5 epochs 短训
    uv run python scripts/run_nnunet_stage2b.py train --epochs N  # 正式训练
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# nnUNet 环境变量（先设再 import nnunetv2 相关模块——包装脚本本身不 import
# nnunetv2，但 CLI 子进程读取；这里显式设并由本脚本传给子进程）
NNUNET_ENV = {
    "nnUNet_raw": str(Path.home() / "data" / "BodyDonor" / "nnUNet_raw"),
    "nnUNet_preprocessed": str(Path.home() / "data" / "BodyDonor" / "nnUNet_preprocessed"),
    "nnUNet_results": str(Path.home() / "data" / "BodyDonor" / "nnUNet_results"),
    # 禁用 torch.compile（nnU-Net 文档化开关 nnUNet_compile）：本机无系统 C
    # 编译器（gcc/cc/clang 实测均缺失，dpkg 无 root 装不了），inductor/Triton
    # 编译失败实测（"Failed to find C compiler"）。eager 模式等价训练（仅速度
    # 差异），fp16 AMP autocast 不受影响。
    "nnUNet_compile": "false",
    # 限制默认进程数（nnU-Net 文档化开关 nnUNet_def_n_proc）：默认 8——短训第
    # 三次实测 validation 导出池 spawn 8 worker 全灭（"Some background workers
    # are no longer alive"，与预处理 2/3 的 worker OOM 同源：WSL 15GB 内存）。
    # 2 保证导出池存活（预处理 -np 1/2 实测通过），训练/导出串行不受影响。
    "nnUNet_def_n_proc": "2",
}

NNUNET_PY = Path.home() / "work" / "BodyDonor" / ".venv-nnunet" / "bin" / "python"
NNUNET_BIN = NNUNET_PY.parent
DATASET = "501"
GPU_LOCK_PATH = Path.home() / "data" / "BodyDonor" / ".gpu.lock"
MEASURE_DIR = Path.home() / "m4-logs"


def nvidia_smi_used_mb() -> float | None:
    """nvidia-smi 实测全局显存（MiB）。WSL2 口径与 segmentation.gpu_peak_memory_mi_b
    同源（--query-gpu=memory.used 全局值，取数字部分）。不可用返回 None。"""
    exe = "/usr/lib/wsl/lib/nvidia-smi"
    if not Path(exe).exists():
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.used", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None
        return float(out.stdout.strip().splitlines()[0].split()[0])
    except (subprocess.TimeoutExpired, OSError, ValueError, IndexError):
        return None


class SmiPoller:
    """后台线程轮询 nvidia-smi（1s 间隔），峰值 = max(samples) - baseline。"""

    def __init__(self) -> None:
        self.baseline = nvidia_smi_used_mb()
        self.samples: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(1.0):
            v = nvidia_smi_used_mb()
            if v is not None:
                self.samples.append(v)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> float | None:
        """返回峰值（MiB，减基线后的净增量）；无样本返回 None（不猜）。"""
        self._stop.set()
        self._thread.join(timeout=5)
        if not self.samples:
            return None
        peak = max(self.samples) - (self.baseline or 0.0)
        return peak if peak > 0 else None


def subprocess_run(cmd: list[str], log_prefix: str) -> dict:
    """跑子进程，全程显存轮询；返回实测记录。stdout/stderr 直接透传终端
    （nohup 时进日志文件）。"""
    env = os.environ.copy()
    env.update(NNUNET_ENV)
    env["PATH"] = str(NNUNET_BIN) + os.pathsep + env.get("PATH", "")
    poller = SmiPoller()
    poller.start()
    t0 = time.monotonic()
    proc = subprocess.run(cmd, env=env)
    elapsed = time.monotonic() - t0
    peak = poller.stop()
    record = {
        "cmd": cmd,
        "exit_code": proc.returncode,
        "elapsed_s": round(elapsed, 1),
        "peak_vram_smi_net_mb": peak,
        "smi_baseline_mb": poller.baseline,
        "n_smi_samples": len(poller.samples),
    }
    print(f"[{log_prefix}] exit={proc.returncode} elapsed={elapsed:.1f}s "
          f"peak_smi_net={peak}MB (baseline={poller.baseline}MB, "
          f"samples={len(poller.samples)})", flush=True)
    return record


def require_lock() -> None:
    """抢 GPU 锁（batch 模式，任务名 nnunet）。失败即中止（不排队硬等——
    本脚本由上游按 GPU 空闲窗口启动）。

    锁持有方式：包装进程全程持有（nohup 下进程活着锁即有效）；命令跑完
    在 release_lock() 显式释放（atexit 兜底——不靠 stale 回收清理自己的锁）。
    """
    from bodydonor.gpu_lock import acquire
    job_id = f"nnunet-stage2b-{os.getpid()}"
    lock = acquire(GPU_LOCK_PATH, reason="batch", task="nnunet",
                   job_id=job_id, wait=30)
    if lock is None:
        print("[stage2b] GPU 锁被占（batch 排队超时 30s），中止。"
              "训练期间绝不同时跑 TotalSegmentator 推理。", file=sys.stderr)
        sys.exit(3)
    globals()["_stage2b_lock"] = lock
    print(f"[stage2b] GPU 锁已持有：pid={os.getpid()} task=nnunet job={job_id} "
          f"（包装进程存活期间锁有效，命令结束显式释放）", flush=True)


def release_lock() -> None:
    """显式释放 GPU 锁（atexit 兜底；异常退出时进程死亡由 stale 回收兜底）。"""
    lock = globals().get("_stage2b_lock")
    if lock is not None:
        lock.release()
        globals()["_stage2b_lock"] = None


def _register_atexit() -> None:
    import atexit
    atexit.register(release_lock)


_register_atexit()


def write_measure(name: str, record: dict) -> Path:
    MEASURE_DIR.mkdir(parents=True, exist_ok=True)
    out = MEASURE_DIR / name
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"[stage2b] 实测记录 -> {out}", flush=True)
    return out


def cmd_preprocess(args: argparse.Namespace) -> int:
    require_lock()
    configs = args.configs.split(",")
    cmd = [str(NNUNET_PY), "-m", "nnunetv2.experiment_planning.plan_and_preprocess_entrypoints",
           "-d", DATASET, "-c", *configs]
    if args.np:
        cmd += ["-np", str(args.np)]
    print(f"[stage2b] 预处理命令：{' '.join(cmd)}", flush=True)
    rec = subprocess_run(cmd, "preprocess")
    write_measure("stage2b-preprocess.json", rec)

    # plans 关键参数实测落盘
    plans_path = (
        Path(NNUNET_ENV["nnUNet_preprocessed"])
        / "Dataset501_BodyDonorDistill" / "nnUNetPlans.json"
    )
    if plans_path.exists():
        plans = json.loads(plans_path.read_text(encoding="utf-8"))
        cfg = plans["configurations"].get("3d_lowres", {})
        key = {
            "plans_file": str(plans_path),
            "configurations_available": sorted(plans["configurations"].keys()),
            "3d_lowres": {
                "patch_size": cfg.get("patch_size"),
                "batch_size": cfg.get("batch_size"),
                "spacing": cfg.get("spacing"),
                "batch_dice": cfg.get("batch_dice"),
                "arch_class_name": cfg.get("architecture", {}).get("arch_class_name"),
            },
            "preprocess_exit_code": rec["exit_code"],
            "preprocess_elapsed_s": rec["elapsed_s"],
            "preprocess_peak_smi_net_mb": rec["peak_vram_smi_net_mb"],
        }
        write_measure("stage2b-plans-keyparams.json", key)
    return 0 if rec["exit_code"] == 0 else 1


def cmd_short_train(args: argparse.Namespace) -> int:
    require_lock()
    cmd = [str(NNUNET_PY), "-m", "nnunetv2.run.run_training",
           "Dataset501_BodyDonorDistill", "3d_lowres", "0",
           "-tr", "nnUNetTrainer_5epochs"]
    print(f"[stage2b] 短训命令：{' '.join(cmd)}", flush=True)
    rec = subprocess_run(cmd, "short-train")
    write_measure("stage2b-short-train.json", rec)
    return 0 if rec["exit_code"] == 0 else 1


def cmd_train(args: argparse.Namespace) -> int:
    require_lock()
    epochs = args.epochs
    # 标准 1000-epoch trainer 的类名就是 nnUNetTrainer（num_epochs=1000 默认）；
    # nnUNetTrainer_Nepochs 变体只有 5epochs 等短训版（1000epochs 类不存在，
    # recursive_find_trainer_class_by_name 实测 RuntimeError）。--epochs 只用于
    # 文档/推算，实际 epoch 数由 trainer 类决定（nnUNetTrainer = 1000）。
    if epochs != 1000:
        raise SystemExit(
            f"--epochs {epochs} 不受支持：标准 trainer 类名 nnUNetTrainer 固定 "
            "1000 epochs（短训用 short-train 子命令的 nnUNetTrainer_5epochs）"
        )
    cmd = [str(NNUNET_PY), "-m", "nnunetv2.run.run_training",
           "Dataset501_BodyDonorDistill", "3d_lowres", "0",
           "-tr", "nnUNetTrainer"]
    print(f"[stage2b] 正式训练命令：{' '.join(cmd)}", flush=True)
    print(f"[stage2b] 启动时间：{time.strftime('%Y-%m-%d %H:%M:%S %z')}", flush=True)
    rec = subprocess_run(cmd, "train")
    write_measure("stage2b-train.json", rec)
    return 0 if rec["exit_code"] == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_prep = sub.add_parser("preprocess", help="nnUNetv2_plan_and_preprocess -d 501")
    p_prep.add_argument("--configs", default="3d_lowres",
                        help="逗号分隔的配置列表（默认 3d_lowres）")
    p_prep.add_argument("--np", type=int, default=0,
                        help="预处理进程数（默认 nnU-Net 自定；OOM 时降为 1-2）")
    sub.add_parser("short-train", help="nnUNetTrainer_5epochs 端到端验证")
    p_train = sub.add_parser("train", help="正式训练（-tr nnUNetTrainer，标准 1000 epochs）")
    p_train.add_argument("--epochs", type=int, default=1000)
    args = ap.parse_args()

    if args.cmd == "preprocess":
        return cmd_preprocess(args)
    if args.cmd == "short-train":
        return cmd_short_train(args)
    return cmd_train(args)


if __name__ == "__main__":
    sys.exit(main())
