#!/usr/bin/env python3
"""hold-out 门禁评估（M3，proposal §8）：教师 mask vs 独立真值逐类 Dice + 门禁判定。

对 eval/holdout.txt 的 30 cases（ts_public 15 + amos 15）：

- ts_public（CT-Lite）：真值在 masks/{case}.nii.gz（TS 标准 class_map 值编码）；
  teacher mask 已有（ts_out/{case}/，117 类每类一个 NIfTI，文件名 = TS 类名）。
- amos（AMOS22）：真值在 amos22_subset/train/labelsTr/{case}.nii.gz（值 1-15，
  AMOS 官方 15 类）；teacher mask 跑 TS total（GPU 锁全程持锁，已有则跳过）。

每 case：(teacher 逐类, 真值逐类) Dice（真实体素计算，不 mock；affine 一致性
守卫与 merge 同模式：不一致 raise，不静默重采样）。按 **类名** 对齐（TS 类名
-> AMOS 值 1-15 / CT-Lite 标准值），逐类 Dice 累计后过 evaluate_gate
（mean ≥ 0.75 + 每类 ≥ 0.5 + 整类漏检，M2-align §1.3 成对判据）。

affine 守卫注意：AMOS 真值与原图同源同几何（labelsTr 与 imagesTr 逐体素对齐）；
TS teacher 输出与原图 affine 一致（merge_ts_output 已守卫）。

用法：
    uv run python eval/evaluate_holdout.py [--skip-inference] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import numpy as np  # noqa: E402

from bodydonor.config import load_settings  # noqa: E402
from bodydonor.holdout import (  # noqa: E402
    MIN_PER_CLASS,
    check_per_class_coverage,
    evaluate_gate,
    load_holdout,
)
from bodydonor.segmentation import resolve_modality  # noqa: E402

DATA_ROOT = Path.home() / "data/BodyDonor/downloads"
CT_LITE = DATA_ROOT / "ct_lite"
AMOS = DATA_ROOT / "amos22_subset/train"

# AMOS22 官方 15 类（labelsTr 值 -> TS 类名；liver/spleen/pancreas 实测对齐验证过）
AMOS_LABEL_MAP: dict[int, str] = {
    1: "spleen", 2: "kidney_right", 3: "kidney_left", 4: "gallbladder",
    5: "esophagus", 6: "liver", 7: "stomach", 8: "aorta", 9: "inferior_vena_cava",
    10: "pancreas", 11: "adrenal_gland_right", 12: "adrenal_gland_left",
    13: "duodenum", 14: "urinary_bladder",
    # AMOS 15 = prostate（男）/uterus（女）；TS total 输出文件名是 prostate.nii.gz
    #（amos_0001 实测：teacher prostate.nii.gz vs GT label 15 dice=0.69，映射成立）
    15: "prostate",
}


def _standard_values() -> dict[str, int]:
    """TS 标准 class_map 值（name -> value；缓存文件已有，ct_lite 真值编码）。"""
    cache = CT_LITE / "ts_class_map.json"
    return json.loads(cache.read_text(encoding="utf-8"))


def ts_teacher_inference(
    image_path: Path, out_dir: Path, *, modality: str
) -> tuple[Path, float | None]:
    """对原图跑 TS total（GPU 锁全程持锁，JSON 契约 runner，M2-align 3.2/3.3）。

    返回 (out_dir, runner torch 分配器 peak_vram_mb)——M2 主值口径；peak 不可用
    （CPU 回退 / 无 CUDA）时为 None（不猜数字）。
    """
    from bodydonor.gpu_lock import acquire
    from bodydonor.segmentation import run_total

    settings = load_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    job_id = f"holdout-{image_path.name[:20]}"
    lock = acquire(
        settings.resolved_data_dir / ".gpu.lock",
        reason="batch", task="total", job_id=job_id, wait=3600,
    )
    try:
        runner = run_total(image_path, out_dir, task="total", device="gpu",
                           settings=settings, job_id=job_id,
                           modality=modality)
    finally:
        lock.release()
    return out_dir, runner.peak_vram_mb


class SmiPoller:
    """nvidia-smi 全局显存轮询（M2 口径复用，segmentation.py segment_series 同模式）。

    - 推理前基线 + 后台线程 1s 采样；交叉核对值 = 轮询峰值 − 基线（全局口径）。
    - 主值仍是 runner 内 torch.cuda.max_memory_allocated()（per-case peak_vram_mb）。
    - nvidia-smi 不可用 / 无样本时峰值为 None（不猜数字）。
    - sampler 参数仅供单测注入（mock 只隔离采样代码路径）；生产默认
      bodydonor.segmentation.gpu_peak_memory_mi_b。
    """

    def __init__(self, interval_s: float = 1.0, sampler=None) -> None:
        self._interval_s = interval_s
        self._sampler = sampler
        self._stop = threading.Event()
        self._samples: list[float] = []
        self.baseline: float | None = None
        self._thread: threading.Thread | None = None

    def _sample(self) -> float | None:
        if self._sampler is not None:
            return self._sampler()
        from bodydonor.segmentation import gpu_peak_memory_mi_b

        return gpu_peak_memory_mi_b()

    def start(self) -> SmiPoller:
        self.baseline = self._sample()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        return self

    def _poll(self) -> None:
        while not self._stop.wait(self._interval_s):
            v = self._sample()
            if v is not None:
                self._samples.append(v)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> SmiPoller:
        return self.start()

    def __exit__(self, *exc) -> bool:
        self.stop()
        return False

    def peak_delta_mb(self) -> float | None:
        """轮询峰值 − 基线；无样本时 None（不猜数字）。"""
        if not self._samples:
            return None
        peak = max(self._samples) - (self.baseline or 0.0)
        return peak if peak > 0 else None


def weakest_classes(
    per_class_dice: dict[str, float],
    per_class_cases: dict[str, int],
    k: int = 5,
) -> list[dict[str, object]]:
    """Dice 最低的 k 类：[{class, dice, cases}]，按 Dice 升序。"""
    ranked = sorted(per_class_dice.items(), key=lambda kv: kv[1])
    return [
        {"class": name, "dice": round(d, 4), "cases": per_class_cases.get(name, 0)}
        for name, d in ranked[:k]
    ]


def prostate_spotlight(
    per_class_dice: dict[str, float],
    per_class_cases: dict[str, int],
    zero_dice_cases_by_class: dict[str, list[str]],
    cases: list[dict],
) -> dict[str, object] | None:
    """prostate 单项聚光（M3 最弱类 0.5914）：总 Dice + case 数 + dice=0 清单 + 分源均值。

    无 prostate 计分时返回 None。AMOS 侧 dice=0 的 case 是教师整类漏检（非映射
    错误，M3-REPORT §3.5；AMOS 15=prostate 男/uterus 女，女性 case 的 label 15
    是 uterus，同一映射会对不上——漏检与性别映射两个因素并存）。
    """
    if "prostate" not in per_class_dice:
        return None
    amos = [
        c["per_class_dice"]["prostate"]
        for c in cases
        if c["source"] == "amos" and "prostate" in c["per_class_dice"]
    ]
    ts = [
        c["per_class_dice"]["prostate"]
        for c in cases
        if c["source"] != "amos" and "prostate" in c["per_class_dice"]
    ]
    return {
        "dice": round(per_class_dice["prostate"], 4),
        "cases": per_class_cases.get("prostate", 0),
        "zero_dice_cases": sorted(zero_dice_cases_by_class.get("prostate", [])),
        "amos_mean_dice": round(sum(amos) / len(amos), 4) if amos else None,
        "amos_cases": len(amos),
        "ts_public_mean_dice": round(sum(ts) / len(ts), 4) if ts else None,
        "ts_public_cases": len(ts),
        "note": (
            "M3 最弱类；AMOS 侧 dice=0 的 case 为教师整类漏检（非映射错误）；"
            "AMOS 15=prostate（男）/uterus（女），女性 case 的 label 15 是 uterus，"
            "漏检与性别映射两个因素并存（M3-REPORT §3.5）。"
        ),
    }


def dice_one_case(
    teacher_out: Path, gt_arr: np.ndarray, image_path: Path,
    name_to_value: dict[str, int],
) -> tuple[float, dict[str, float]]:
    """teacher 每类 NIfTI vs 真值逐类 Dice（按类名对齐，真实体素计算）。

    - teacher 文件名（liver.nii.gz）-> TS 类名 -> 真值编码值
    - AMOS 真值不在 teacher 名单的类（如 femur）不进本 case 的 per_class
    - affine 一致性守卫（不一致 raise，不静默重采样）
    """
    import nibabel as nib

    from bodydonor.nifti_geom import assert_affine_match

    img = nib.load(str(image_path))
    per_class: dict[str, float] = {}
    for f in sorted(teacher_out.glob("*.nii.gz")):
        cls_name = f.name.removesuffix(".nii.gz")
        val = name_to_value.get(cls_name)
        if val is None:
            continue  # 真值编码不含该类（AMOS 只有 15 类）
        mask_img = nib.load(str(f))
        assert_affine_match(img, mask_img, label=f"holdout-dice {cls_name}")
        t = np.asarray(mask_img.dataobj) > 0
        g = gt_arr == val
        denom = float(t.sum()) + float(g.sum())
        if denom == 0:
            continue  # 空对空
        per_class[cls_name] = 2.0 * float((t & g).sum()) / denom
    mean = sum(per_class.values()) / len(per_class) if per_class else 0.0
    return mean, per_class


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-inference", action="store_true",
                    help="只重算 Dice（用已有 TS 输出）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="eval/holdout_results.json")
    args = ap.parse_args()

    entries = load_holdout()
    if args.limit:
        entries = entries[: args.limit]
    if not entries:
        print("holdout.txt 为空（先跑 eval/build_holdout.py 固化）")
        return 1

    std_values = _standard_values()  # name -> value（CT-Lite 真值编码）
    # 真值编码是 **值**（GT 里的 int），类名对齐要 值 -> 名 反查表
    std_value_to_name = {v: k for k, v in std_values.items()}
    # AMOS：值 -> 类名（AMOS_LABEL_MAP 本身就是 值 -> 名）
    amos_name_to_value = {name: val for val, name in AMOS_LABEL_MAP.items()}
    amos_value_to_name = dict(AMOS_LABEL_MAP)

    results = []
    dice_sum_by_class: dict[str, float] = {}
    case_count_by_class: dict[str, int] = {}
    zero_by_class: dict[str, int] = {}
    gt_names_per_case: dict[str, set[str]] = {}
    zero_dice_cases: dict[str, list[str]] = {}
    peak_vram_by_case: dict[str, float] = {}
    n_fresh_inference = 0
    t0 = time.monotonic()

    import nibabel as nib

    # 显存采样（M2 口径）：全程 nvidia-smi 轮询 + per-case runner 分配器峰值。
    poller = SmiPoller()
    poller.start()
    for e in entries:
        case_id = e.case_id
        if case_id.startswith("amos_"):
            image_path = AMOS / "imagesTr" / f"{case_id}.nii.gz"
            gt_path = AMOS / "labelsTr" / f"{case_id}.nii.gz"
            teacher_dir = AMOS.parent / case_id / "ts_out"
            name_to_value = amos_name_to_value
            value_to_name = amos_value_to_name
        else:
            image_path = CT_LITE / "images" / f"{case_id}.nii.gz"
            gt_path = CT_LITE / "masks" / f"{case_id}.nii.gz"
            teacher_dir = CT_LITE / "ts_out" / case_id
            name_to_value = std_values
            value_to_name = std_value_to_name
        if not image_path.exists() or not gt_path.exists():
            print(f"SKIP {case_id}（image/真值缺失）")
            continue

        if not any(teacher_dir.glob("*.nii.gz")):
            if args.skip_inference:
                print(f"SKIP {case_id}（teacher 输出缺失，--skip-inference）")
                continue
            print(f"[holdout] {case_id}: TS teacher 推理（GPU 锁）...")
            _, peak_vram = ts_teacher_inference(
                image_path, teacher_dir, modality=resolve_modality(e)
            )
            n_fresh_inference += 1
            if peak_vram is not None:
                peak_vram_by_case[case_id] = peak_vram
        else:
            print(f"[holdout] {case_id}: 已有 teacher 输出")

        gt_arr = np.asarray(nib.load(str(gt_path)).dataobj)
        # 真值出现过的类（编码值 -> 类名反查；真值编码不含的类不进本 case）
        gt_classes = {
            value_to_name[int(v)] for v in np.unique(gt_arr)
            if v != 0 and int(v) in value_to_name
        }
        gt_names_per_case[case_id] = set(gt_classes)

        mean, per_class = dice_one_case(teacher_dir, gt_arr, image_path, name_to_value)
        # 逐 case 记录：dice=0 的类进 zero_by_class（累计 case 数）；
        # 「整类漏检」= 类在 hold-out 存在但**所有** case dice=0，跑完 30 例后判定
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
    # 整类漏检（§8）：类在 hold-out 存在但所有 case dice=0
    whole_missed = sorted(
        cls for cls, cnt in zero_by_class.items()
        if cnt > 0 and cnt == case_count_by_class.get(cls, 0)
    )
    gate = evaluate_gate(dice_sum_by_class, case_count_by_class, n_cases=n,
                         whole_class_missed=whole_missed)
    # 覆盖检查（M4 阶段 4：check_per_class_coverage 真正接进评估路径；默认只
    # 显式告警 + 列清单，不改 pass/fail——理由见 holdout.evaluate_gate）。
    coverage_problems = check_per_class_coverage(gt_names_per_case)

    summary = {
        "n_cases": n,
        "gate_passed": gate.passed,
        "failures": gate.failures,
        "mean_dice": round(gate.mean_dice, 4),
        "per_class_dice": {k: round(v, 4) for k, v in sorted(gate.per_class_dice.items())},
        "per_class_cases": dict(gate.per_class_cases),
        "whole_class_missed": gate.whole_class_missed,
        "low_frequency_classes": gate.low_frequency_classes,
        "coverage_problems": coverage_problems,
        "coverage_warning": gate.coverage_warning,
        "zero_dice_cases_by_class": {
            k: sorted(v) for k, v in sorted(zero_dice_cases.items())
        },
        "weakest_classes": weakest_classes(gate.per_class_dice, gate.per_class_cases),
        "prostate": prostate_spotlight(
            gate.per_class_dice, gate.per_class_cases, zero_dice_cases, results
        ),
        "vram": {
            "peak_vram_mb": (
                max(peak_vram_by_case.values()) if peak_vram_by_case else None
            ),
            "peak_vram_nvidia_smi_mb": poller.peak_delta_mb(),
            "n_cases_with_fresh_inference": n_fresh_inference,
            "note": (
                "主值 peak_vram_mb = fresh inference 各 case runner torch 分配器峰值的"
                "最大值（已有输出/--skip-inference 的 case 不计入）；交叉核对值 "
                "peak_vram_nvidia_smi_mb = 全程 nvidia-smi 轮询峰值 − 推理前基线"
                "（M2 口径）；两者皆 None 表示本次运行无 GPU 推理（只重算 Dice），"
                "不是 0 显存。"
            ),
        },
        "elapsed_s": round(elapsed, 1),
    }
    out_path = PROJECT_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"summary": summary, "gate": gate.to_dict(), "cases": results},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(
        f"[holdout] 低频类 {len(gate.low_frequency_classes)} 个"
        f"（< {MIN_PER_CLASS} cases），其 Dice 不受每类下限约束——见 "
        "summary.low_frequency_classes"
    )
    if summary["prostate"] is not None:
        pr = summary["prostate"]
        print(
            f"[holdout] prostate 最弱类：dice={pr['dice']}（{pr['cases']} cases），"
            f"dice=0 的 case {len(pr['zero_dice_cases'])} 例：{pr['zero_dice_cases']}"
        )
    print(f"[holdout] results -> {out_path}")
    return 0 if gate.passed else 1


if __name__ == "__main__":
    sys.exit(main())
