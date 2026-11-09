#!/usr/bin/env python3
"""M4 阶段 2b：登记学生模型 ModelVersion 行（D 步骤）。

- 读 M3 落库纪律：SQLite 单一写入（db.write_lock），ModelVersion 由蒸馏训练侧
  创建（persist_gate_result 只更新门禁字段，不建行——holdout.py docstring 实测）。
- 来源约束：base_model=nnU-Net v2；训练集 = Dataset501_BodyDonorDistill
  （TS total 教师 mask + 纯人工修正，商用安全路径 LICENSES §8.1）；
  gate_status=pending（门禁在阶段 2c/3 跑 hold-out 评估后由 persist_gate_result
  写入）。
- 权重路径在训练完成后才有（checkpoint_final.pth），先留空；写清 model_version_id
  与全部字段到 stdout 供上游取用。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

MANIFEST = (
    Path.home() / "data" / "BodyDonor" / "nnUNet_raw"
    / "Dataset501_BodyDonorDistill" / "manifest.json"
)


def main() -> int:
    from datetime import UTC, datetime

    from bodydonor import config, db, models

    settings = config.Settings()
    # 训练集清单实测（manifest 读真实文件，不编造）
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    n_cases = manifest.get("n_cases")
    label_policy = manifest.get("label_policy", {})

    mv_id = (
        "student-nnunet-v2-distill-"
        + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    name = "nnU-Net v2 3d_lowres student (M4 distillation, TS total teacher)"
    base_model = "nnU-Net v2"
    train_manifest_path = str(MANIFEST)
    source_constraint = (
        "commercial-safe: TS total teacher masks + manual correction only "
        "(LICENSES-THIRD-PARTY.md §8.1); nnInteractive/SegVol/licensed-task "
        "outputs excluded"
    )
    gate_status = "pending"

    engine = db._engine(settings.database.url)
    try:
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            try:
                row = models.ModelVersion(
                    model_version_id=mv_id,
                    name=name,
                    base_model=base_model,
                    weights_key=None,  # 训练完成后由阶段 3 回填 checkpoint_final.pth
                    gate_status=gate_status,
                    gate_metrics={
                        "train_manifest": train_manifest_path,
                        "n_train_cases": n_cases,
                        "source_constraint": source_constraint,
                        "label_policy": label_policy,
                        "configuration": "3d_lowres",
                        "fold": 0,
                        "epochs": 1000,
                        "note": "gate_metrics 在 hold-out 评估后由 persist_gate_result 覆盖",
                    },
                    gate_holdout_file=None,  # 门禁评估后由 persist_gate_result 写入
                )
                s.add(row)
                s.commit()
                print(json.dumps({
                    "model_version_id": mv_id,
                    "name": name,
                    "base_model": base_model,
                    "weights_key": None,
                    "gate_status": gate_status,
                    "train_manifest": train_manifest_path,
                    "n_train_cases": n_cases,
                    "source_constraint": source_constraint,
                }, ensure_ascii=False, indent=1))
            finally:
                s.close()
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
