#!/usr/bin/env python3
"""M4 蒸馏教师推理（proposal §6 阶段 3）：对训练池缺教师 mask 的 case 跑 TS total。

训练池（AMOS22 非 hold-out + CT-Lite 非 hold-out）中只有部分 case 已有 TS total
输出（ts_out 每类一个 NIfTI）：CT-Lite 25 例全有（M3 冷启动跑过），AMOS 40 例中
只有 hold-out 15 例有（M3 门禁评估跑过）——**25 个训练 AMOS case 需现跑**。

- GPU 锁全程持锁（每 case 一锁，与 eval/evaluate_holdout.ts_teacher_inference
  同模式；批量推理 reason=batch，任务 total）
- 输出落 amos22_subset/{case}/ts_out/（与 hold-out case 目录布局一致）
- 许可：TS total 任务权重 Apache-2.0（"any usage"，商用蒸馏唯一安全教师，
  LICENSES §7/§8.1）；白名单判定在 run_total 内部强制（check_worker_task）
- 显存实测：runner 内 torch.cuda.max_memory_allocated（主值）逐 case 记录

用法：
    uv run python scripts/run_distill_teacher_inference.py [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bodydonor.config import load_settings  # noqa: E402
from bodydonor.distill_data import (  # noqa: E402
    build_train_pool,
    write_teacher_source_json,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个缺 mask 的 case")
    ap.add_argument("--dry-run", action="store_true", help="只列出缺 mask 的 case")
    args = ap.parse_args()

    settings = load_settings()
    pool = build_train_pool()
    missing = [c for c in pool if not any(c.teacher_out.glob("*.nii.gz"))]
    print(f"[teacher-infer] 训练池 {len(pool)} cases，缺教师 mask {len(missing)} 个")
    if args.limit:
        missing = missing[: args.limit]
    if not missing:
        print("[teacher-infer] 无需推理（全部已有教师 mask）")
        return 0
    if args.dry_run:
        for c in missing:
            print(f"  {c.case_id} ({c.source})")
        return 0

    from bodydonor.gpu_lock import acquire
    from bodydonor.segmentation import run_total

    results = []
    failures: list[dict] = []
    t0 = time.monotonic()
    for i, c in enumerate(missing, start=1):
        c.teacher_out.mkdir(parents=True, exist_ok=True)
        job_id = f"distill-teacher-{c.case_id[:20]}"
        lock = acquire(
            settings.resolved_data_dir / ".gpu.lock",
            reason="batch", task="total", job_id=job_id, wait=3600,
        )
        if lock is None:  # batch 模式排队或 TimeoutError，None 仅理论不可达
            print(f"  [{i}/{len(missing)}] {c.case_id}: GPU 锁获取失败，中止", file=sys.stderr)
            break
        try:
            t_case = time.monotonic()
            runner = run_total(
                c.image_path, c.teacher_out, task="total", device="gpu",
                settings=settings, job_id=job_id,
            )
            elapsed = time.monotonic() - t_case
            # 成功后写来源清单（位置与 read_teacher_provenance 一致：
            # teacher_out.parent / source.json）；失败 case 不写
            write_teacher_source_json(c.teacher_out, c.case_id)
            results.append({
                "case_id": c.case_id,
                "source": c.source,
                "elapsed_s": round(elapsed, 1),
                "peak_vram_mb": runner.peak_vram_mb,
                "n_classes": len(runner.classes),
            })
            print(
                f"  [{i}/{len(missing)}] {c.case_id}: {elapsed:.1f}s "
                f"peak={runner.peak_vram_mb}MB {len(runner.classes)} 类"
            )
        except Exception as e:  # noqa: BLE001 — 单 case 失败不丢已完成 case 的实测
            failures.append({"case_id": c.case_id, "error": str(e)[:300]})
            print(f"  [{i}/{len(missing)}] {c.case_id}: 失败 {e}", file=sys.stderr)
        finally:
            lock.release()

    # 单 case 失败不写 results 文件的情况已消除：failures 只记录，循环继续
    if failures:
        print(f"[teacher-infer] {len(failures)} 个 case 失败（详见 results 文件）")
        if len(failures) == len(missing):
            print("[teacher-infer] 全部 case 失败，中止", file=sys.stderr)
            return 1

    total_elapsed = time.monotonic() - t0
    summary = {
        "n_cases": len(results),
        "n_failures": len(failures),
        "failures": failures,
        "total_elapsed_s": round(total_elapsed, 1),
        "mean_elapsed_s": round(total_elapsed / len(results), 1) if results else 0,
        "peak_vram_mb_max": max(
            (r["peak_vram_mb"] for r in results if r["peak_vram_mb"] is not None),
            default=None,
        ),
        "results": results,
    }
    out = Path.home() / "data/BodyDonor/nnUNet_raw/distill_teacher_inference.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"},
                     ensure_ascii=False, indent=1))
    print(f"[teacher-infer] 实测记录 -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
