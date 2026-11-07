#!/usr/bin/env python3
"""固化 hold-out 构成（M3，proposal §8）：case_id 列表写 eval/holdout.txt。

构成（proposal §8 三源混合，按本机实际可用数据落定）：
  ① ts_public 15 例（CT-Lite 25 例子集，标注「与教师同源」，§8 偏乐观）
  ② amos 15 例（AMOS22 labelsTr 独立人工精标，防同源偏乐观）
  ③ local_correction 不进（M3 尚无人工修正产物；§8「要么不进要么足量 ≥5」）

泄漏检查：训练集（ct_lite 全 25 例训练用名单之外的 ts_out 案例 + 仓库内无本地
训练集）与 hold-out 交集必须为空，非空即中止。M3 冷启动阶段学生未训练，
训练集列表为空（M4 nnU-Net 蒸馏时由 qc_pipeline 启动前调 check_leak）。

用法：
    uv run python eval/build_holdout.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bodydonor.holdout import (  # noqa: E402
    HoldoutEntry,
    check_holdout_composition,
    check_leak,
    write_holdout,
)

# ① TS 公开集 15 例（CT-Lite 25 例中抽 15；这些 case 同批用于 QC 冷启动评分，
#    hold-out 用的是「teacher vs 真值 Dice」，与教师同源——偏乐观，§8 明示）
TS_PUBLIC_IDS = [
    "s0045", "s0071", "s0147", "s0209", "s0319",
    "s0321", "s0338", "s0392", "s0447", "s0495",
    "s0556", "s0662", "s0723", "s0797", "s0847",
]
# ② AMOS22 独立来源 15 例（labelsTr 纯人工精标，proposal §7.1 推荐独立金标准）
AMOS_IDS = [
    "amos_0001", "amos_0004", "amos_0005", "amos_0006", "amos_0007",
    "amos_0009", "amos_0010", "amos_0011", "amos_0014", "amos_0015",
    "amos_0016", "amos_0017", "amos_0019", "amos_0021", "amos_0023",
]


def main() -> int:
    entries = [HoldoutEntry(case_id=c, source="ts_public") for c in TS_PUBLIC_IDS]
    entries += [HoldoutEntry(case_id=c, source="amos") for c in AMOS_IDS]

    # 三源构成 + 规模校验（≥30、每源区间、无重复）
    problems = check_holdout_composition(entries)
    if problems:
        for p in problems:
            print(f"构成问题：{p}")
        return 1

    # 泄漏检查（M3 阶段训练集为空；M4 蒸馏启动时以真实训练 case 列表重查）
    check_leak([], [e.case_id for e in entries])

    write_holdout(entries)
    print(f"holdout.txt 固化：{len(entries)} cases"
          f"（ts_public={len(TS_PUBLIC_IDS)} amos={len(AMOS_IDS)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
