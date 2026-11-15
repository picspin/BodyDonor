"""TotalSegmentator runner（M2-align 3.2）：在 TS 独立 venv 内执行的 JSON 任务 runner。

**执行环境**：`.venv-totalseg/bin/python src/bodydonor/ts_runner.py`（torch/totalsegmentator
只在该 venv 可用，主 .venv 隔离——Q7）。主进程（segmentation.py）以 **JSON 任务**
经 stdin 调它、以 **JSON 结果**经 stdout 收它。

契约（M2-ALIGN-REPORT 3.2 写明）：

- **任务 JSON**（stdin，UTF-8）：`{input_nii, task, device, output_dir, job_id}`，
  可选 `extra_args: [str]`（透传 TS python API 关键字以外的场景不用，M2 不传）。
- **结果 JSON**（stdout，UTF-8，**纯 JSON**——日志一律走 stderr，不混入）：
  `{status, exit_code, mask_path, classes: [str], wall_seconds, peak_vram_mb,
  error}`。status ∈ ok | error；peak_vram_mb 不可用时（CPU 回退 / 无 CUDA）填
  null 并在 error/reason 注明，**不猜数字**。
- **显存峰值**：`torch.cuda.reset_peak_memory_stats()` 起始 +
  `torch.cuda.max_memory_allocated()` 结束，换算 MiB。这是主值（分配器口径）；
  主进程的 nvidia-smi 轮询是交叉核对值（全局口径，两者都记录）。
- **白名单**：主进程先做（check_worker_task）；runner 收到任务后再查一次
  RUNNER_BLOCKED_TASKS（纵深防线，runner 不信任 stdin 输入）。

用法（主进程不直接跑本文件，由 segmentation.build_runner_cmd 组装命令）：

    echo '{"input_nii": "...", "task": "total", ...}' | \\
        .venv-totalseg/bin/python src/bodydonor/ts_runner.py
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

# 本文件由 `.venv-totalseg/bin/python` 执行（torch/totalsegmentator 只在该 venv
# 可用，主 .venv 隔离——Q7）。主 .venv import 本模块只拿任务/结果契约常量，
# 不触发 TS import（延迟到 run_task/main 内）。

# runner 侧白名单纵深：只查本黑名单（主进程 check_worker_task 是第一道，
# 走许可注册表；这里第二道，防绕过）
RUNNER_BLOCKED_TASKS = {"brain_aneurysm"}


def _log(msg: str) -> None:
    """日志只走 stderr（stdout 是纯 JSON 契约通道）。"""
    print(msg, file=sys.stderr, flush=True)


def _classes_for_task(task: str) -> list[str]:
    """TS 任务的全部输出类名（class_map 表；total=117 类）。

    类名列表来自 TS 包自带类别表（totalsegmentator.map_to_binary.class_map），
    与实际落盘的每类 NIfTI 一致；表里没有的任务（自定义 task）回退为空列表，
    由主进程从输出目录文件名兜底。
    """
    try:
        from totalsegmentator.map_to_binary import class_map
    except ImportError:
        return []
    try:
        cm = class_map.get(task)
        if not cm:
            return []
        return [name for _, name in sorted(cm.items(), key=lambda kv: kv[0])]
    except Exception:  # noqa: BLE001 — 类别表异常不挡推理，主进程兜底
        return []


def _peak_vram_mi_b() -> tuple[float | None, str]:
    """torch.cuda.max_memory_allocated() 实测显存峰值（MiB）。

    返回 (值 | None, reason)。值不可用（无 CUDA / CPU 回退）时返回
    (None, reason)，**不猜数字**。
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return None, "CUDA 不可用（CPU 回退），torch 分配器无显存峰值"
        peak_bytes = torch.cuda.max_memory_allocated()
        if peak_bytes <= 0:
            return None, "torch.cuda.max_memory_allocated() 返回 0（无 CUDA 分配记录）"
        return peak_bytes / (1024 * 1024), ""
    except Exception as e:  # noqa: BLE001 — torch 异常不挡结果上报
        return None, f"显存峰值读取失败：{type(e).__name__}: {e}"


def run_task(task_json: dict[str, Any]) -> dict[str, Any]:
    """执行单个 TS 任务，返回结果 JSON（dict）。"""
    job_id = str(task_json.get("job_id", ""))
    input_nii = Path(str(task_json["input_nii"]))
    task = str(task_json["task"])
    device = str(task_json.get("device", "gpu"))
    output_dir = Path(str(task_json["output_dir"]))

    # runner 侧白名单纵深（blocked 一票否决；主进程已查过一次）
    if task in RUNNER_BLOCKED_TASKS:
        return {
            "status": "error",
            "exit_code": 2,
            "mask_path": None,
            "classes": [],
            "wall_seconds": 0.0,
            "peak_vram_mb": None,
            "error": f"任务白名单拒绝（runner 侧纵深）：'{task}' is BLOCKED "
            "(license: see LICENSES-THIRD-PARTY.md)",
        }

    if not input_nii.exists():
        return {
            "status": "error",
            "exit_code": 2,
            "mask_path": None,
            "classes": [],
            "wall_seconds": 0.0,
            "peak_vram_mb": None,
            "error": f"输入不存在：{input_nii}",
        }

    # TS venv 内 totalsegmentator 包的 python API（延迟 import：主 .venv 不触发）
    from totalsegmentator.python_api import totalsegmentator

    _log(f"[ts_runner] job={job_id} task={task} device={device} input={input_nii.name}")

    # 显存峰值测量：起始 reset，结束 max_memory_allocated（分配器口径，主值）
    vram_note = ""
    try:
        import torch

        if device == "gpu" and torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.synchronize()
        else:
            vram_note = "CPU 回退或 CUDA 不可用，peak_vram_mb 不可测（null，不猜数字）"
    except Exception as e:  # noqa: BLE001
        vram_note = f"peak 重置失败：{type(e).__name__}: {e}"

    t0 = time.monotonic()
    exit_code = 0
    error = ""
    try:
        totalsegmentator(
            input=input_nii,
            output=output_dir,
            task=task,
            device=device,
            quiet=True,
        )
    except SystemExit as e:  # TS 内部 sys.exit
        exit_code = int(e.code or 0) if e.code else 0
        if exit_code != 0:
            error = f"TotalSegmentator SystemExit({e.code})"
    except Exception as e:  # noqa: BLE001
        exit_code = 1
        error = f"{type(e).__name__}: {e}"
        _log(traceback.format_exc())
    wall = time.monotonic() - t0

    peak, peak_reason = _peak_vram_mi_b()
    if peak is None and not peak_reason:
        peak_reason = "peak 值不可用"

    # TS 输出：output_dir 下每类一个 NIfTI（liver.nii.gz 等）
    seg_files = sorted(output_dir.glob("*.nii.gz")) if output_dir.exists() else []
    classes_from_files = [f.name.removesuffix(".nii.gz") for f in seg_files]
    classes = _classes_for_task(task) or classes_from_files

    if exit_code != 0 or not seg_files:
        if not error:
            error = "TS 输出目录无 mask（exit 0 但无 NIfTI 落盘）"
        return {
            "status": "error",
            "exit_code": exit_code or 1,
            "mask_path": None,
            "classes": [],
            "wall_seconds": round(wall, 2),
            "peak_vram_mb": round(peak, 1) if peak is not None else None,
            "error": error if not vram_note else f"{error}；{vram_note}",
        }

    return {
        "status": "ok",
        "exit_code": 0,
        "mask_path": str(output_dir),
        "classes": classes,
        "wall_seconds": round(wall, 2),
        "peak_vram_mb": round(peak, 1) if peak is not None else None,
        "error": vram_note,  # ok 但峰值不可用时写 reason（主进程透传）
    }


def main() -> int:
    """stdin 读任务 JSON（单行或整段），stdout 写结果 JSON（纯 JSON）。"""
    try:
        raw = sys.stdin.read()
        task_json = json.loads(raw)
    except json.JSONDecodeError as e:
        result = {
            "status": "error",
            "exit_code": 2,
            "mask_path": None,
            "classes": [],
            "wall_seconds": 0.0,
            "peak_vram_mb": None,
            "error": f"任务 JSON 解析失败：{e}",
        }
        json.dump(result, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 2
    result = run_task(task_json)
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if result["status"] == "ok" else (result.get("exit_code") or 1)


if __name__ == "__main__":
    sys.exit(main())
