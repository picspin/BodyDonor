"""student_pred.load_student_prediction 单测（M6-e 第 1 步）：读取入口找到/缺失。

- 找到：tmp 数据目录放真实结构产物（student_holdout/pred/<case>.nii.gz 单标签体
  + nnUNet_raw/Dataset*/dataset.json 类别表）+ tmp DB 种 passed ModelVersion，
  端到端真读（不 mock 判据本体）
- 缺失：无 passed ModelVersion / weights_key 无法定位 Dataset / case 无预测 /
  dataset.json labels 空 —— 四种缺失信号各自 StudentPredictionError，不返回假数据
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import SimpleITK as sitk  # noqa: N813 — sitk 官方惯用别名

from bodydonor.student_pred import (
    StudentPrediction,
    StudentPredictionError,
    load_student_prediction,
)

DATASET_DIR = "Dataset501_BodyDonorDistill"
LABELS = {
    "background": 0,
    "spleen": 1,
    "liver": 6,
    "prostate": 15,
}


@pytest.fixture
def env(tmp_path: Path):
    """tmp 数据目录 + init_db 的独立 SQLite + dataset.json + 种 ModelVersion。"""
    from bodydonor.config import Settings
    from bodydonor.db import init_db

    data_dir = tmp_path / "data" / "BodyDonor"
    nnunet_raw = data_dir / "nnUNet_raw" / DATASET_DIR
    nnunet_results = data_dir / "nnUNet_results" / DATASET_DIR / "fold_0"
    (nnunet_raw).mkdir(parents=True)
    (nnunet_results).mkdir(parents=True)
    (nnunet_raw / "dataset.json").write_text(json.dumps({"labels": LABELS}), encoding="utf-8")
    (nnunet_results / "checkpoint_final.pth").write_bytes(b"stub")
    url = f"sqlite:///{tmp_path / 'student_pred.db'}"
    init_db(url)
    s = Settings()
    s.data_dir = data_dir
    s.database.url = url
    return s


def _seed_model_version(settings, weights_key: str | None, gate_status: str = "passed"):
    from sqlalchemy.orm import Session

    from bodydonor import db
    from bodydonor.models import ModelVersion

    engine = db._engine(settings.database.url)
    try:
        with engine.connect() as c:
            s = Session(c)
            s.add(
                ModelVersion(
                    model_version_id="mv-student-test",
                    name="student-test",
                    weights_key=weights_key,
                    gate_status=gate_status,
                )
            )
            s.commit()
            s.close()
    finally:
        engine.dispose()


def _seed_prediction(settings, case_id: str, values: tuple[int, ...]) -> Path:
    """student_holdout/pred/<case>.nii.gz 单标签体（真实 NIfTI 落盘）。"""
    arr = np.zeros((8, 8, 4), dtype=np.uint8)
    for i, v in enumerate(values, start=1):
        arr[i, i, 1] = v
    pred_dir = settings.resolved_data_dir / "student_holdout" / "pred"
    pred_dir.mkdir(parents=True, exist_ok=True)
    img = sitk.GetImageFromArray(arr)
    out = pred_dir / f"{case_id}.nii.gz"
    sitk.WriteImage(img, str(out))
    return out


def test_load_found_returns_mask_and_classes(env):
    """找到：mask_arr 是预测单标签体 numpy、classes 是 dataset.json 值->类名。"""
    weights_key = str(
        env.resolved_data_dir / "nnUNet_results" / DATASET_DIR / "fold_0" / "checkpoint_final.pth"
    )
    _seed_model_version(env, weights_key)
    _seed_prediction(env, "s0001", values=(1, 6, 15))

    sp = load_student_prediction("s0001", settings=env)
    assert isinstance(sp, StudentPrediction)
    assert sp.case_id == "s0001"
    assert sp.classes == {1: "spleen", 6: "liver", 15: "prostate"}
    np.testing.assert_array_equal(sp.mask_arr[1, 1, 1], 1)  # 值 1（spleen）体素真读回
    assert set(np.unique(sp.mask_arr)) == {0, 1, 6, 15}
    assert sp.model_version_id == "mv-student-test"
    assert sp.weights_key == weights_key
    assert sp.pred_path.name == "s0001.nii.gz"


def test_load_missing_case_raises(env):
    """缺失 case：无预测文件 → StudentPredictionError（不返回假数据）。"""
    weights_key = str(
        env.resolved_data_dir / "nnUNet_results" / DATASET_DIR / "fold_0" / "checkpoint_final.pth"
    )
    _seed_model_version(env, weights_key)
    with pytest.raises(StudentPredictionError, match="无学生预测文件"):
        load_student_prediction("s9999", settings=env)


def test_load_no_passed_model_version_raises(env):
    """无 gate_status=passed 的 ModelVersion → StudentPredictionError。"""
    weights_key = str(
        env.resolved_data_dir / "nnUNet_results" / DATASET_DIR / "fold_0" / "checkpoint_final.pth"
    )
    _seed_model_version(env, weights_key, gate_status="pending")
    with pytest.raises(StudentPredictionError, match="gate_status=passed"):
        load_student_prediction("s0001", settings=env)


def test_load_weights_key_cannot_locate_dataset_raises(env):
    """weights_key 无 Dataset 目录 → StudentPredictionError（类别表无法解析）。"""
    _seed_model_version(env, str(env.resolved_data_dir / "some" / "weights.pth"))
    with pytest.raises(StudentPredictionError, match="Dataset"):
        load_student_prediction("s0001", settings=env)


def test_load_empty_labels_raises(env):
    """dataset.json labels 空 → StudentPredictionError（不返回假类别表）。"""
    raw = env.resolved_data_dir / "nnUNet_raw" / DATASET_DIR
    (raw / "dataset.json").write_text(json.dumps({"labels": {}}), encoding="utf-8")
    weights_key = str(
        env.resolved_data_dir / "nnUNet_results" / DATASET_DIR / "fold_0" / "checkpoint_final.pth"
    )
    _seed_model_version(env, weights_key)
    _seed_prediction(env, "s0001", values=(1,))
    with pytest.raises(StudentPredictionError, match="labels 为空"):
        load_student_prediction("s0001", settings=env)
