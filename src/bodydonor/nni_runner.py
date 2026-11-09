"""nnInteractive runner（M4 阶段 3）：在 nnInteractive 独立 venv 内执行的 JSON 任务 runner。

**执行环境**：`.venv-nninteractive/bin/python src/bodydonor/nni_runner.py`
（torch/nnInteractive 只在该 venv 可用，主 .venv 隔离——Q7 隔离纪律）。
主进程（nninteractive_backend.py）以 **JSON 任务**经 stdin 调它、以 **JSON 结果**
经 stdout 收它（照抄 ts_runner.py 契约，M2-align 3.2）。

契约：

- **任务 JSON**（stdin，UTF-8）：`{image_nii, prompts, target_label, device,
  output_dir, job_id}`。prompts 为列表：`{"type": "point", "x": .., "y": ..,
  "z": .., "include": true|false}`（正/负点）或
  `{"type": "bbox", "bbox": [[x0,x1],[y0,y1],[z0,z1]]}`（2D bbox：一维须为
  [d, d+1] 表示单一切片，上游限制）。
- **结果 JSON**（stdout，UTF-8，**纯 JSON**——日志一律走 stderr，不混入）：
  `{status, exit_code, mask_path, wall_seconds, load_seconds, inference_seconds,
  peak_vram_mb, error}`。status ∈ ok | error；peak_vram_mb 不可用时（CPU 模式 /
  无 CUDA）填 null 并在 error/reason 注明，**不猜数字**。
- **显存峰值**：`torch.cuda.reset_peak_memory_stats()` 起始 +
  `torch.cuda.max_memory_allocated()` 结束，换算 MiB（分配器口径，主值）；
  主进程的 nvidia-smi 轮询是交叉核对值（全局口径，两者都记录）。
- **原图不做预处理**：session.set_image 直接吃原 NIfTI 数组（(1,x,y,z) float），
  不归一化、不转 uint8、不裁剪（上游 API 事实：set_image 原样吃体数据）。
- **target buffer**：`torch.zeros(img.shape[1:], dtype=torch.uint8)`，结果直接
  写进 buffer（每次点/框交互后立即生效）。

用法（主进程不直接跑本文件，由 nninteractive_backend.build_runner_cmd 组装命令）：

    echo '{"image_nii": "...", "prompts": [...], ...}' | \\
        .venv-nninteractive/bin/python src/bodydonor/nni_runner.py
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

# 本文件由 `.venv-nninteractive/bin/python` 执行（torch/nnInteractive 只在该 venv
# 可用）。主 .venv import 本模块只拿契约常量，不触发 torch/nnInteractive import
# （延迟到 run_task/main 内）。

# CPU 模式推理没有可测显存；CPU 推理慢（上游无官方 CPU 支持承诺），只做冒烟
# 与小 patch 验证。
RESULT_FIELDS = (
    "status",
    "exit_code",
    "mask_path",
    "wall_seconds",
    "load_seconds",
    "inference_seconds",
    "peak_vram_mb",
    "error",
)


def _log(msg: str) -> None:
    """日志只走 stderr（stdout 是纯 JSON 契约通道）。"""
    print(msg, file=sys.stderr, flush=True)


def _error_result(exit_code: int, error: str) -> dict[str, Any]:
    return {
        "status": "error",
        "exit_code": exit_code,
        "mask_path": None,
        "wall_seconds": 0.0,
        "load_seconds": 0.0,
        "inference_seconds": 0.0,
        "peak_vram_mb": None,
        "error": error,
    }


def _peak_vram_mi_b(device: str) -> tuple[float | None, str]:
    """torch.cuda.max_memory_allocated() 实测显存峰值（MiB）。

    返回 (值 | None, reason)。值不可用（CPU 模式 / 无 CUDA）时返回
    (None, reason)，**不猜数字**。
    """
    try:
        import torch

        if device != "cuda" or not torch.cuda.is_available():
            return None, (
                "CUDA 不可用或 CPU 模式，torch 分配器无显存峰值"
                "（peak_vram_mb=null，不猜数字）"
            )
        peak_bytes = torch.cuda.max_memory_allocated()
        if peak_bytes <= 0:
            return None, "torch.cuda.max_memory_allocated() 返回 0（无 CUDA 分配记录）"
        return peak_bytes / (1024 * 1024), ""
    except Exception as e:  # noqa: BLE001 — torch 异常不挡结果上报
        return None, f"显存峰值读取失败：{type(e).__name__}: {e}"


def _validate_prompts(prompts: Any) -> tuple[list[dict[str, Any]], str]:
    """prompt 结构校验（runner 不信任 stdin 输入）。返回 (规范化列表, 错误)。"""
    if not isinstance(prompts, list) or not prompts:
        return [], "prompts 必须是非空列表"
    out: list[dict[str, Any]] = []
    for i, p in enumerate(prompts):
        if not isinstance(p, dict):
            return [], f"prompts[{i}] 必须是对象"
        ptype = p.get("type")
        if ptype == "point":
            try:
                xyz = (float(p["x"]), float(p["y"]), float(p["z"]))
            except (KeyError, TypeError, ValueError):
                return [], f"prompts[{i}] point 缺 x/y/z 或非数值"
            out.append({"type": "point", "xyz": xyz, "include": bool(p.get("include", True))})
        elif ptype == "bbox":
            bbox = p.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 3:
                return [], (
                    f"prompts[{i}] bbox 须为 [[x0,x1],[y0,y1],[z0,z1]]"
                    "（2D bbox：单切片维 [d,d+1]）"
                )
            try:
                norm = [[float(v) for v in axis] for axis in bbox]
            except (TypeError, ValueError):
                return [], f"prompts[{i}] bbox 数值非法"
            for axis in norm:
                if len(axis) != 2 or axis[1] < axis[0]:
                    return [], f"prompts[{i}] bbox 每维须为 [lo,hi] 且 hi>=lo（单切片维用 [d,d+1]）"
            out.append({"type": "bbox", "bbox": norm})
        else:
            return [], f"prompts[{i}] type 须为 point|bbox，收到 {ptype!r}"
    return out, ""


def run_task(task_json: dict[str, Any]) -> dict[str, Any]:
    """执行单个 nnInteractive 修正任务，返回结果 JSON（dict）。"""
    job_id = str(task_json.get("job_id", ""))
    image_nii = Path(str(task_json["image_nii"]))
    device = str(task_json.get("device", "cuda"))
    output_dir = Path(str(task_json["output_dir"]))
    target_label = int(task_json.get("target_label", 1))
    prompts, perr = _validate_prompts(task_json.get("prompts"))
    if perr:
        return _error_result(2, perr)
    if not image_nii.exists():
        return _error_result(2, f"输入不存在：{image_nii}")

    # nnInteractive venv 内延迟 import（主 .venv 不触发）
    import nibabel as nib
    import numpy as np
    import torch
    from nnInteractive.inference.inference_session import nnInteractiveInferenceSession

    from bodydonor.nifti_geom import assert_affine_match

    model_dir = task_json.get("model_dir")
    _log(f"[nni_runner] job={job_id} device={device} input={image_nii.name} "
         f"prompts={len(prompts)} model_dir={model_dir}")

    # ---- 加载原图（不预处理）----
    t_load0 = time.monotonic()
    img_nib = nib.load(str(image_nii))
    img_arr = np.asarray(img_nib.dataobj, dtype=np.float32)
    # set_image 吃 (1, x, y, z)
    img_t = torch.from_numpy(img_arr[None])
    target_buffer = torch.zeros(img_arr.shape, dtype=torch.uint8)

    # ---- 初始化会话 + 加载模型 ----
    load_seconds = 0.0
    license_id = ""
    session: Any = None
    try:
        session = nnInteractiveInferenceSession(
            device=torch.device("cuda:0" if device == "cuda" else "cpu"),
            use_torch_compile=False,  # 短会话不编译（每次修正 acquire→跑→存→放）
            do_autozoom=True,
        )
        session.initialize_from_trained_model_folder(str(model_dir))
        license_id = str(getattr(session, "license", "") or "")
    except Exception as e:  # noqa: BLE001
        _log(traceback.format_exc())
        return _error_result(1, f"模型加载失败：{type(e).__name__}: {e}")
    load_seconds = time.monotonic() - t_load0
    if license_id and "CC BY-NC-SA" not in license_id:
        # 权重许可事实源是模型文件夹自带的 LICENSE 文件（session.license）。
        # 非预期许可不静默放行——上游约定 v1 checkpoint = CC BY-NC-SA 4.0。
        _log(f"[nni_runner] 警告：session.license={license_id!r} 非 CC BY-NC-SA（如实记录）")
    _log(f"[nni_runner] license={license_id!r} load={load_seconds:.1f}s")

    # ---- 显存峰值测量：起始 reset（分配器口径，主值）----
    vram_note = ""
    try:
        if device == "cuda" and torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.synchronize()
        else:
            vram_note = "CPU 模式或 CUDA 不可用，peak_vram_mb 不可测（null，不猜数字）"
    except Exception as e:  # noqa: BLE001
        vram_note = f"peak 重置失败：{type(e).__name__}: {e}"

    # ---- 交互推理 ----
    t_inf0 = time.monotonic()
    exit_code = 0
    error = ""
    try:
        session.set_image(img_t)
        session.set_target_buffer(target_buffer)
        for p in prompts:
            if p["type"] == "point":
                session.add_point_interaction(p["xyz"], include_interaction=p["include"])
                _log(f"[nni_runner] point {p['xyz']} include={p['include']}")
            else:
                session.add_bbox_interaction(p["bbox"])
                _log(f"[nni_runner] bbox {p['bbox']}")
    except Exception as e:  # noqa: BLE001
        exit_code = 1
        error = f"{type(e).__name__}: {e}"
        _log(traceback.format_exc())
    inference_seconds = time.monotonic() - t_inf0

    wall = load_seconds + inference_seconds
    if exit_code != 0:
        return {
            "status": "error",
            "exit_code": exit_code,
            "mask_path": None,
            "wall_seconds": round(wall, 2),
            "load_seconds": round(load_seconds, 2),
            "inference_seconds": round(inference_seconds, 2),
            "peak_vram_mb": None,
            "error": error,
        }

    # ---- 落盘修正 mask（与原图 affine 一致性校验，照抄 review_save 守卫语义）----
    try:
        mask_arr = target_buffer.numpy().astype(np.uint8)
        # set_target_buffer 写进的是传入的同一 buffer（上游约定：结果直接写进
        # target buffer）；仍以防御式读回为准
        buf = getattr(session, "predicted_mask", None)
        if buf is not None and hasattr(buf, "numpy"):
            with contextlib.suppress(Exception):
                mask_arr = buf.numpy().astype(np.uint8)
        mask_img = nib.Nifti1Image(mask_arr, img_nib.affine)
        assert_affine_match(img_nib, mask_img, label=f"nni_runner {job_id}")
        output_dir.mkdir(parents=True, exist_ok=True)
        mask_path = output_dir / "correction_mask.nii.gz"
        nib.save(mask_img, str(mask_path))
    except Exception as e:  # noqa: BLE001
        _log(traceback.format_exc())
        return {
            "status": "error",
            "exit_code": 1,
            "mask_path": None,
            "wall_seconds": round(wall, 2),
            "load_seconds": round(load_seconds, 2),
            "inference_seconds": round(inference_seconds, 2),
            "peak_vram_mb": None,
            "error": f"修正 mask 落盘失败：{type(e).__name__}: {e}",
        }

    peak, peak_reason = _peak_vram_mi_b(device)
    if peak is None and not peak_reason:
        peak_reason = "peak 值不可用"

    n_target_voxels = int((mask_arr == target_label).sum()) if mask_arr.size else 0
    return {
        "status": "ok",
        "exit_code": 0,
        "mask_path": str(mask_path),
        "wall_seconds": round(wall, 2),
        "load_seconds": round(load_seconds, 2),
        "inference_seconds": round(inference_seconds, 2),
        "peak_vram_mb": round(peak, 1) if peak is not None else None,
        "error": vram_note or peak_reason,  # ok 但峰值不可用时写 reason
        "license": license_id,
        "n_target_voxels": n_target_voxels,
    }


def main() -> int:
    """stdin 读任务 JSON（单行或整段），stdout 写结果 JSON（纯 JSON）。"""
    try:
        raw = sys.stdin.read()
        task_json = json.loads(raw)
    except json.JSONDecodeError as e:
        result = _error_result(2, f"任务 JSON 解析失败：{e}")
        json.dump(result, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 2
    result = run_task(task_json)
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if result["status"] == "ok" else (result.get("exit_code") or 1)


if __name__ == "__main__":
    sys.exit(main())
