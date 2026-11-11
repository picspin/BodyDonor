"""学生预测读取入口（M6-e 第 1 步）：从预测产物读同 case 学生 mask。

M4 阶段 6 学生评估（eval/evaluate_student_holdout.py）把学生预测落盘在
``<data_dir>/student_holdout/pred/<case_id>.nii.gz``（单标签体，值 = Dataset501
类别 1–15）。本模块提供可复用读取入口：给定 case id，从**当前已通过门禁的
学生 ModelVersion**（gate_status=passed，最新 created_at）解析 weights_key
→ 定位 nnUNet_raw/Dataset*/dataset.json 类别表 → 读同 case 预测 mask。

- **缺失语义**：找不到已通过门禁的 ModelVersion、weights_key 无法定位
  dataset.json、或该 case 无预测文件 → raise StudentPredictionError（不返回
  假数据；M6-c w3 项 / M6-d 不确定性特征消费方 catch 后按自身降级语义处理，
  如 w3 置零）。
- **覆盖范围**：预测产物只覆盖 hold-out 30 例（eval/holdout.txt，ts_public 15
  + amos 15）；生产路径上的 case id 与 hold-out case id 无交集（DB 实测），
  因此入口对生产 case 一律缺失——M6-e 第 2 步接线的显式 skipped 语义由此保证。
- 类别表格式 = dict[int, str]（Dataset501 值 → 类名），与 run_qc 的
  student_classes 参数、eval 评估的 dataset_labels 同构（按类名对齐所需）。

M6-e/M6-c/M6-d 共用：本入口只读 M4 学生评估产物（合规干净——不涉及
nnInteractive 产物），不触发推理。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings


class StudentPredictionError(Exception):
    """学生预测缺失（无 passed ModelVersion / dataset.json 无法定位 / case 无预测）。"""


@dataclass
class StudentPrediction:
    """一个 case 的学生预测（M6-c w3 / M6-d 不确定性特征的输入）。"""

    case_id: str
    mask_arr: Any  # np.ndarray，单标签体，值 = Dataset501 类别 1-15
    classes: dict[int, str]  # Dataset501 值 -> 类名（按类名对齐所需）
    pred_path: Path  # 预测文件落盘路径
    model_version_id: str  # 产生该预测的 ModelVersion（血缘/DecisionTrace 用）
    weights_key: str  # ModelVersion.weights_key（checkpoint 路径）


def _resolve_dataset_json(weights_key: str) -> Path:
    """weights_key（checkpoint 路径）-> nnUNet_raw/Dataset*/dataset.json。

    checkpoint 路径形如 ~/data/BodyDonor/nnUNet_results/Dataset501_BodyDonorDistill/
    nnUNetTrainer__nnUNetPlans__3d_lowres/fold_0/checkpoint_final.pth：
    nnUNet_results 同级的 nnUNet_raw 下同 Dataset 目录的 dataset.json 是类别表
    单一事实来源（学生 15 类 = 该文件 labels，评估与规则同源）。
    """
    parts = Path(weights_key).parts
    dataset_dir = next((i for i, p in enumerate(parts) if p.startswith("Dataset")), None)
    if dataset_dir is None:
        raise StudentPredictionError(
            f"weights_key 无法定位 Dataset 目录（期望 nnUNet_results/Dataset*/…）: {weights_key}"
        )
    raw_root = Path(*parts[:dataset_dir]).with_name("nnUNet_raw")
    dataset_json = raw_root / parts[dataset_dir] / "dataset.json"
    if not dataset_json.exists():
        raise StudentPredictionError(f"dataset.json 缺失（学生类别表无法解析）: {dataset_json}")
    return dataset_json


def load_student_prediction(
    case_id: str,
    settings: Settings | None = None,
) -> StudentPrediction:
    """给定 case id，读当前学生 ModelVersion 对应的同 case 预测 mask。

    返回 StudentPrediction（mask_arr = numpy 单标签体，值 1-15；classes =
    Dataset501 值 -> 类名）。缺失信号 = raise StudentPredictionError：

    - 无 gate_status=passed 的学生 ModelVersion（或其 weights_key 为空）
    - weights_key 无法定位 nnUNet_raw/Dataset*/dataset.json
    - 该 case 无预测文件（<data_dir>/student_holdout/pred/<case_id>.nii.gz）——
      预测产物只覆盖 hold-out 30 例，生产 case 一律走此缺失信号

    不触发推理、不返回假数据；调用方（M6-c w3 / M6-d 不确定性特征）catch 后
    按各自降级语义处理。
    """
    import json as _json

    import numpy as np

    from . import db
    from .models import ModelVersion

    settings = settings or Settings()

    # 当前学生 ModelVersion：gate_status=passed、最新 created_at（单一事实）
    url = settings.database.url
    engine = db._engine(url)
    try:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        with engine.connect() as conn:
            s = Session(conn)
            try:
                mv = (
                    s.execute(
                        select(ModelVersion)
                        .where(ModelVersion.gate_status == "passed")
                        .order_by(ModelVersion.created_at.desc())
                        .limit(1)
                    )
                    .scalars()
                    .first()
                )
            finally:
                s.close()
    finally:
        engine.dispose()
    if mv is None or not mv.weights_key:
        raise StudentPredictionError(
            "无已通过门禁（gate_status=passed）的学生 ModelVersion 或 weights_key 为空"
            "——学生预测产物无法定位"
        )

    dataset_json = _resolve_dataset_json(mv.weights_key)
    doc = _json.loads(dataset_json.read_text(encoding="utf-8"))
    classes = {int(v): k for k, v in doc.get("labels", {}).items() if k != "background"}
    if not classes:
        raise StudentPredictionError(f"dataset.json labels 为空: {dataset_json}")

    pred_path = settings.resolved_data_dir / "student_holdout" / "pred" / f"{case_id}.nii.gz"
    if not pred_path.exists():
        raise StudentPredictionError(
            f"case 无学生预测文件: {pred_path}（预测产物只覆盖 M4 学生评估的 hold-out 30 例）"
        )

    import nibabel as nib

    pred_img = nib.load(str(pred_path))
    mask_arr = np.asarray(pred_img.dataobj)
    return StudentPrediction(
        case_id=case_id,
        mask_arr=mask_arr,
        classes=classes,
        pred_path=pred_path,
        model_version_id=mv.model_version_id,
        weights_key=mv.weights_key,
    )


__all__ = ["StudentPrediction", "StudentPredictionError", "load_student_prediction"]
