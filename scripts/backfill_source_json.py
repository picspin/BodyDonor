#!/usr/bin/env python3
"""M7-S3P R2 ③：历史 ts_out 目录缺 source.json 的回填（S3R1 补录路径）。

现状：AMOS 40 目录中 20 个缺 source.json（amos_0001…0033，含 hold-out 15 例 +
M4 早于 a0da6d9 补丁跑出的训练 5 例），CT-Lite 25 例全部缺——全部先于
write_teacher_source_json（a0da6d9，2026-11-09）落补丁产出，属历史目录。

来源判定**逐目录从证据定**（不猜，定不下来保持缺失）：
 1. amos_XXXX 且 ts_out 117 类完整 + 与已有 20 份 source.json 逐字段同构
    -> source/task=total（TS total 任务权重 Apache-2.0，商用蒸馏唯一安全教师）
    依据分两档：
    - M3 门禁评估（mtime 2026-11-07 18:14–19:07）：eval/evaluate_holdout.py
      ts_teacher_inference，输出落 amos22_subset/{case}/ts_out/
    - M4 训练侧（mtime 2026-11-08 11:06–11:16）：scripts/
      run_distill_teacher_inference.py 81c94ac 版本（先于 a0da6d9 补丁）
 2. ct_lite ts_out/{case}（mtime 2026-11-07 17:26–18:55）-> M3 冷启动 TS 推理
    （scripts/qc_coldstart.py；qc_thresholds.yaml cold_start n_cases=25 实证）。
    写入位置：ts_out/source.json **一库一份**（read_teacher_provenance 读
    teacher_out.parent/source.json，ct_lite 布局下 parent=ts_out/），逐 case 判
    证据、全部通过才写（任一定不下来整库保持缺失）
 3. 证据不符（非 total 布局 / mtime 窗口外 / 类数不全）-> 保持缺失并逐个列出

红线：不删不重做已有数据；只写/补 source.json（teacher_out.parent /
source.json，与 read_teacher_provenance 读取位置一致）；不改门禁代码语义。
写入内容与 write_teacher_source_json 同构，外加 backfill 字段：
  {"source": "total", "task": "total", "created_by": ..., "case_id": ...,
   "backfill": true, "backfill_basis": "...", "backfilled_at": "...",
   "attestation": "inferred", "evidence": "mtime_window", "evidence_scope": ...,
   "generated_by": "backfill_source_json.py sha256=..."}

S3P2-R2 ①：证明字段（attestation 层级）——回填是脚本推断不是实测，写
attestation="inferred" + evidence="mtime_window"，与实测写入（write_teacher_
source_json 的 attestation="asserted"）肉眼可区分。永不降级：磁盘已有
asserted 文件不被回填覆盖（asserted 只能被更新的 asserted 覆盖）。已有文件
（无论有无证明字段）就地补证明字段，不删不重做——AMOS 每目录一份的
evidence_scope 写本 case id；ct_lite 库级文件枚举 evidence_scope=覆盖的
cases 列表。generated_by = 本脚本名 + 运行时自算 __file__ 的 sha256。

用法：
    PYTHONPATH=<worktree>/src python scripts/backfill_source_json.py [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bodydonor.distill_data import (  # noqa: E402
    AMOS,
    CT_LITE,
    missing_distill_classes,
    teacher_out_complete,
)
from bodydonor.holdout import load_holdout  # noqa: E402

# 依据分档：mtime 窗口（本地时间）+ 产生脚本，逐档有据可查
M3_EVAL_WINDOW = ("2026-11-07 18:00", "2026-11-07 19:30")
M4_TRAIN_WINDOW = ("2026-11-08 10:30", "2026-11-08 12:00")
M3_COLDSTART_WINDOW = ("2026-11-07 17:00", "2026-11-07 19:30")

BASIS_M3_EVAL = (
    "M3 门禁评估 TS 推理（eval/evaluate_holdout.py ts_teacher_inference，"
    "81c94ac 之前产出）：ts_out 117 类完整，mtime 在 M3 评估窗口"
)
BASIS_M4_TRAIN = (
    "M4 训练侧教师推理（scripts/run_distill_teacher_inference.py，81c94ac 版本"
    "先于 a0da6d9 write_teacher_source_json 补丁产出）：ts_out 117 类完整，"
    "mtime 在 M4 训练窗口"
)
BASIS_M3_COLDSTART = (
    "M3 QC 冷启动 TS 推理（scripts/qc_coldstart.py，qc_thresholds.yaml "
    "cold_start n_cases=25 实证）：ts_out 117 类完整，mtime 在 M3 冷启动窗口"
)

CREATED_BY = "run_distill_teacher_inference.py"
BACKFILLED_AT = "2026-11-16"


def _generated_by() -> str:
    """本脚本名 + 运行时自算 __file__ 的 sha256（谁写的可追溯到具体版本）。"""
    sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return f"backfill_source_json.py sha256={sha}"


ATTESTATION = "inferred"
EVIDENCE = "mtime_window"


def _in_window(mtime_epoch: float, window: tuple[str, str]) -> bool:
    lo = time.mktime(time.strptime(window[0], "%Y-%m-%d %H:%M"))
    hi = time.mktime(time.strptime(window[1], "%Y-%m-%d %H:%M"))
    return lo <= mtime_epoch <= hi


def _aorta_mtime(teacher_out: Path) -> float | None:
    """aorta.nii.gz mtime；缺失返回 None（undecided evidence 不因此崩）。"""
    f = teacher_out / "aorta.nii.gz"
    return f.stat().st_mtime if f.exists() else None


def _all_in_window(teacher_out: Path, window: tuple[str, str]) -> bool:
    """全部 nii 的 mtime 都在窗口内才算证据一致（单文件 mtime 判据偏弱，
    116 个其他文件逐一核对；目录空 -> False）。"""
    files = sorted(teacher_out.glob("*.nii.gz"))
    if not files:
        return False
    return all(_in_window(f.stat().st_mtime, window) for f in files)


def decide_amos_basis(case_dir: Path) -> tuple[str, str] | None:
    """amos_XXXX 目录证据判定，返回 (依据, created_by)；定不下来返回 None。"""
    teacher_out = case_dir / "ts_out"
    if not teacher_out_complete(teacher_out):
        return None  # 非 total 全量布局，不猜
    if missing_distill_classes(teacher_out):
        return None
    if _all_in_window(teacher_out, M3_EVAL_WINDOW):
        return BASIS_M3_EVAL, "evaluate_holdout.py ts_teacher_inference"
    if _all_in_window(teacher_out, M4_TRAIN_WINDOW):
        return BASIS_M4_TRAIN, CREATED_BY
    return None


def decide_ct_lite_basis(case_dir: Path) -> tuple[str, str] | None:
    """ct_lite ts_out/{case} 目录证据判定，定不下来返回 None。"""
    teacher_out = case_dir
    if not teacher_out_complete(teacher_out):
        return None
    if missing_distill_classes(teacher_out):
        return None
    if _all_in_window(teacher_out, M3_COLDSTART_WINDOW):
        return BASIS_M3_COLDSTART, "qc_coldstart.py"
    return None


def _upgrade_plan(target: Path, case_id: str, evidence_scope: list[str] | str) -> dict | None:
    """已有 source.json 的就地补证明字段计划（S3P2-R2 ①，不删不重做）。

    - attestation 已存在：inferred 已升级过（幂等跳过）；asserted 不动（永不降级）
    - 无 attestation 且 backfill=true：推断文件 -> 补 attestation/evidence/
      evidence_scope/generated_by（保留原有 source/task/backfill_basis 等字段）
    - 无 attestation 且无 backfill：旧实测写入（读侧默认 asserted），无需补
    """
    try:
        doc = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None  # 坏 JSON 不猜（读侧拒绝，留待人工处理）
    if not isinstance(doc, dict):
        return None
    if "attestation" in doc:
        return None  # asserted 永不降级；inferred 已升级过（幂等）
    if doc.get("backfill") is not True:
        return None  # 旧实测写入，读侧默认 asserted
    return {"path": str(target), "case_id": case_id,
            "evidence_scope": evidence_scope}


def _run(dry_run: bool) -> int:
    """回填主流程（main 委托；单测直接调本函数绕过 sys.argv）。"""
    holdout_ids = {e.case_id for e in load_holdout()}
    plans: list[dict[str, str]] = []  # {path, case_id, source_role, basis}
    upgrades: list[dict] = []  # {path, case_id, evidence_scope}（已有文件就地补证明字段）
    undecided: list[dict[str, str]] = []  # {case_id, checked, reason}

    for case_dir in sorted(AMOS.parent.glob("amos_*")):
        case_id = case_dir.name
        target = case_dir / "source.json"
        if target.exists():
            up = _upgrade_plan(target, case_id, case_id)
            if up is not None:
                upgrades.append(up)
            continue  # 已有清单，不重做
        decided = decide_amos_basis(case_dir)
        if decided is None:
            evidence = (
                f"nii 数={len(list((case_dir / 'ts_out').glob('*.nii.gz')))}, "
                f"缺蒸馏类={missing_distill_classes(case_dir / 'ts_out')[:3]}, "
                f"完整={teacher_out_complete(case_dir / 'ts_out')}, "
                f"aorta mtime={_aorta_mtime(case_dir / 'ts_out')}"
            )
            undecided.append({
                "case_id": case_id, "checked": evidence,
                "reason": "布局或 mtime 窗口与两档依据都不符，不猜",
            })
            continue
        basis, created_by = decided
        role = "holdout" if case_id in holdout_ids else "train"
        plans.append({"path": str(target), "case_id": case_id,
                      "source_role": role, "basis": basis,
                      "created_by": created_by})

    # ct_lite 布局 ts_out/{case}：read_teacher_provenance 读 teacher_out.parent
    # /source.json = ts_out/source.json，一库一份（非每 case 一份）。逐 case 判
    # 证据，全部通过才写库级一份（任一定不下来整库保持缺失，fail-closed）。
    ct_target = CT_LITE / "ts_out" / "source.json"
    if ct_target.exists():
        up = _upgrade_plan(ct_target, "ct_lite/ts_out", [])
        if up is not None:
            upgrades.append(up)
    else:
        ct_cases: list[str] = []
        ct_basis = ""
        ct_created_by = ""
        for case_dir in sorted((CT_LITE / "ts_out").iterdir()):
            if not case_dir.is_dir():
                continue
            case_id = case_dir.name
            decided = decide_ct_lite_basis(case_dir)
            if decided is None:
                evidence = (
                    f"nii 数={len(list(case_dir.glob('*.nii.gz')))}, "
                    f"完整={teacher_out_complete(case_dir)}, "
                    f"aorta mtime={_aorta_mtime(case_dir)}"
                )
                undecided.append({
                    "case_id": f"ct_lite/{case_id}", "checked": evidence,
                    "reason": "布局或 mtime 窗口与 M3 冷启动依据不符，不猜",
                })
                continue
            basis, created_by = decided
            ct_cases.append(case_id)
            ct_basis, ct_created_by = basis, created_by
        all_ct = [p.name for p in sorted((CT_LITE / "ts_out").iterdir()) if p.is_dir()]
        if ct_cases and ct_cases == all_ct:  # 非空且全部 case 证据通过才写
            plans.append({"path": str(ct_target), "case_id": "ct_lite/ts_out",
                          "source_role": f"library ({len(ct_cases)} cases)",
                          "basis": ct_basis, "created_by": ct_created_by,
                          "cases": ct_cases})
        elif ct_cases:
            print(f"[backfill] ct_lite 有 {len(all_ct) - len(ct_cases)} 个 case "
                  "证据不符，整库保持缺失（fail-closed）", file=sys.stderr)

    print(f"[backfill] 可回填 {len(plans)}，可升级 {len(upgrades)}，定不下来 {len(undecided)}")
    for p in plans:
        print(f"  WRITE {p['path']}  role={p['source_role']}  created_by={p['created_by']}")
    for u in upgrades:
        print(f"  UPGRADE {u['path']}"
              "（就地补证明字段 attestation/evidence/evidence_scope/generated_by）")
    for u in undecided:
        print(f"  KEEP-MISSING {u['case_id']}: {u['reason']}（checked: {u['checked']}）",
              file=sys.stderr)
    if dry_run:
        print("[backfill] dry-run，未写任何文件")
        return 0

    for u in upgrades:
        # 就地补证明字段：读原 doc，只增 attestation/evidence/evidence_scope/
        # generated_by，原有 source/task/backfill_basis 等字段全部保留
        target = Path(u["path"])
        doc = json.loads(target.read_text(encoding="utf-8"))
        doc["attestation"] = ATTESTATION
        doc["evidence"] = EVIDENCE
        doc["evidence_scope"] = u["evidence_scope"]
        doc["generated_by"] = _generated_by()
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, target)

    for p in plans:
        doc = {
            "source": "total",
            "task": "total",
            "created_by": p["created_by"],
            "case_id": p["case_id"],
            "backfill": True,
            "backfill_basis": p["basis"],
            "backfilled_at": BACKFILLED_AT,
            # S3P2-R2 ①：回填是脚本推断不是实测，写 inferred + 证据范围
            "attestation": ATTESTATION,
            "evidence": EVIDENCE,
            "evidence_scope": p["cases"] if "cases" in p else p["case_id"],
            "generated_by": _generated_by(),
        }
        if "cases" in p:  # ct_lite 库级清单：附逐 case 列表
            doc["cases"] = p["cases"]
        # 原子写入：中断不留半截 JSON（坏 JSON 会被 read_teacher_provenance 拒绝）
        target = Path(p["path"])
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, target)
    print(f"[backfill] 实写 {len(plans)} + 升级 {len(upgrades)} 个 source.json")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="只列清单不写文件")
    args = ap.parse_args()
    return _run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
