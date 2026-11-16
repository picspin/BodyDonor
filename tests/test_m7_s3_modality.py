"""S3 模态门闩（M7-S3）：HU 规则只对 CT 真跑；modality unknown 显式 skipped。

- 合成 MR：hu_distribution skipped "modality unsupported"，留记录
  （rule_scores 落 QCReport，抽检可查）
- modality NULL/空（旧数据未回填）：skipped "modality_unknown"
  （M7-PLAN v3 §2.2.3 跳过写原因，不静默按 CT 处理）
- CT 行为不变：HU 规则照跑；未声明模态的规则（volume_prior）不受门闩影响
- 全部合成 fixture（无本地产物依赖，CI 可跑）
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from bodydonor.config import Settings
from bodydonor.qc import RULE_HU_DISTRIBUTION, RULE_VOLUME_PRIOR, run_qc
from bodydonor.qc_pipeline import qc_series

# HU 规则开、其余关的最小 thresholds（门闩只看 hu_distribution）
S3_THRESHOLDS = {
    "cold_start": {"dataset": "s3_modality"},
    "rules": {
        "volume_prior": {"enabled": True, "min_frac": 0.0001, "max_frac": 0.65},
        "hu_distribution": {
            "enabled": True,
            "min_hu": -200,
            "max_hu": 400,
            "percentile": 5,
        },
        "connected_component": {"enabled": False},
        "holes": {"enabled": False},
        "boundary_smoothness": {"enabled": False},
        "left_right_symmetry": {"enabled": False},
    },
    "overall": {"hard_flag_rules": [], "min_score": 0.5},
}


# ---------------------------------------------------------------------------
# run_qc 单元层（模态门闩本体）
# ---------------------------------------------------------------------------


def _solid_mask(shape: tuple[int, ...] = (32, 32, 16)) -> np.ndarray:
    arr = np.zeros(shape, dtype=np.uint8)
    arr[10:20, 10:20, 2:12] = 1
    return arr


def test_run_qc_mr_hu_skipped() -> None:
    """合成 MR：HU 规则 skipped "modality unsupported"，不进 flags。"""
    arr = _solid_mask()
    hu = np.full(arr.shape, 50, dtype=np.int16)
    r = run_qc(
        arr,
        arr.shape,
        "mv-mr",
        "sid-mr",
        thresholds=S3_THRESHOLDS,
        hu_values=hu,
        totalseg_classes={1: "liver"},
        modality="MR",
    )
    assert RULE_HU_DISTRIBUTION not in r.flags
    assert r.rule_scores[RULE_HU_DISTRIBUTION] == {"skipped": "modality unsupported"}
    # 未声明模态的规则不受门闩影响：volume_prior 照跑（MR 也做体积先验）
    assert r.rule_scores[RULE_VOLUME_PRIOR]["flagged"] is False


def test_run_qc_modality_unknown_skipped() -> None:
    """modality 未知（None/不传）：skipped "modality_unknown"，不静默按 CT。"""
    arr = _solid_mask()
    hu = np.full(arr.shape, 50, dtype=np.int16)
    r = run_qc(
        arr,
        arr.shape,
        "mv-u",
        "sid-u",
        thresholds=S3_THRESHOLDS,
        hu_values=hu,
        totalseg_classes={1: "liver"},
        modality=None,
    )
    assert r.rule_scores[RULE_HU_DISTRIBUTION] == {"skipped": "modality_unknown"}
    # 不传 modality（旧调用方）同 unknown
    r2 = run_qc(
        arr,
        arr.shape,
        "mv-u2",
        "sid-u2",
        thresholds=S3_THRESHOLDS,
        hu_values=hu,
        totalseg_classes={1: "liver"},
    )
    assert r2.rule_scores[RULE_HU_DISTRIBUTION] == {"skipped": "modality_unknown"}


def test_run_qc_ct_hu_in_window_runs() -> None:
    """CT 行为不变：软组织 HU 在窗内，HU 规则真跑不 skipped 不 flag。"""
    arr = _solid_mask()
    hu = np.full(arr.shape, 50, dtype=np.int16)
    r = run_qc(
        arr,
        arr.shape,
        "mv-ct",
        "sid-ct",
        thresholds=S3_THRESHOLDS,
        hu_values=hu,
        totalseg_classes={1: "liver"},
        modality="CT",
    )
    assert RULE_HU_DISTRIBUTION not in r.flags
    rs = r.rule_scores[RULE_HU_DISTRIBUTION]
    assert "skipped" not in rs
    assert rs["flagged"] is False


def test_run_qc_ct_hu_out_of_window_flags() -> None:
    """CT 行为不变：HU 出软组织窗照常 flag（门闩不改变 CT 判定）。"""
    arr = _solid_mask()
    hu = np.full(arr.shape, 800, dtype=np.int16)  # 骨密度值出 [−200, 400] 窗
    r = run_qc(
        arr,
        arr.shape,
        "mv-ct2",
        "sid-ct2",
        thresholds=S3_THRESHOLDS,
        hu_values=hu,
        totalseg_classes={1: "liver"},
        modality="CT",
    )
    assert RULE_HU_DISTRIBUTION in r.flags


# ---------------------------------------------------------------------------
# qc_series 接线层（Series.modality 读取 + QCReport 留记录）
# ---------------------------------------------------------------------------


def _nii_bytes(tmp_path: Path, arr: np.ndarray) -> bytes:
    """ndarray -> .nii.gz bytes（nibabel 只吃路径，先写临时文件再读回）。"""
    import nibabel as nib

    p = tmp_path / f"seed_{abs(hash(arr.tobytes())) % 10**8}.nii.gz"
    nib.save(nib.Nifti1Image(arr, np.eye(4)), str(p))
    return p.read_bytes()


def _seed_qc_series(tmp_path: Path, modality: str | None) -> tuple[str, Settings]:
    """最小种子：Case/Series(modality)/MaskVersion + mask/image/labels 落盘。"""
    from sqlalchemy.orm import Session

    from bodydonor import db
    from bodydonor.db import init_db
    from bodydonor.models import Case, MaskVersion, Series
    from bodydonor.storage import LocalFsStorage

    url = f"sqlite:///{tmp_path / 's3_modality.db'}"
    init_db(url)
    s = Settings()
    s.database.url = url

    st = LocalFsStorage(s.resolved_data_dir / "storage")
    mask = np.zeros((8, 8, 8), dtype=np.uint8)
    mask[2:6, 2:6, 2:6] = 1  # 64 体素（HU 分位 ≥50 生效）
    hu = np.full((8, 8, 8), 50, dtype=np.int16)
    st.save("case-s3/series-s3/mask.nii.gz", _nii_bytes(tmp_path, mask))
    st.save("case-s3/series-s3/image.nii.gz", _nii_bytes(tmp_path, hu))
    st.save(
        "case-s3/series-s3/labels.json",
        json.dumps({"labels": [{"index": 1, "totalseg_class": "liver"}]}).encode(),
    )
    engine = db._engine(url)
    try:
        with engine.connect() as c:
            sess = Session(c)
            sess.add(Case(case_id="case-s3"))
            sess.add(Series(series_id="series-s3", case_id="case-s3", modality=modality))
            sess.add(
                MaskVersion(
                    mask_version_id="mv-s3",
                    series_id="series-s3",
                    source="teacher",
                    storage_key="case-s3/series-s3/mask.nii.gz",
                )
            )
            sess.commit()
            sess.close()
    finally:
        engine.dispose()
    return "series-s3", s


def _qc_report_rule_scores(settings: Settings) -> dict:
    """读回落库 QCReport 的 rule_scores（留记录验收）。"""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from bodydonor import db
    from bodydonor.models import QCReport

    engine = db._engine(settings.database.url)
    try:
        with engine.connect() as c:
            sess = Session(c)
            row = (
                sess.execute(select(QCReport).where(QCReport.mask_version_id == "mv-s3"))
                .scalars()
                .first()
            )
            sess.close()
            assert row is not None, "QCReport 未落库"
            return dict(row.rule_scores)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "modality,expect",
    [
        ("MR", {"skipped": "modality unsupported"}),
        (None, {"skipped": "modality_unknown"}),
    ],
)
def test_qc_series_modality_gate_leaves_record(
    tmp_path: Path, modality: str | None, expect: dict
) -> None:
    """qc_series 读 Series.modality：MR/unknown → HU skipped 且 QCReport 留记录。"""
    sid, s = _seed_qc_series(tmp_path, modality)
    out = qc_series(sid, s, thresholds=S3_THRESHOLDS)
    assert out["rule_scores"][RULE_HU_DISTRIBUTION] == expect
    assert RULE_HU_DISTRIBUTION not in out["flags"]
    # 留记录：skipped 注记落 QCReport.rule_scores（抽检可查原因）
    assert _qc_report_rule_scores(s)[RULE_HU_DISTRIBUTION] == expect


def test_qc_series_ct_hu_unchanged(tmp_path: Path) -> None:
    """CT 行为不变：qc_series 下 HU 规则照跑（软组织 HU 不 flag）。"""
    sid, s = _seed_qc_series(tmp_path, "CT")
    out = qc_series(sid, s, thresholds=S3_THRESHOLDS)
    rs = out["rule_scores"][RULE_HU_DISTRIBUTION]
    assert "skipped" not in rs
    assert rs["flagged"] is False
    assert RULE_HU_DISTRIBUTION not in out["flags"]
