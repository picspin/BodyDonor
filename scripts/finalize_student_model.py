#!/usr/bin/env python3
"""M4 阶段 6：学生 ModelVersion 回填（weights_key + 门禁结果落库）。

- weights_key = checkpoint_final.pth 绝对路径（只更新该行，不新建；走
  db.write_lock 单一写入纪律，仿 register_student_model.py）。
- 再调 holdout.persist_gate_result 写 gate_status/gate_metrics/gate_holdout_file
  （holdout 文件传 eval/holdout.txt 的仓库相对路径）。
- 写完读回该行核对三字段（weights_key/gate_status/gate_holdout_file）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

WEIGHTS_KEY = (
    "/home/gogvf/data/BodyDonor/nnUNet_results/Dataset501_BodyDonorDistill"
    "/nnUNetTrainer__nnUNetPlans__3d_lowres/fold_0/checkpoint_final.pth"
)
MODEL_VERSION_ID = "student-nnunet-v2-distill-20261109T062920Z"


def main() -> int:
    from sqlalchemy import select

    from bodydonor import config, db, models

    settings = config.Settings()
    engine = db._engine(settings.database.url)
    try:
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            try:
                mv = s.execute(
                    select(models.ModelVersion).where(
                        models.ModelVersion.model_version_id
                        == MODEL_VERSION_ID[:60]
                    )
                ).scalar_one_or_none()
                if mv is None:
                    print(f"ModelVersion 不存在：{MODEL_VERSION_ID}", file=sys.stderr)
                    return 1
                mv.weights_key = WEIGHTS_KEY
                s.commit()
                print(json.dumps({
                    "model_version_id": mv.model_version_id,
                    "weights_key": mv.weights_key,
                }, ensure_ascii=False))
            finally:
                s.close()
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
