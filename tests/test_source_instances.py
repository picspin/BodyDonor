"""M2-5(a)：源 DICOM 实例持久化回填测试。

真实引用路径：ingest 提取源实例 -> commit 落库 -> load_source_instances 读回 ->
source_info_from_persisted -> export_seg 用真实 SOP 引用 -> roundtrip diff=0。
合成 fallback：无源实例（NIfTI 输入）时 export_seg 合成源实例仍可用。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import SimpleITK as sitk  # noqa: N813
from tests.factories import make_dicom_dataset, save_dicom

from bodydonor.export_seg import export_seg, verify_seg
from bodydonor.ingest import extract_source_instances
from bodydonor.pipeline import load_source_instances, source_info_from_persisted


@pytest.fixture
def dicom_series_dir(tmp_path: Path) -> Path:  # noqa: ANN001
    """5 层合成 DICOM 序列（每层不同 IPP，沿 z 递增）。"""
    root = tmp_path / "series"
    root.mkdir(parents=True)
    uid = "1.2.826.0.1.3680043.8.498.srcinst"
    for i in range(5):
        ds = make_dicom_dataset(series_uid=uid, num_slices=5, rows=32, cols=32)
        ds.ImagePositionPatient = [0.0, 0.0, float(i)]
        ds.SOPInstanceUID = f"1.2.826.0.1.3680043.8.498.inst.{i + 1}"
        save_dicom(ds, root / f"slice{i}.dcm")
    return root


def _items_from_dir(root: Path) -> list[tuple[Path, object]]:  # noqa: ANN401
    import pydicom

    return [(p, pydicom.dcmread(str(p))) for p in sorted(root.glob("*.dcm"))]


class TestExtractSourceInstances:
    def test_extracts_sop_uids_and_geometry(self, dicom_series_dir: Path) -> None:
        """提取逐层 SOP UID + 几何三要素。"""
        items = _items_from_dir(dicom_series_dir)
        info = extract_source_instances(items, rows=32, cols=32)
        assert info["rows"] == 32
        assert info["columns"] == 32
        insts = info["instances"]
        assert len(insts) == 5
        uids = {inst["sop_instance_uid"] for inst in insts}
        assert "1.2.826.0.1.3680043.8.498.inst.1" in uids
        # 几何三要素齐全
        for inst in insts:
            assert len(inst["image_orientation_patient"]) == 6
            assert len(inst["image_position_patient"]) == 3
            assert inst["pixel_spacing"]
            assert inst["slice_thickness_mm"]

    def test_sorted_by_normal_descending(self, dicom_series_dir: Path) -> None:
        """按法向投影降序（帧 0 = 法向距离最大，DICOM 帧序惯例）。"""
        items = _items_from_dir(dicom_series_dir)
        info = extract_source_instances(items, rows=32, cols=32)
        z_vals = [inst["image_position_patient"][2] for inst in info["instances"]]
        assert z_vals == sorted(z_vals, reverse=True)

    def test_no_pixel_data_in_extracted(self, dicom_series_dir: Path) -> None:
        """提取只取头字段，不含像素（像素不进 AgentContext/不持久化两份）。"""
        items = _items_from_dir(dicom_series_dir)
        info = extract_source_instances(items, rows=32, cols=32)
        assert all("PixelData" not in str(k) for k in info)
        assert all("pixel_data" not in inst for inst in info["instances"])


class TestPersistRoundtrip:
    def test_commit_persists_and_readback(
        self, tmp_path, settings, dicom_series_dir: Path
    ) -> None:  # noqa: ANN001
        """commit 落库 source_instances_json -> load_source_instances 读回。

        5 层合成序列被选择器排除（<30 层防线），显式勾选覆盖（同 guard 语义）。
        """
        from bodydonor.pipeline import commit, scan
        from bodydonor.storage import LocalFsStorage

        outcome = scan(dicom_series_dir, settings)
        assert outcome.report.n_series == 1
        sid = outcome.report.series[0].series_id

        settings.database.url = f"sqlite:///{tmp_path}/t.db"
        storage = LocalFsStorage(tmp_path / "storage")
        committed = commit(outcome, settings, storage, user_overrides=[sid])
        assert sid in committed.selected_series_ids

        persisted = load_source_instances(sid, settings)
        assert persisted is not None
        assert len(persisted["instances"]) == 5
        uids = {inst["sop_instance_uid"] for inst in persisted["instances"]}
        assert "1.2.826.0.1.3680043.8.498.inst.3" in uids

    def test_readback_missing_series_returns_none(self, tmp_path, settings) -> None:  # noqa: ANN001
        from bodydonor import db

        settings.database.url = f"sqlite:///{tmp_path}/empty.db"
        db.init_db(settings.database.url)  # 建表（空库）
        assert load_source_instances("no-such-series", settings) is None


class TestSegExportRealReferences:
    def test_real_reference_roundtrip_voxel_diff_zero(
        self, tmp_path, dicom_series_dir: Path
    ) -> None:
        """真实引用路径 roundtrip：持久化信息 -> SourceSeriesInfo -> SEG -> 重读 diff=0。"""
        items = _items_from_dir(dicom_series_dir)
        persisted = extract_source_instances(items, rows=32, cols=32)

        # 模拟从 DB 读回（直接用 extract 结果；load_source_instances 已单测）
        study_uid = "1.2.826.0.1.3680043.8.498.123"
        series_uid = "1.2.826.0.1.3680043.8.498.srcinst"
        source = source_info_from_persisted(persisted, series_uid, study_uid)

        # 真实 SOP 引用进了 SourceSeriesInfo
        assert len(source.source_instances) == 5
        assert source.source_instances[0]["sop_instance_uid"].startswith(
            "1.2.826.0.1.3680043.8.498.inst."
        )

        # mask 与源同几何（5 层 32x32）
        rng = np.random.default_rng(3)
        mask = (rng.random((5, 32, 32)) > 0.9).astype(np.uint8)
        mask[1:4, 8:20, 8:20] = 1
        mask_path = tmp_path / "mask.nii.gz"
        sitk.WriteImage(sitk.GetImageFromArray(mask), str(mask_path))

        seg_path = export_seg(mask_path, source, tmp_path / "seg.dcm")
        rep = verify_seg(
            seg_path,
            source_mask_path=mask_path,
            expected_series_uid=series_uid,
        )
        assert rep.ok, rep.errors
        assert rep.voxel_diff_nonzero == 0
        assert rep.n_frames == 5

    def test_real_reference_sop_uids_in_seg(self, tmp_path, dicom_series_dir: Path) -> None:
        """SEG 的 ReferencedSOPInstanceUID 是真实源 SOP UID（非合成）。"""
        import pydicom

        items = _items_from_dir(dicom_series_dir)
        persisted = extract_source_instances(items, rows=32, cols=32)
        source = source_info_from_persisted(
            persisted, "1.2.826.0.1.3680043.8.498.srcinst", "1.2.826.0.1.3680043.8.498.123"
        )

        rng = np.random.default_rng(4)
        mask = (rng.random((5, 32, 32)) > 0.9).astype(np.uint8)
        mask[0:2, 4:12, 4:12] = 1
        mask_path = tmp_path / "mask.nii.gz"
        sitk.WriteImage(sitk.GetImageFromArray(mask), str(mask_path))

        seg_path = export_seg(mask_path, source, tmp_path / "seg.dcm")
        ds = pydicom.dcmread(str(seg_path))
        ref = ds.ReferencedSeriesSequence[0]
        ref_uids = {
            str(item.ReferencedSOPInstanceUID)
            for item in ref.ReferencedInstanceSequence
        }
        # 真实源实例 UID 全部出现在 SEG 引用里
        for i in range(1, 6):
            assert f"1.2.826.0.1.3680043.8.498.inst.{i}" in ref_uids

    def test_synthetic_fallback_still_works(self, tmp_path) -> None:
        """无源实例（NIfTI 输入）时合成 fallback 仍可用（不回归）。"""
        from tests.test_export_seg import source as _  # noqa: F401

        source = source_info_from_persisted(
            {"rows": 32, "columns": 32, "instances": []},
            "1.2.826.0.1.3680043.8.498.fallback",
            "1.2.826.0.1.3680043.8.498.123",
        )
        # 空实例列表 -> source_instances 空 -> export_seg 走合成路径
        assert len(source.source_instances) == 0
        rng = np.random.default_rng(5)
        mask = (rng.random((4, 32, 32)) > 0.9).astype(np.uint8)
        mask[0:2, 8:16, 8:16] = 1
        mask_path = tmp_path / "mask.nii.gz"
        sitk.WriteImage(sitk.GetImageFromArray(mask), str(mask_path))
        seg_path = export_seg(mask_path, source, tmp_path / "seg.dcm")
        rep = verify_seg(seg_path, source_mask_path=mask_path)
        assert rep.ok, rep.errors
        assert rep.voxel_diff_nonzero == 0


class TestCommitWithFixture:
    def test_commit_persists_instances(
        self, tmp_path, settings, dicom_series_dir: Path
    ) -> None:
        """端到端：scan -> commit（显式勾选覆盖 <30 层）-> load_source_instances。"""
        from bodydonor.pipeline import commit, scan
        from bodydonor.storage import LocalFsStorage

        outcome = scan(dicom_series_dir, settings)
        assert outcome.report.n_series == 1
        sid = outcome.report.series[0].series_id

        settings.database.url = f"sqlite:///{tmp_path}/t2.db"
        storage = LocalFsStorage(tmp_path / "storage")
        commit(outcome, settings, storage, user_overrides=[sid])

        persisted = load_source_instances(sid, settings)
        assert persisted is not None
        assert len(persisted["instances"]) == 5
