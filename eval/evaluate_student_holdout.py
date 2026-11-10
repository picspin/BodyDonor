#!/usr/bin/env python3
"""学生 hold-out 门禁评估（M4 阶段 6）：nnU-Net 学生 mask vs 独立真值逐类 Dice。

对 eval/holdout.txt 的 30 cases（ts_public 15 + amos 15）：

- 学生推理：nnUNetv2_predict -d 501 -c 3d_lowres -f 0 -tr nnUNetTrainer
  （默认 TTA，与 fold_0 validation 可比；-npp 1 -nps 1，WSL2 15.8GB 多
  worker OOM 已实测）。输入 = 30 例原图以 <case>_0000.nii.gz **symlink**
  进 input 目录；输出单标签体（值 1-15）到 student_holdout/pred/。
  每 case 持 GPU 锁（bodydonor.gpu_lock，与 ts_teacher_inference 同模式），
  已有预测文件时跳过（重跑便宜）。
- 拆分：预测体按 dataset.json 的 15 类拆成每类一个 NIfTI（文件名 = TS 类名），
  affine 与原图一致（assert_affine_match 守卫）。
- 评分：dice_one_case 学生拆分 vs GT（同一函数、同一 GT、同一类名对齐，
  AMOS_LABEL_MAP / CT-Lite 标准值复用 evaluate_holdout）；过 evaluate_gate。
- 显存：全程 SmiPoller（M2 口径）。nnUNet predict 不上报 torch 分配器峰值：
  peak_vram_mb 写 None（无 runner 口径，不编造）。
- 教师-学生一致性（阶段 5 校准输入）：逐类逐 case 体素 Dice（教师 vs 学生，
  对称，只算两边任一非空的类）。
- 门禁分口径（M4 收尾第二轮）：in-scope 门禁只评学生 15 类（Dataset501 范围），
  naive 117 类数字保留为 scope_naive 对照（超范围类天然 dice=0，是学生设计
  范围外，不是模型漏检；与 M3 教师门禁对比必须用同 15 类口径）。
- 失败 case 如实记录进 summary.failed_cases（不静默跳过），门禁按实际结果判。

用法：
    .venv/bin/python eval/evaluate_student_holdout.py [--skip-inference] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

# 复用教师评估的常量与函数（不复制粘贴逻辑）
from eval.evaluate_holdout import (  # noqa: E402
    AMOS_LABEL_MAP,
    SmiPoller,
    _standard_values,
    dice_one_case,
    prostate_spotlight,
    weakest_classes,
)
from scripts.run_nnunet_stage2b import (  # noqa: E402
    NNUNET_BIN,
    NNUNET_ENV,
)

from bodydonor.config import load_settings  # noqa: E402
from bodydonor.holdout import (  # noqa: E402
    MIN_PER_CLASS,
    check_per_class_coverage,
    evaluate_gate,
    load_holdout,
)

DATA_ROOT = Path.home() / "data/BodyDonor"
CT_LITE = DATA_ROOT / "downloads/ct_lite"
AMOS = DATA_ROOT / "downloads/amos22_subset/train"
STUDENT_DIR = DATA_ROOT / "student_holdout"
PRED_DIR = STUDENT_DIR / "pred"
INPUT_DIR = STUDENT_DIR / "input"
MODEL_VERSION_ID = "student-nnunet-v2-distill-20261109T062920Z"
CHECKPOINT = (
    DATA_ROOT / "nnUNet_results/Dataset501_BodyDonorDistill"
    / "nnUNetTrainer__nnUNetPlans__3d_lowres/fold_0/checkpoint_final.pth"
)


def _paths_for(case_id: str) -> dict[str, Path]:
    """case_id -> 原图/真值/教师输出目录路径（与 evaluate_holdout 同布局）。"""
    if case_id.startswith("amos_"):
        return {
            "image": AMOS / "imagesTr" / f"{case_id}.nii.gz",
            "gt": AMOS / "labelsTr" / f"{case_id}.nii.gz",
            "teacher_dir": AMOS.parent / case_id / "ts_out",
        }
    return {
        "image": CT_LITE / "images" / f"{case_id}.nii.gz",
        "gt": CT_LITE / "masks" / f"{case_id}.nii.gz",
        "teacher_dir": CT_LITE / "ts_out" / case_id,
    }


def student_predict(image_path: Path, case_id: str) -> Path:
    """单 case 学生推理（nnUNetv2_predict，GPU 锁全程持锁）。

    输入以 <case>_0000.nii.gz **symlink** 进 INPUT_DIR（nnUNet 按后缀
    _0000 识别通道，symlink 省拷贝）；每次调用前清空 INPUT_DIR（只留本
    case——nnUNetv2_predict -i 目录是批量预测，残留 symlink 会重复预测
    全部 case，首轮实测 s0045 被重写为证）；输出到 PRED_DIR/<case>.nii.gz。
    已有预测文件时跳过（重跑便宜）。-npp 1 -nps 1（WSL2 15.8GB 多 worker
    OOM 已实测），默认 TTA（与 validation 可比），device cuda。
    """
    from bodydonor.gpu_lock import acquire

    out_path = PRED_DIR / f"{case_id}.nii.gz"
    if out_path.exists():
        return out_path
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    PRED_DIR.mkdir(parents=True, exist_ok=True)
    for stale in INPUT_DIR.glob("*"):
        stale.unlink()
    link = INPUT_DIR / f"{case_id}_0000.nii.gz"
    # ITK（nnUNet 读图）只接受严格正交方向余弦；个别 CT-Lite 原图方向矩阵
    # 非严格正交（s0209 列间 max|cos|=1.2e-4，sitk 全 30 例实测仅此 1 例不可
    # 读，教师评估走 nibabel 不受影响）。对不可读的 case 落一个 header 正交
    # 化副本（方向矩阵 Gram-Schmidt 正交化，zooms/origin/体素网格不变，预测
    # 输出与原图体素一一对应，affine 写回原图值由 split_pred 的守卫兜住）。
    import nibabel as nib

    try:
        import SimpleITK  # noqa: F401 — nnUNet 同款读取路径可读性探测

        SimpleITK.ReadImage(str(image_path))
        link.symlink_to(image_path.resolve())
    except RuntimeError:
        fixed_path = STUDENT_DIR / "_orthofix" / f"{case_id}.nii.gz"
        fixed_path.parent.mkdir(parents=True, exist_ok=True)
        if not fixed_path.exists():
            img = nib.load(str(image_path))
            zooms = np.asarray(img.header.get_zooms()[:3], dtype=float)
            rot = img.affine[:3, :3] / zooms[None, :]
            # 极分解正交化：Q = U·Vᵀ 是 Frobenius 范数下距 rot 最近的正交阵
            # （np.linalg.qr 的符号翻转实测偏差 3.28mm，不可用）；det<0 时翻一列
            u, _, vt = np.linalg.svd(rot)
            q = u @ vt
            if np.linalg.det(q) < 0:
                q[:, 0] = -q[:, 0]
            fixed_aff = img.affine.copy()
            fixed_aff[:3, :3] = q * zooms[None, :]
            fixed = nib.Nifti1Image(
                np.asarray(img.dataobj), fixed_aff, img.header.copy()
            )
            fixed.header.set_zooms(tuple(float(z) for z in zooms))
            nib.save(fixed, str(fixed_path))
        link.symlink_to(fixed_path.resolve())
    settings = load_settings()
    lock = acquire(
        settings.resolved_data_dir / ".gpu.lock",
        reason="batch", task="student",
        job_id=f"student-holdout-{case_id}", wait=3600,
    )
    env = os.environ.copy()
    env.update(NNUNET_ENV)
    env["PATH"] = str(NNUNET_BIN) + os.pathsep + env.get("PATH", "")
    cmd = [
        # nnUNetv2_predict 可执行文件（console_script entry point）——
        # -m nnunetv2.inference.predict_from_raw_data 跑的是模块 __main__ 示例
        # （Dataset004_Hippocampus），不是 CLI（smoke 实测）。
        str(NNUNET_BIN / "nnUNetv2_predict"),
        "-i", str(INPUT_DIR), "-o", str(PRED_DIR),
        "-d", "501", "-c", "3d_lowres", "-f", "0", "-tr", "nnUNetTrainer",
        "-npp", "1", "-nps", "1", "-device", "cuda",
        "--disable_progress_bar",
    ]
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"nnUNetv2_predict exit={proc.returncode}\n"
                f"stderr tail: {proc.stderr[-2000:]}"
            )
    finally:
        lock.release()
    if not out_path.exists():
        raise RuntimeError(
            f"predict exit 0 但输出缺失：{out_path}\nstderr tail: {proc.stderr[-1000:]}"
        )
    return out_path


def split_pred(pred_path: Path, image_path: Path, case_id: str,
               dataset_labels: dict[int, str]) -> Path:
    """预测单标签体（值 1-15）拆成每类一个 NIfTI（文件名 = TS 类名）。

    affine 与原图一致（assert_affine_match 守卫，dice 前同守卫）；orthofix
    case（输入是 header 正交化副本）守卫基准与写回 affine 都用修正后
    affine（与 pred 输出一致，偏差 1.13e-4 mm > 1e-4 容差，不能用原图值）。
    返回拆分目录 STUDENT_DIR/<case>/。
    """
    import nibabel as nib

    from bodydonor.nifti_geom import assert_affine_match

    out_dir = STUDENT_DIR / case_id
    if out_dir.exists() and any(out_dir.glob("*.nii.gz")):
        return out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    img = nib.load(str(image_path))
    fixed_path = STUDENT_DIR / "_orthofix" / f"{case_id}.nii.gz"
    ref = nib.load(str(fixed_path)) if fixed_path.exists() else img
    pred_img = nib.load(str(pred_path))
    assert_affine_match(ref, pred_img, label=f"student-split {case_id}")
    pred_arr = np.asarray(pred_img.dataobj)
    for val, cls_name in sorted(dataset_labels.items()):
        nib.save(
            nib.Nifti1Image((pred_arr == val).astype(np.uint8), ref.affine),
            str(out_dir / f"{cls_name}.nii.gz"),
        )
    return out_dir


def teacher_student_consistency(
    cases: list[dict],
    entries_by_case: dict[str, str],
) -> dict[str, object]:
    """教师-学生一致性（阶段 5 校准输入）：同一 case 上逐类体素 Dice。

    直接用拆分 mask（教师 <case>/ts_out 每类 NIfTI vs 学生 <case>/ 每类
    NIfTI，对称，2*|t∩s|/(|t|+|s|)）；只算两边任一非空的类。返回总体分布
    （均值/中位数/p10/p25/最小值）+ 逐类均值与 case 数 + 低于 0.80 的类清单。
    """
    import nibabel as nib
    from eval.evaluate_holdout import AMOS as TEACHER_AMOS
    from eval.evaluate_holdout import CT_LITE as TEACHER_CT_LITE

    from bodydonor.nifti_geom import assert_affine_match

    scores: list[float] = []
    per_class_sum: dict[str, float] = {}
    per_class_n: dict[str, int] = {}
    for c in cases:
        case_id = c["case_id"]
        is_amos = case_id.startswith("amos_")
        teacher_dir = (
            (TEACHER_AMOS.parent / case_id / "ts_out")
            if is_amos else (TEACHER_CT_LITE / "ts_out" / case_id)
        )
        student_dir = STUDENT_DIR / case_id
        image_path = _paths_for(case_id)["image"]
        img = nib.load(str(image_path))
        for stu_f in sorted(student_dir.glob("*.nii.gz")):
            cls_name = stu_f.name.removesuffix(".nii.gz")
            t_f = teacher_dir / f"{cls_name}.nii.gz"
            if not t_f.exists():
                continue  # 教师无该类输出（TS total 类名集合与 Dataset501 差异）
            t = np.asarray(nib.load(str(t_f)).dataobj) > 0
            s = np.asarray(nib.load(str(stu_f)).dataobj) > 0
            if not t.any() and not s.any():
                continue  # 空对空不算
            aff = t_f if is_amos else image_path
            aff_img = nib.load(str(aff)) if not is_amos else img
            assert_affine_match(
                aff_img, nib.load(str(t_f)),
                label=f"consistency teacher {case_id} {cls_name}",
            )
            assert_affine_match(
                img, nib.load(str(stu_f)),
                label=f"consistency student {case_id} {cls_name}",
            )
            denom = float(t.sum()) + float(s.sum())
            if denom == 0:
                continue
            d = 2.0 * float((t & s).sum()) / denom
            scores.append(d)
            per_class_sum[cls_name] = per_class_sum.get(cls_name, 0.0) + d
            per_class_n[cls_name] = per_class_n.get(cls_name, 0) + 1

    if scores:
        arr = np.array(scores)
        overall = {
            "mean": round(float(arr.mean()), 4),
            "median": round(float(np.median(arr)), 4),
            "p10": round(float(np.percentile(arr, 10)), 4),
            "p25": round(float(np.percentile(arr, 25)), 4),
            "min": round(float(arr.min()), 4),
            "n_pairs": len(scores),
        }
    else:
        overall = {"n_pairs": 0}
    per_class = {
        k: {
            "mean": round(per_class_sum[k] / per_class_n[k], 4),
            "cases": per_class_n[k],
        }
        for k in sorted(per_class_sum)
    }
    below_080 = sorted(k for k, v in per_class.items() if v["mean"] < 0.80)
    return {
        "overall": overall,
        "per_class": per_class,
        "below_080_classes": below_080,
        "note": (
            "教师 mask vs 学生 mask 逐类体素 Dice（对称，只算两边任一非空的类）；"
            "阶段 5 多教师一致性规则的校准输入（聚合方式校准依据：case 级均值 "
            "vs 最差类判别力对比），与 distill_data 温度缩放无关。"
        ),
    }


def in_scope_gate_inputs(
    dice_sum: dict[str, float],
    case_count: dict[str, int],
    whole_missed: list[str],
    student_classes: set[str],
) -> tuple[dict[str, float], dict[str, int], list[str]]:
    """口径切分（M4 收尾第二轮）：把 naive 累计过滤到学生类范围内的三元组。

    学生只训了 Dataset501 的 15 类；GT 里的其余类（教师 117 类范围）学生
    天然整类 dice=0，是口径错误不是模型失败——完美 15 类学生在 naive 口径
    下也永远 FAIL。返回过滤后的 (dice_sum, case_count, whole_missed)，直接
    进 evaluate_gate（同判据：mean ≥ 0.75、每类 ≥ 0.5 仅 ≥10 cases、整类漏检）。
    naive 三元组原样保留（调用方另算 scope_naive 对照）。
    """
    ds = {k: v for k, v in dice_sum.items() if k in student_classes}
    cc = {k: v for k, v in case_count.items() if k in student_classes}
    wm = [c for c in whole_missed if c in student_classes]
    return ds, cc, wm


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-inference", action="store_true",
                    help="只重算 Dice/一致性（用已有学生预测）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="eval/student_holdout_results.json")
    args = ap.parse_args()

    entries = load_holdout()
    if args.limit:
        entries = entries[: args.limit]
    if not entries:
        print("holdout.txt 为空")
        return 1
    if not CHECKPOINT.exists():
        print(f"学生 checkpoint 缺失：{CHECKPOINT}")
        return 1

    import nibabel as nib

    # dataset.json 的 15 类（值 -> 类名；拆分与类名对齐的单一事实来源）
    dataset_json = json.loads(
        (DATA_ROOT / "nnUNet_raw/Dataset501_BodyDonorDistill/dataset.json")
        .read_text(encoding="utf-8")
    )
    dataset_labels = {
        int(v): k for k, v in dataset_json["labels"].items() if k != "background"
    }

    std_values = _standard_values()  # name -> value（CT-Lite 真值编码）
    amos_name_to_value = {name: val for val, name in AMOS_LABEL_MAP.items()}

    results: list[dict] = []
    failed_cases: list[dict] = []
    dice_sum_by_class: dict[str, float] = {}
    case_count_by_class: dict[str, int] = {}
    zero_by_class: dict[str, int] = {}
    gt_names_per_case: dict[str, set[str]] = {}
    zero_dice_cases: dict[str, list[str]] = {}
    n_fresh_inference = 0
    per_case_elapsed: dict[str, float] = {}
    t0 = time.monotonic()

    poller = SmiPoller()
    poller.start()
    for e in entries:
        case_id = e.case_id
        p = _paths_for(case_id)
        image_path, gt_path = p["image"], p["gt"]
        if not image_path.exists() or not gt_path.exists():
            failed_cases.append({"case_id": case_id, "reason": "image/GT 缺失"})
            print(f"FAIL {case_id}（image/真值缺失）")
            continue
        try:
            pred_path = PRED_DIR / f"{case_id}.nii.gz"
            if pred_path.exists():
                print(f"[student-holdout] {case_id}: 已有预测（跳过推理）")
            else:
                if args.skip_inference:
                    failed_cases.append({
                        "case_id": case_id,
                        "reason": "预测缺失（--skip-inference）",
                    })
                    print(f"SKIP {case_id}（预测缺失）")
                    continue
                print(f"[student-holdout] {case_id}: 学生推理（GPU 锁）...")
                tc0 = time.monotonic()
                pred_path = student_predict(image_path, case_id)
                per_case_elapsed[case_id] = round(time.monotonic() - tc0, 1)
                n_fresh_inference += 1
                print(f"  推理 {per_case_elapsed[case_id]}s")
            split_dir = split_pred(pred_path, image_path, case_id, dataset_labels)
        except Exception as exc:  # noqa: BLE001 — 失败 case 如实记录，不静默
            failed_cases.append({
                "case_id": case_id, "reason": str(exc)[:500],
            })
            print(f"FAIL {case_id}: {str(exc)[:200]}")
            continue

        gt_arr = np.asarray(nib.load(str(gt_path)).dataobj)
        if case_id.startswith("amos_"):
            name_to_value = amos_name_to_value
            value_to_name = dict(AMOS_LABEL_MAP)
        else:
            name_to_value = std_values
            value_to_name = {v: k for k, v in std_values.items()}
        gt_classes = {
            value_to_name[int(v)] for v in np.unique(gt_arr)
            if v != 0 and int(v) in value_to_name
        }
        gt_names_per_case[case_id] = set(gt_classes)

        mean, per_class = dice_one_case(split_dir, gt_arr, image_path, name_to_value)
        for cls in gt_classes:
            dice_sum_by_class[cls] = dice_sum_by_class.get(cls, 0.0) + per_class.get(cls, 0.0)
            case_count_by_class[cls] = case_count_by_class.get(cls, 0) + 1
            if per_class.get(cls, 0.0) == 0.0:
                zero_by_class[cls] = zero_by_class.get(cls, 0) + 1
                zero_dice_cases.setdefault(cls, []).append(case_id)

        results.append({
            "case_id": case_id,
            "source": e.source,
            "mean_dice": round(mean, 4),
            "n_classes_scored": len(per_class),
            "per_class_dice": {k: round(v, 4) for k, v in sorted(per_class.items())},
        })
        print(f"  mean_dice={mean:.4f}（{len(per_class)} 类）")

    poller.stop()
    elapsed = time.monotonic() - t0
    n = len(results)
    whole_missed = sorted(
        cls for cls, cnt in zero_by_class.items()
        if cnt > 0 and cnt == case_count_by_class.get(cls, 0)
    )
    # 分口径门禁（M4 收尾第二轮）：in-scope 门禁只评学生 15 类（Dataset501
    # 范围）；naive 117 类口径结构性 FAIL——102 个超范围类天然 dice=0（学生
    # 设计范围外，不是模型漏检），完美 15 类学生也永远过不了，不是质量信号。
    student_classes = set(dataset_labels.values())
    in_dice, in_cc, in_wm = in_scope_gate_inputs(
        dice_sum_by_class, case_count_by_class, whole_missed, student_classes
    )
    gate = evaluate_gate(
        in_dice, in_cc, n_cases=n, whole_class_missed=in_wm,
    )
    scope_naive = evaluate_gate(
        dice_sum_by_class, case_count_by_class, n_cases=n,
        whole_class_missed=whole_missed,
    )
    coverage_problems = check_per_class_coverage(gt_names_per_case)

    tsc = teacher_student_consistency(results, {})
    summary = {
        "n_cases": n,
        "gate_scope": "student-15-classes",
        "gate_note": (
            "门禁口径 = 学生 15 类（Dataset501 训练范围）in-scope：GT 超范围类"
            "（教师 117 类范围）学生天然整类 dice=0，是学生设计范围外，不是模型"
            "漏检；naive 117 类口径结构性 FAIL（完美 15 类学生也永远过不了），"
            "保留为 scope_naive 对照。与 M3 教师门禁对比必须用同 15 类口径"
            "（教师 0.8548 vs 学生均值），不是教师 117 类 0.9335 vs 学生 0.1027。"
        ),
        "gate_passed": gate.passed,
        "failures": gate.failures,
        "mean_dice": round(gate.mean_dice, 4),
        "per_class_dice": {k: round(v, 4) for k, v in sorted(gate.per_class_dice.items())},
        "per_class_cases": dict(gate.per_class_cases),
        "whole_class_missed": gate.whole_class_missed,
        "low_frequency_classes": gate.low_frequency_classes,
        "scope_naive": {
            "scope": "all-117-classes",
            "passed": scope_naive.passed,
            "failures": scope_naive.failures,
            "mean_dice": round(scope_naive.mean_dice, 4),
            "n_classes": len(scope_naive.per_class_dice),
            "n_per_class_failures": sum(
                1 for f in scope_naive.failures if f.startswith("每类 Dice 下限")
            ),
            "note": (
                "naive 对照口径：学生当 117 类模型评，102 个超范围类天然 "
                "dice=0 拉垮均值（M4 收尾第二轮前 DB 里的 gate_status=failed/"
                "mean 0.1027 即此口径，属口径错误非模型失败，本次纠正）。"
            ),
        },
        "coverage_problems": coverage_problems,
        "coverage_warning": gate.coverage_warning,
        "zero_dice_cases_by_class": {
            k: sorted(v) for k, v in sorted(zero_dice_cases.items())
        },
        "weakest_classes": weakest_classes(gate.per_class_dice, gate.per_class_cases),
        "prostate": prostate_spotlight(
            gate.per_class_dice, gate.per_class_cases, zero_dice_cases, results
        ),
        "teacher_student_consistency": {
            "overall": tsc["overall"],
            "below_080_classes": tsc["below_080_classes"],
        },
        "failed_cases": failed_cases,
        "per_case_elapsed_s": dict(sorted(per_case_elapsed.items())),
        "model": {
            "model_version_id": MODEL_VERSION_ID,
            "checkpoint": str(CHECKPOINT),
            "configuration": "3d_lowres",
            "fold": 0,
            "epochs": 1000,
            "trainer": "nnUNetTrainer",
            "tta": True,
        },
        "vram": {
            "peak_vram_mb": None,
            "peak_vram_nvidia_smi_mb": poller.peak_delta_mb(),
            "n_cases_with_fresh_inference": n_fresh_inference,
            "note": (
                "peak_vram_mb=None：nnUNetv2_predict 为子进程，torch 分配器峰值"
                "不可从本进程读（无 runner 口径），不编造；交叉核对值 "
                "peak_vram_nvidia_smi_mb = 全程 nvidia-smi 轮询峰值 − 推理前基线"
                "（M2 口径，子进程显存计入全局值）。"
            ),
        },
        "elapsed_s": round(elapsed, 1),
    }
    out_path = PROJECT_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"summary": summary, "gate": gate.to_dict(), "cases": results,
                    "teacher_student_consistency": tsc},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    # 教师-学生一致性完整分布写独立文件（阶段 5 校准输入）
    tsc_path = PROJECT_ROOT / "eval/student_teacher_consistency.json"
    tsc_path.write_text(
        json.dumps(tsc, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(
        f"[student-holdout] 低频类 {len(gate.low_frequency_classes)} 个"
        f"（< {MIN_PER_CLASS} cases），其 Dice 不受每类下限约束——见 "
        "summary.low_frequency_classes"
    )
    if failed_cases:
        print(f"[student-holdout] 失败 case {len(failed_cases)} 例："
              f"{[c['case_id'] for c in failed_cases]}")
    if tsc["overall"].get("n_pairs"):
        ov = tsc["overall"]
        print(f"[student-holdout] 教师-学生一致性：mean={ov['mean']} "
              f"median={ov['median']} p10={ov['p10']} min={ov['min']} "
              f"（{ov['n_pairs']} 对；<0.80 类 {len(tsc['below_080_classes'])} 个）")
    print(f"[student-holdout] results -> {out_path}")
    print(f"[student-holdout] consistency -> {tsc_path}")
    return 0 if gate.passed else 1  # exit 码以 in-scope 门禁为准（scope_naive 只对照）


if __name__ == "__main__":
    sys.exit(main())
