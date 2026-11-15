"""M1-fix：DICOM SEG 导出与校验测试（roundtrip：NIfTI mask -> SEG -> 重读 -> 逐体素 diff=0）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import SimpleITK as sitk  # noqa: N813

from bodydonor.export_seg import (
    SEG_MODALITY,
    SEG_SOP_CLASS_UID,
    SegExportError,
    SourceSeriesInfo,
    export_and_verify,
    export_seg,
    verify_seg,
)


@pytest.fixture
def source() -> SourceSeriesInfo:
    return SourceSeriesInfo(
        series_uid="1.2.826.0.1.3680043.8.498.seg.src",
        study_uid="1.2.826.0.1.3680043.8.498.123",
        modality="CT",
        rows=32,
        columns=32,
        pixel_spacing=(1.0, 1.0),
        slice_thickness_mm=1.0,
    )


def _make_mask(path, shape: tuple[int, int, int] = (8, 32, 32)) -> Path:
    """合成 3D mask（部分层有标签体素）。"""

    rng = np.random.default_rng(11)
    mask = (rng.random(shape) > 0.85).astype(np.uint8)
    mask[2:6, 8:20, 8:20] = 1  # 保证有体素
    sitk.WriteImage(sitk.GetImageFromArray(mask), str(path))
    return path


class TestSegExport:
    def test_roundtrip_voxel_diff_zero(self, tmp_path, source) -> None:  # noqa: ANN001
        """roundtrip：NIfTI mask -> SEG -> 重读 -> 逐体素 diff 为零。"""

        mask_path = _make_mask(tmp_path / "mask.nii.gz")
        seg_path = export_seg(mask_path, source, tmp_path / "seg.dcm")
        assert seg_path.exists()

        rep = verify_seg(
            seg_path, source_mask_path=mask_path, expected_series_uid=source.series_uid
        )
        assert rep.ok, rep.errors
        assert rep.readable is True
        assert rep.n_frames == 8  # 帧数 = mask 层数（omit_empty_frames=False）
        assert rep.n_segments == 1
        assert rep.voxel_diff_nonzero == 0
        assert rep.referenced_source_series == source.series_uid

    def test_export_and_verify_ok(self, tmp_path, source) -> None:  # noqa: ANN001
        """export_and_verify 一步通过。"""
        mask_path = _make_mask(tmp_path / "mask.nii.gz")
        rep = export_and_verify(mask_path, source, tmp_path / "seg2.dcm")
        assert rep.ok

    def test_seg_sop_class_and_modality(self, tmp_path, source) -> None:  # noqa: ANN001
        """pydicom 重读：SOPClassUID == Segmentation Storage，Modality == SEG。"""
        import pydicom

        mask_path = _make_mask(tmp_path / "mask.nii.gz")
        seg_path = export_seg(mask_path, source, tmp_path / "seg3.dcm")
        ds = pydicom.dcmread(str(seg_path))
        assert str(ds.SOPClassUID) == SEG_SOP_CLASS_UID
        assert str(ds.Modality) == SEG_MODALITY
        assert int(ds.NumberOfFrames) == 8

    def test_mask_shape_mismatch_raises(self, tmp_path, source) -> None:  # noqa: ANN001
        """mask 面内尺寸与源不一致 -> SegExportError（禁止静默重采样）。"""

        mask = np.zeros((8, 16, 16), dtype=np.uint8)
        mask[1:3, 4:10, 4:10] = 1
        p = tmp_path / "small.nii.gz"
        sitk.WriteImage(sitk.GetImageFromArray(mask), str(p))
        with pytest.raises(SegExportError, match="面内尺寸"):
            export_seg(p, source, tmp_path / "seg4.dcm")

    def test_slice_count_mismatch_raises(self, tmp_path, source) -> None:  # noqa: ANN001
        """mask 层数与源实例数不一致 -> SegExportError。"""
        source_inst = SourceSeriesInfo(
            series_uid=source.series_uid,
            study_uid=source.study_uid,
            modality="CT",
            rows=32,
            columns=32,
            source_instances=[{"sop_instance_uid": "1.2.3", "image_position_patient": [0, 0, 0]}],
        )
        mask_path = _make_mask(tmp_path / "mask8.nii.gz")  # 8 层 vs 1 源实例
        with pytest.raises(SegExportError, match="层数"):
            export_seg(mask_path, source_inst, tmp_path / "seg5.dcm")

    def test_verify_tampered_seg_diff_nonzero(self, tmp_path, source) -> None:  # noqa: ANN001
        """校验能抓到体素 diff 非零：源 mask 换掉后 verify 报错。"""
        mask_path = _make_mask(tmp_path / "mask.nii.gz")
        seg_path = export_seg(mask_path, source, tmp_path / "seg6.dcm")
        # 换一个不同的 mask（diff 非零）
        other = np.zeros((8, 32, 32), dtype=np.uint8)
        other[0:2, 0:4, 0:4] = 1
        other_path = tmp_path / "other.nii.gz"
        sitk.WriteImage(sitk.GetImageFromArray(other), str(other_path))
        rep = verify_seg(seg_path, source_mask_path=other_path)
        assert not rep.ok
        assert rep.voxel_diff_nonzero > 0

    def test_verify_wrong_series_uid_raises(self, tmp_path, source) -> None:  # noqa: ANN001
        """verify_seg：expected_series_uid 与 SEG 引用不一致 -> 报错。"""
        mask_path = _make_mask(tmp_path / "mask.nii.gz")
        seg_path = export_seg(mask_path, source, tmp_path / "seg7.dcm")
        rep = verify_seg(seg_path, source_mask_path=mask_path, expected_series_uid="1.2.999")
        assert not rep.ok
        assert any("源 series" in e for e in rep.errors)
        # export_and_verify 对同一 mask/seg 配对自洽 -> 通过
        rep2 = export_and_verify(mask_path, source, tmp_path / "seg8.dcm")
        assert rep2.ok
