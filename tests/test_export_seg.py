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
    SegVerifyError,
    SourceSeriesInfo,
    _build_source_images,
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


# ---------------------------------------------------------------------------
# S3 收口轮 ⑥：X-ray 导出修正——DX/CR 保留源信息，SEG 自身模态与源影像模态分开
# ---------------------------------------------------------------------------

class TestXraySourcePreserved:
    def test_dx_source_stays_dx(self, tmp_path) -> None:  # noqa: ANN001
        """DX 源的合成源实例 Modality 保留 DX（不一律转 CR）。"""
        src = SourceSeriesInfo(
            series_uid="1.2.826.0.1.3680043.8.498.dx",
            study_uid="1.2.826.0.1.3680043.8.498.dxstudy",
            modality="DX",
            rows=32,
            columns=32,
        )
        images = _build_source_images(src, 32, 32, 1)
        assert len(images) == 1
        assert images[0].Modality == "DX"
        assert images[0].SOPClassUID == "1.2.840.10008.5.1.4.1.1.1.1"  # DX Storage

    def test_cr_source_stays_cr(self, tmp_path) -> None:  # noqa: ANN001
        """CR 源保留 CR。"""
        src = SourceSeriesInfo(
            series_uid="1.2.826.0.1.3680043.8.498.cr",
            study_uid="1.2.826.0.1.3680043.8.498.crstudy",
            modality="CR",
            rows=32,
            columns=32,
        )
        images = _build_source_images(src, 32, 32, 1)
        assert images[0].Modality == "CR"
        assert images[0].SOPClassUID == "1.2.840.10008.5.1.4.1.1.1"  # CR Storage

    def test_xray_seg_roundtrip_preserves_source_info(self, tmp_path) -> None:  # noqa: ANN001
        """DX 源 SEG 导出 roundtrip：SEG 自身 Modality 恒为 SEG，源影像模态另记。"""
        src = SourceSeriesInfo(
            series_uid="1.2.826.0.1.3680043.8.498.dx2",
            study_uid="1.2.826.0.1.3680043.8.498.dx2study",
            modality="DX",
            rows=32,
            columns=32,
            pixel_spacing=(1.0, 1.0),
            slice_thickness_mm=1.0,
        )
        mask_path = _make_mask(tmp_path / "mask_dx.nii.gz")
        seg_path = export_seg(mask_path, src, tmp_path / "seg_dx.dcm")
        rep = verify_seg(seg_path, source_mask_path=mask_path)
        assert rep.ok, rep.errors
        import pydicom

        ds = pydicom.dcmread(str(seg_path))
        # SEG 对象自身模态恒为 SEG；源影像模态（DX）从引用序列分开核对
        assert ds.Modality == SEG_MODALITY
        assert ds.ReferencedSeriesSequence[0].ReferencedInstanceSequence[
            0
        ].ReferencedSOPClassUID == "1.2.840.10008.5.1.4.1.1.1.1"  # DX Storage

    def test_verify_seg_checks_source_modality(self, tmp_path) -> None:  # noqa: ANN001
        """S3R4 W-2：verify_seg 从 ReferencedSOPClassUID 反查源影像模态并核对
        （DX 源 SEG 通过；期望模态与引用不符时报错）。"""
        src = SourceSeriesInfo(
            series_uid="1.2.826.0.1.3680043.8.498.dx3",
            study_uid="1.2.826.0.1.3680043.8.498.dx3study",
            modality="DX",
            rows=32,
            columns=32,
            pixel_spacing=(1.0, 1.0),
            slice_thickness_mm=1.0,
        )
        mask_path = _make_mask(tmp_path / "mask_dx3.nii.gz")
        seg_path = export_seg(mask_path, src, tmp_path / "seg_dx3.dcm")
        # 期望模态与源一致 -> 通过
        rep = verify_seg(seg_path, source_mask_path=mask_path, expected_source_modality="DX")
        assert rep.ok, rep.errors
        # 期望模态与引用不符（DX 源被声称 CT）-> 报错
        rep2 = verify_seg(seg_path, source_mask_path=mask_path, expected_source_modality="CT")
        assert not rep2.ok
        assert any("源影像" in e for e in rep2.errors)

    def test_export_and_verify_checks_source_modality(self, tmp_path) -> None:  # noqa: ANN001
        """S3R5 W-2 收尾：export_and_verify 透传源模态核对——导出链路默认核对。

        现状边界：MR 合成源实例的 SOP Class 落默认 CT Storage（与 W-1 同族的
        已知未决项，见定稿报告未决清单）——接线前该不一致静默通过，接线后核对捕获。
        """
        src = SourceSeriesInfo(
            series_uid="1.2.826.0.1.3680043.8.498.mr5",
            study_uid="1.2.826.0.1.3680043.8.498.mr5study",
            modality="MR",
            rows=32,
            columns=32,
            pixel_spacing=(1.0, 1.0),
            slice_thickness_mm=1.0,
        )
        mask_path = _make_mask(tmp_path / "mask_mr5.nii.gz")
        with pytest.raises(SegVerifyError, match="源影像"):
            export_and_verify(mask_path, src, tmp_path / "seg_mr5.dcm")
