"""M6-e 第 2 步：qc_series 学生 mask 接线 + 419 对一致性回归（test_m6e_wiring.py）。

- 接线单测：case_id 给出且学生预测可取 → multi_teacher_consistency 真跑；
  无 case_id / 学生预测缺失 → 显式 skipped="no student mask"（不进 flags）。
- 419 对回归：M4 离线评估（eval/student_teacher_consistency.json，均值聚合
  flag 5/30 例，M4-REPORT §3.4）为基线，30 例 hold-out 用真实教师+学生 mask
  跑 run_qc，对比接入后 flag 行为（±1 例边界差异允许）。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from bodydonor.config import Settings
from bodydonor.qc import RULE_MULTI_TEACHER
from bodydonor.qc_pipeline import qc_series
from bodydonor.student_pred import StudentPrediction

REPO = Path(__file__).resolve().parents[1]
EVAL_BASELINE = json.loads(
    (REPO / "eval" / "student_teacher_consistency.json").read_text(encoding="utf-8")
)

# 最小 thresholds：只开 multi_teacher_consistency（其余 disabled——回归只看该规则）
MIN_THRESHOLDS = {
    "cold_start": {"dataset": "m6e_regression"},
    "rules": {
        "multi_teacher_consistency": {"enabled": True, "threshold": 0.80},
        "volume_prior": {"enabled": False},
        "connected_component": {"enabled": False},
        "holes": {"enabled": False},
        "boundary_smoothness": {"enabled": False},
        "hu_distribution": {"enabled": False},
        "left_right_symmetry": {"enabled": False},
    },
    "overall": {"hard_flag_rules": [], "min_score": 0.5},
}


def _seed_series_with_mask(settings: Settings, storage_key: str, arr: np.ndarray) -> str:
    """最小种子：Case/Series/MaskVersion（teacher），返回 series_id。

    storage_key 由调用方放好（storage 布局），mask 数组由调用方写盘。
    """
    from sqlalchemy.orm import Session

    from bodydonor import db
    from bodydonor.models import Case, MaskVersion, Series

    engine = db._engine(settings.database.url)
    try:
        with engine.connect() as c:
            sess = Session(c)
            sess.add(Case(case_id="case-wired"))
            sess.add(Series(series_id="series-wired", case_id="case-wired"))
            sess.add(
                MaskVersion(
                    mask_version_id="mv-wired",
                    series_id="series-wired",
                    source="teacher",
                    storage_key=storage_key,
                )
            )
            sess.commit()
            sess.close()
    finally:
        engine.dispose()
    return "series-wired"


def _make_settings(tmp_path: Path) -> Settings:
    """临时库 settings（不动真库）。"""
    from bodydonor.db import init_db

    url = f"sqlite:///{tmp_path / 'm6e_wiring.db'}"
    init_db(url)
    s = Settings()
    s.database.url = url
    return s


def _seed_mask_payload(tmp_path: Path, settings: Settings, arr: np.ndarray) -> tuple[str, str]:
    """mask + labels.json 写盘（LocalFsStorage 布局）+ DB 种子。

    labels.json 是 totalseg_classes 来源（HU 骨类跳过 + 左右对称性 +
    multi_teacher 按名对齐），单测里只放 aorta 一条即可。
    返回 (series_id, storage_key)。
    """
    from bodydonor.storage import LocalFsStorage

    st = LocalFsStorage(settings.resolved_data_dir / "storage")
    st.save("case-wired/series-wired/mask.nii.gz", _nifti_bytes(tmp_path, arr))
    st.save(
        "case-wired/series-wired/labels.json",
        json.dumps({"labels": [{"index": 3, "totalseg_class": "aorta"}]}).encode(),
    )
    return _seed_series_with_mask(settings, "case-wired/series-wired/mask.nii.gz", arr), (
        "case-wired/series-wired/mask.nii.gz"
    )


def _nifti_bytes(tmp_path: Path, arr: np.ndarray) -> bytes:
    """ndarray -> .nii.gz bytes（nibabel 只吃路径，先写临时文件再读回）。"""
    import nibabel as nib

    p = tmp_path / "seed_mask.nii.gz"
    nib.save(nib.Nifti1Image(arr, np.eye(4)), str(p))
    return p.read_bytes()


# ---------------------------------------------------------------------------
# qc_series 接线单测
# ---------------------------------------------------------------------------


def test_qc_series_without_case_id_skips_multi_teacher(tmp_path: Path) -> None:
    """无 case_id → multi_teacher_consistency 显式 skipped（M4 已定语义）。"""
    arr = np.zeros((4, 4, 4), dtype=np.uint8)
    arr[1:3, 1:3, 1:3] = 3  # aorta 会有体素
    s = _make_settings(tmp_path)
    series_id, storage_key = _seed_mask_payload(tmp_path, s, arr)
    out = qc_series(series_id, s, thresholds=MIN_THRESHOLDS)
    rs = out["rule_scores"][RULE_MULTI_TEACHER]
    assert rs == {"skipped": "no student mask"}
    assert RULE_MULTI_TEACHER not in out["flags"]
    assert out["student_source"] == "no case_id"


def test_qc_series_with_missing_prediction_skips(tmp_path: Path) -> None:
    """case_id 给出但学生预测缺失 → 显式 skipped，不 raise、不造假。"""
    arr = np.zeros((4, 4, 4), dtype=np.uint8)
    arr[1:3, 1:3, 1:3] = 3
    s = _make_settings(tmp_path)
    series_id, _ = _seed_mask_payload(tmp_path, s, arr)
    out = qc_series(series_id, s, thresholds=MIN_THRESHOLDS, case_id="no_such_case")
    rs = out["rule_scores"][RULE_MULTI_TEACHER]
    assert rs == {"skipped": "no student mask"}
    assert RULE_MULTI_TEACHER not in out["flags"]
    assert out["student_source"].startswith("unavailable:")


def test_qc_series_with_student_mask_runs_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case_id 给出且学生预测可取 → 规则真跑（mean_dice 进 rule_scores）。"""
    arr = np.zeros((4, 4, 4), dtype=np.uint8)
    arr[1:3, 1:3, 1:3] = 3
    s = _make_settings(tmp_path)
    series_id, _ = _seed_mask_payload(tmp_path, s, arr)

    import bodydonor.qc_pipeline as qcp

    def fake_load(case_id: str, settings: Settings | None = None) -> StudentPrediction:
        return StudentPrediction(
            case_id=case_id,
            mask_arr=arr,
            classes={3: "aorta"},
            pred_path=tmp_path / "fake_pred.nii.gz",
            model_version_id="student-test",
            weights_key="/tmp/weights.pth",
        )

    monkeypatch.setattr(qcp, "load_student_prediction", fake_load)
    out = qc_series(series_id, s, thresholds=MIN_THRESHOLDS, case_id="case-wired")
    rs = out["rule_scores"][RULE_MULTI_TEACHER]
    assert "skipped" not in rs, rs
    assert "mean_dice" in rs
    assert rs["n_compared_classes"] >= 1
    assert out["student_source"] == "student-test"


# ---------------------------------------------------------------------------
# 419 对一致性回归（真实教师 + 学生 mask，30 例 hold-out）
# ---------------------------------------------------------------------------


def _teacher_volume_and_classes(
    case_id: str,
) -> tuple[np.ndarray, dict[int, str]] | None:
    """case_id -> (教师整图 arr, teacher_classes)；教师 = ts_out 拆分文件拼接。

    只拼 Dataset501 15 类（ts_class_map 交集），与门禁口径一致。
    """
    import nibabel as nib

    data = Path.home() / "data/BodyDonor"
    ts_map = json.loads((data / "downloads/ct_lite/ts_class_map.json").read_text())
    if case_id.startswith("amos_"):
        teacher_dir = data / "downloads/amos22_subset" / case_id / "ts_out"
    else:
        teacher_dir = data / "downloads/ct_lite/ts_out" / case_id
    dataset_labels = set(
        json.loads((data / "student_holdout/pred/dataset.json").read_text(encoding="utf-8"))[
            "labels"
        ].keys()
    ) - {"background"}
    ref = None
    teacher_arr = None
    teacher_classes: dict[int, str] = {}
    for f in sorted(teacher_dir.glob("*.nii.gz")):
        name = f.name.removesuffix(".nii.gz")
        if name not in ts_map or name not in dataset_labels:
            continue
        img = nib.load(str(f))
        if ref is None:
            ref = img
            teacher_arr = np.zeros(img.shape, dtype=np.int16)
        teacher_arr[np.asarray(img.dataobj) > 0] = ts_map[name]
        teacher_classes[ts_map[name]] = name
    if teacher_arr is None:
        return None
    return teacher_arr, teacher_classes


def _baseline_flagged_cases() -> set[str]:
    """离线评估 flag 的 5 例（M4-REPORT §3.4 实测，2026-11-11）。

    s0209 0.1181、amos_0005 0.6592、s0338 0.7081、amos_0006 0.7567、
    amos_0016 0.7862——均值聚合 flag 5/30 例（正是分歧最大的 5 例）。
    """
    return {"s0209", "amos_0005", "s0338", "amos_0006", "amos_0016"}


def _real_products_available() -> bool:
    """419 对回归依赖的真实产物是否齐全（缺失时 skip 不 fail）。

    对照 test 真正的读取路径逐项探测：
    - 教师：downloads/amos22_subset、downloads/ct_lite/ts_out（逐类拼接）、
      downloads/ct_lite/ts_class_map.json（_teacher_volume_and_classes）
    - 学生：student_holdout/pred/dataset.json（dataset_labels）+
      eval/holdout.txt 各 case 的 pred/<case_id>.nii.gz
      （load_student_prediction 的产物路径）；DB passed ModelVersion 的
      weights_key 无法定位 nnUNet_raw/Dataset*/dataset.json 时同样缺失
      （load_student_prediction 会 raise），逐 case 捕获转 skip 条件。
    """
    data = Path.home() / "data/BodyDonor"
    paths = [
        data / "downloads/amos22_subset",
        data / "downloads/ct_lite/ts_class_map.json",
        data / "downloads/ct_lite/ts_out",
        data / "student_holdout/pred/dataset.json",
    ]
    if not all(p.exists() for p in paths):
        return False
    holdout = [
        line.split("\t")[0]
        for line in (REPO / "eval" / "holdout.txt").read_text().splitlines()
        if line.strip()
    ]
    # 学生预测：每个 hold-out case 的 pred 文件 + 类别表可解析（缺一即 skip）
    from bodydonor.student_pred import StudentPredictionError, load_student_prediction

    for case_id in holdout:
        if not (data / "student_holdout/pred" / f"{case_id}.nii.gz").exists():
            return False
        try:
            load_student_prediction(case_id)
        except StudentPredictionError:
            return False
    return True


@pytest.mark.skipif(
    not _real_products_available(),
    reason="~/data/BodyDonor 真实产物缺失（回归依赖 30 例 hold-out 教师+学生 mask）",
)
def test_regression_419_pairs_flag_baseline() -> None:
    """30 例 hold-out 真跑 run_qc（教师 vs 学生），flag 集合对比离线评估 5 例。

    基线：M4 离线评估均值聚合 flag 5/30（M4-REPORT §3.4 实测：s0209、
    amos_0005、s0338、amos_0006、amos_0016）。允许 ±1 例边界差异，差异需在
    报告记录原因。另校验总体 mean Dice 与 419 对基线 mean 0.856 的偏差
    < 0.02（教师整图拼接 vs 逐类文件的边界体素差可忽略）。
    """
    from bodydonor.qc import run_qc
    from bodydonor.student_pred import load_student_prediction

    holdout = [
        line.split("\t")[0]
        for line in (REPO / "eval" / "holdout.txt").read_text().splitlines()
        if line.strip()
    ]
    baseline = _baseline_flagged_cases()
    flagged: list[str] = []
    dices: list[float] = []
    for case_id in holdout:
        t = _teacher_volume_and_classes(case_id)
        assert t is not None, f"{case_id}: 无教师输出"
        teacher_arr, teacher_classes = t
        pred = load_student_prediction(case_id)
        res = run_qc(
            teacher_arr,
            tuple(int(v) for v in teacher_arr.shape),
            mask_version_id=f"m6e-regress-{case_id}",
            series_id=case_id,
            thresholds=MIN_THRESHOLDS,
            hu_values=None,
            totalseg_classes=teacher_classes,
            student_mask_arr=np.asarray(pred.mask_arr),
            student_classes=pred.classes,
        )
        rs = res.rule_scores[RULE_MULTI_TEACHER]
        assert "skipped" not in rs, f"{case_id}: 规则未真跑 {rs}"
        dices.append(float(rs["mean_dice"]))
        if rs["flagged"]:
            flagged.append(case_id)

    assert abs(len(flagged) - len(baseline)) <= 1, (
        f"flag 数 {len(flagged)} 与离线基线 {len(baseline)} 偏差 > 1 例"
        f"（flagged: {sorted(flagged)}）"
    )
    diff = set(flagged).symmetric_difference(baseline)
    assert len(diff) <= 2, (
        f"flag 集合与基线差异 > ±1 例：diff={sorted(diff)}，flagged={sorted(flagged)}"
    )
    mean_dice = float(np.mean(dices))
    baseline_mean = float(EVAL_BASELINE["overall"]["mean"])
    assert abs(mean_dice - baseline_mean) < 0.02, (
        f"mean Dice {mean_dice:.4f} 与 419 对基线 {baseline_mean} 偏差 ≥ 0.02"
    )
