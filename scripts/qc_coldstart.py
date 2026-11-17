#!/usr/bin/env python3
"""QC 冷启动校准（M3，proposal §7）：TS 公开集上跑 QC 引擎，拿「QC 分数 vs
与真值 Dice」的相关性定 separating threshold。

方法：
1. 对每个 case 的 teacher mask（TS 对原图推理的输出）跑 run_qc 评分；
2. 对同一 teacher mask 与公开集真值 mask 算逐类 Dice（真实体素计算）；
3. 每个 case 出 (QC 总分, 与真值 mean Dice) 一对，画分布/相关性；
4. separating threshold：取「低质量组（Dice < 阈值）与高质量组的 QC 分数分布」
   的分位边界，写回 qc_thresholds.yaml（source=cold_start + n_cases）。

用法：
    uv run python scripts/qc_coldstart.py            # 用下载的 25 例
    uv run python scripts/qc_coldstart.ts --out ...  # 结果 JSON + 更新 yaml
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from bodydonor.config import load_settings
from bodydonor.qc import run_qc
from bodydonor.segmentation import resolve_modality


def ts_teacher_mask(
    image_path: Path, out_dir: Path, task: str = "total", *, modality: str
) -> tuple[np.ndarray | None, list[str], float]:
    """对原图跑 TS teacher 推理，合并为多类标签图（真卡，GPU 锁全程持锁）。"""
    from sqlalchemy import text  # noqa: F401

    from bodydonor.segmentation import merge_ts_output

    out_dir.mkdir(parents=True, exist_ok=True)
    if not any(out_dir.glob("*.nii.gz")):
        # 推理（GPU 锁 + JSON 契约 runner，M2-align 3.2/3.3）
        from bodydonor.gpu_lock import acquire
        from bodydonor.segmentation import run_total

        s = load_settings()
        lock_path = s.resolved_data_dir / ".gpu.lock"
        job_id = f"coldstart-{image_path.stem[:16]}"
        lock = acquire(lock_path, reason="batch", task=task, job_id=job_id, wait=3600)
        try:
            run_total(
                image_path, out_dir, task=task, device="gpu", settings=s,
                job_id=job_id, modality=modality,
            )
        finally:
            lock.release()
    merged, class_names = merge_ts_output(out_dir, image_path)
    arr = np.asarray(merged.dataobj)
    return arr, class_names, 0.0


def dice_vs_gt_by_name(
    teacher_out: Path, gt_arr: np.ndarray, image_path: Path
) -> tuple[float, dict[str, float], dict[str, int]]:
    """按类名对齐的 teacher vs 真值逐类 Dice（真实体素计算）。

    - teacher 每类一个 NIfTI（liver.nii.gz），文件名 -> TS 标准 class_map 值
    - 真值 mask 用 TS 标准值；逐类 (teacher binary, gt binary) Dice
    - affine 一致性守卫（与 merge 同模式：不一致 raise，不静默重采样）
    """
    import nibabel as nib

    from bodydonor.nifti_geom import assert_affine_match

    img = nib.load(str(image_path))
    per_class: dict[str, float] = {}
    n_by_class: dict[str, int] = {}
    dice_sum = 0.0
    n_classes = 0
    for f in sorted(teacher_out.glob("*.nii.gz")):
        name = f.name.removesuffix(".nii.gz")
        mask_img = nib.load(str(f))
        assert_affine_match(img, mask_img, label=f"dice_vs_gt {name}")
        t = np.asarray(mask_img.dataobj) > 0
        val = _standard_value(name)  # 标准 TS class_map 值（liver=5 等）
        if val is None:
            continue
        g = gt_arr == val
        denom = float(t.sum()) + float(g.sum())
        if denom == 0:
            continue
        d = 2.0 * float((t & g).sum()) / denom
        per_class[name] = d
        n_by_class[name] = n_by_class.get(name, 0) + 1
        dice_sum += d
        n_classes += 1
    mean = dice_sum / n_classes if n_classes else 0.0
    return mean, per_class, n_by_class


_STD_VALUES: dict[str, int] | None = None


def _standard_value(totalseg_class: str) -> int | None:
    """TS 标准 class_map 值（name -> value；缓存到 ts_class_map.json）。"""
    global _STD_VALUES
    if _STD_VALUES is None:
        cache = Path.home() / "data/BodyDonor/downloads/ct_lite/ts_class_map.json"
        if cache.exists():
            _STD_VALUES = json.loads(cache.read_text(encoding="utf-8"))
        else:
            # TS venv 里拿（subprocess，主 venv 隔离不变）
            import subprocess

            venv_py = Path.home() / "work/BodyDonor/.venv-totalseg/bin/python"
            out = subprocess.run(
                [str(venv_py), "-c",
                 "import json; from totalsegmentator.map_to_binary import class_map;"
                 "print(json.dumps(class_map['total']))"],
                capture_output=True, text=True, timeout=60,
            )
            _STD_VALUES = json.loads(out.stdout)
        # class_map 是 value->name；缓存反转为 name->value（name 查值用）
        if _STD_VALUES and all(isinstance(k, int) or k.isdigit() for k in _STD_VALUES):
            _STD_VALUES = {v: int(k) for k, v in _STD_VALUES.items()}
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(_STD_VALUES))
    return _STD_VALUES.get(totalseg_class)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(Path.home() / "data/BodyDonor/downloads/ct_lite"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="eval/coldstart_results.json")
    ap.add_argument("--skip-inference", action="store_true", help="只重算相关性（用已有 TS 输出）")
    args = ap.parse_args()

    data = Path(args.data_dir)
    images = sorted((data / "images").glob("*.nii.gz"))
    if args.limit:
        images = images[: args.limit]
    if not images:
        print(f"no cases in {data}")
        return 1

    import nibabel as nib

    results = []
    t0 = time.monotonic()
    for img_path in images:
        case_id = img_path.stem.removesuffix(".nii")
        gt_path = data / "masks" / f"{case_id}.nii.gz"
        if not gt_path.exists():
            print(f"SKIP {case_id}（无真值）")
            continue
        teacher_out = data / "ts_out" / case_id
        print(f"[coldstart] {case_id}: teacher inference + qc ...")
        teacher_arr, class_names, _ = ts_teacher_mask(img_path, teacher_out,
                                                      modality=resolve_modality("ct_lite"))
        if teacher_arr is None:
            print(f"SKIP {case_id}（teacher 输出缺失）")
            continue
        img = nib.load(str(img_path))
        img_hu = np.asarray(img.dataobj)
        # label 索引 -> totalseg_class 名（merge_ts_output 的字母序索引）
        idx_to_class = {i + 1: name for i, name in enumerate(class_names)}
        qc = run_qc(
            teacher_arr,
            tuple(int(v) for v in teacher_arr.shape),
            mask_version_id=f"coldstart-{case_id}",
            series_id=case_id,
            hu_values=img_hu,
            totalseg_classes=idx_to_class,
            modality=resolve_modality("ct_lite"),  # S3 模态门闩：HU 规则只 CT
        )
        # teacher mask vs 公开集真值：逐类 Dice（真实体素计算）。
        # CT-Lite 真值 mask 用 **TS 标准 class_map 值**（liver=5 等），与
        # merge_ts_output 的字母序索引不同——按类名对齐：teacher 每类 NIfTI
        # 文件名 -> 标准 class_map 值，与真值逐类比对（不重采样）。
        gt_img = nib.load(str(gt_path))
        gt_arr = np.asarray(gt_img.dataobj)
        mean_dice, per_class, _n = dice_vs_gt_by_name(
            teacher_out, gt_arr, img_path
        )
        results.append({
            "case_id": case_id,
            "qc_score": round(float(qc.overall_score), 4),
            "qc_passed": bool(qc.passed),
            "qc_flags": list(qc.flags),
            "mean_dice_vs_gt": round(float(mean_dice), 4),
            "per_class_dice": {k: round(v, 4) for k, v in per_class.items()},
        })
        print(f"  qc={qc.overall_score:.3f} dice={mean_dice:.4f} flags={qc.flags}")

    elapsed = time.monotonic() - t0
    # 相关性（Pearson；n<3 时 None）
    qs = np.array([r["qc_score"] for r in results])
    ds = np.array([r["mean_dice_vs_gt"] for r in results])
    corr = float(np.corrcoef(qs, ds)[0, 1]) if len(results) >= 3 else None
    # separating threshold：Dice 达标组（>= 0.75）与未达标组的 QC 分数分布
    good = qs[ds >= 0.75]
    bad = qs[ds < 0.75]
    sep = {
        "n_cases": len(results),
        "n_good_dice": int(len(good)),
        "n_bad_dice": int(len(bad)),
        "pearson_qc_vs_dice": corr,
        "good_qc_min": float(good.min()) if len(good) else None,
        "good_qc_p10": float(np.percentile(good, 10)) if len(good) else None,
        "bad_qc_max": float(bad.max()) if len(bad) else None,
        "bad_qc_p90": float(np.percentile(bad, 90)) if len(bad) else None,
        "elapsed_s": round(elapsed, 1),
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"summary": sep, "cases": results}, ensure_ascii=False, indent=1)
    )
    print(json.dumps(sep, ensure_ascii=False, indent=1))
    print(f"[coldstart] results -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
