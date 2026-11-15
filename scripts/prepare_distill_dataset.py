#!/usr/bin/env python3
"""M4 蒸馏数据集准备（proposal §6 阶段 6 + LICENSES §8.3）。

训练池 = AMOS22 非 hold-out + CT-Lite 非 hold-out（eval/holdout.txt 逐 case 排除，
排除后 holdout.check_leak 以真实训练列表重查，泄漏即中止）。

训练标签**永远只用** TS total 教师 mask（已有 ts_out 117 类每类一个 NIfTI，复用
仓库 merge 逻辑合并为单标签图）；AMOS labelsTr 人工金标准（hold-out 真值）与
CT-Lite masks/ 绝不进训练集。许可门禁：拒绝 nnInteractive 修正产物 / licensed
任务教师 mask / SegVol 逃生通道产物（LICENSES §8.1/§8.3）。

类别范围：收敛到 hold-out 门禁需要的 AMOS 15 类（理由实测写进
bodydonor.distill_data 模块 docstring 与 manifest.class_scope.rationale）。

输出：~/data/BodyDonor/nnUNet_raw/Dataset501_BodyDonorDistill（imagesTr/ labelsTr/
dataset.json + manifest.json 逐 case 来源清单，供 ModelVersion 审计）。

用法：
    uv run python scripts/prepare_distill_dataset.py [--dry-run]

注意：不跑 TS 教师推理。AMOS 25 个训练 case 的 ts_out 若缺失会**报错中止**
（不静默跳过）——教师推理用 eval/evaluate_holdout.py 的 ts_teacher_inference
同模式补齐（GPU 锁全程持锁）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bodydonor.distill_data import prepare_dataset  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="只构建训练池 + 泄漏检查 + 统计，不落盘")
    ap.add_argument("--out-root", default="",
                    help="nnUNet_raw 根目录（默认 ~/data/BodyDonor/nnUNet_raw）")
    ap.add_argument("--profile", default="commercial", choices=["commercial", "research"],
                    help="许可门禁口径（S3F ③）：commercial 只放行 total；"
                         "research 走注册表 research_use")
    args = ap.parse_args()

    out_root = Path(args.out_root) if args.out_root else None
    try:
        result = prepare_dataset(out_root=out_root, dry_run=args.dry_run,
                                 profile=args.profile)
    except Exception as e:  # noqa: BLE001 — 许可门禁/泄漏/数据缺失都从这里呈现
        print(f"[distill] 失败：{e}", file=sys.stderr)
        return 1

    print(f"[distill] 完成：{result['n_cases']} cases -> {result['dataset_dir']}")
    print(f"[distill] manifest -> {result['manifest_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
