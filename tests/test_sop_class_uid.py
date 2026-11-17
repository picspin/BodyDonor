"""S3P2 ③：SOPClassUID 原样回填测试——删除"默认 CT"设计。

真实 SOPClassUID 逐字 round-trip（Enhanced MR / PET 各档）、未映射模态
raise（PT/US/NM/MG/XA）、合成源 fallback（DX/CR/MR/CT）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import SimpleITK as sitk  # noqa: N813
from tests.factories import make_dicom_dataset, save_dicom

from bodydonor.export_seg import (
    SegExportError,
    SourceSeriesInfo,
    _build_source_images,
    export_and_verify,
    export_seg,
    resolve_source_sop_class_uid,
    verify_seg,
)
from bodydonor.ingest import extract_source_instances
from bodydonor.pipeline import source_info_from_persisted

# 各档候选（pydicom UID 字典值，非记忆）
ENHANCED_MR = "1.2.840.10008.5.1.4.1.1.4.1"  # Enhanced MR Image Storage
LEGACY_ENHANCED_MR = "1.2.840.10008.5.1.4.1.1.4.4"  # Legacy Converted Enhanced MR
PET = "1.2.840.10008.5.1.4.1.1.128"  # PET Image Storage
PET_SOONE = "1.2.840.10008.5.1.4.1.1.130"  # PET Image Storage（Retired）
PET_CROPPED = "1.2.840.10008.5.1.4.1.1.128.1"  # Optimized PET Image Storage


def _make_mask(path, shape: tuple[int, int, int] = (4, 32, 32)) -> Path:
    rng = np.random.default_rng(7)
    mask = (rng.random(shape) > 0.9).astype(np.uint8)
    mask[0:2, 8:20, 8:20] = 1
    sitk.WriteImage(sitk.GetImageFromArray(mask), str(path))
    return path


def _dicom_dir_with_sop_class(tmp_path: Path, sop_class: str, n: int = 4) -> Path:
    """n 层合成 DICOM 序列，每层指定 SOP Class UID。"""
    root = tmp_path / f"series_{sop_class.replace('.', '_')}"
    root.mkdir(parents=True)
    for i in range(n):
        ds = make_dicom_dataset(
            series_uid="1.2.826.0.1.3680043.8.498.sopcls", num_slices=n, rows=32, cols=32
        )
        ds.ImagePositionPatient = [0.0, 0.0, float(i)]
        ds.SOPInstanceUID = f"1.2.826.0.1.3680043.8.498.sopcls.{i + 1}"
        ds.SOPClassUID = sop_class
        ds.file_meta.MediaStorageSOPClassUID = sop_class
        save_dicom(ds, root / f"slice{i}.dcm")
    return root


def _persisted_from_dir(root: Path) -> dict:
    import pydicom

    items = [(p, pydicom.dcmread(str(p))) for p in sorted(root.glob("*.dcm"))]
    return extract_source_instances(items, rows=32, cols=32)


class TestVerbatimRoundTrip:
    """真实 SOPClassUID 逐字 round-trip（不靠映射表）。"""

    # 经典 PET（…128）是单帧 IOD，可走全链路 round-trip；Enhanced MR/PET 各档
    # （…4.1/…4.4/…130/…128.1）是多帧 IOD，highdicom 拒绝其作为单帧 source
    # images——这些只能在 resolve 层测原样 UID（TestVerbatimResolveLayer）。
    @pytest.mark.parametrize("sop_class", [PET])
    def test_verbatim_sop_class_in_seg(self, tmp_path, sop_class: str) -> None:
        """源实例带非默认 SOP Class 时 SEG 的 ReferencedSOPClassUID 逐字相等。"""
        src_dir = _dicom_dir_with_sop_class(tmp_path, sop_class)
        persisted = _persisted_from_dir(src_dir)
        source = source_info_from_persisted(
            persisted, "1.2.826.0.1.3680043.8.498.sopcls", "1.2.826.0.1.3680043.8.498.123", "MR"
        )
        assert source.source_instances[0]["sop_class_uid"] == sop_class

        mask_path = _make_mask(tmp_path / "mask.nii.gz")
        seg_path = export_seg(mask_path, source, tmp_path / "seg.dcm")

        import pydicom

        ds = pydicom.dcmread(str(seg_path))
        ref = ds.ReferencedSeriesSequence[0].ReferencedInstanceSequence[0]
        assert str(ref.ReferencedSOPClassUID) == sop_class

        # verify_seg 逐字比对通过（靠原样比对，非反查表）
        rep = verify_seg(
            seg_path,
            source_mask_path=mask_path,
            expected_source_sop_class_uid=sop_class,
        )
        assert rep.ok, rep.errors

    @pytest.mark.parametrize(
        "sop_class", [ENHANCED_MR, LEGACY_ENHANCED_MR, PET_SOONE, PET_CROPPED]
    )
    def test_verbatim_resolve_layer_multiframe(self, sop_class: str) -> None:
        """多帧 IOD 各档（Enhanced MR/PET）在 resolve 层逐字原样通过（不靠映射）。"""
        src = SourceSeriesInfo(
            series_uid="1.2.826.0.1.3680043.8.498.mf",
            study_uid="1.2.826.0.1.3680043.8.498.mfstudy",
            modality="MR",
            rows=32,
            columns=32,
            source_instances=[
                {"sop_instance_uid": "1.2.3", "sop_class_uid": sop_class,
                 "image_position_patient": [0.0, 0.0, 0.0]}
            ],
        )
        assert resolve_source_sop_class_uid(src, src.source_instances[0]) == sop_class
        images = _build_source_images(src, 32, 32, 1)
        assert images[0].SOPClassUID == sop_class

    def test_export_and_verify_verbatim_sop_class(self, tmp_path) -> None:
        """export_and_verify 的期望 SOP Class 与建成 SEG 引用同源（永不分叉）。"""
        src_dir = _dicom_dir_with_sop_class(tmp_path, PET)
        persisted = _persisted_from_dir(src_dir)
        source = source_info_from_persisted(
            persisted, "1.2.826.0.1.3680043.8.498.sopcls", "1.2.826.0.1.3680043.8.498.123", "PT"
        )
        mask_path = _make_mask(tmp_path / "mask.nii.gz")
        rep = export_and_verify(mask_path, source, tmp_path / "seg.dcm")
        assert rep.ok, rep.errors

    def test_extract_carries_sop_class_uid(self, tmp_path) -> None:
        """ingest.extract_source_instances 提取 SOPClassUID。"""
        src_dir = _dicom_dir_with_sop_class(tmp_path, ENHANCED_MR)
        persisted = _persisted_from_dir(src_dir)
        assert persisted["instances"][0]["sop_class_uid"] == ENHANCED_MR


class TestUnmappedRaises:
    """未映射模态（合成源，无实例 UID）一律 raise，不默认 CT。"""

    @pytest.mark.parametrize("modality", ["PT", "US", "NM", "MG", "XA"])
    def test_unmapped_synthetic_source_raises(self, modality: str) -> None:
        src = SourceSeriesInfo(
            series_uid="1.2.826.0.1.3680043.8.498.unmap",
            study_uid="1.2.826.0.1.3680043.8.498.unmapstudy",
            modality=modality,
            rows=32,
            columns=32,
        )
        with pytest.raises(SegExportError, match="不默认 CT"):
            _build_source_images(src, 32, 32, 1)
        with pytest.raises(SegExportError, match="不默认 CT"):
            resolve_source_sop_class_uid(src, None)

    def test_pt_real_instances_without_uid_raises(self, tmp_path) -> None:
        """PT 真实实例但头无 SOPClassUID（空串）→ 走 raise 分支（非默认 CT）。"""
        src_dir = _dicom_dir_with_sop_class(tmp_path, PET)
        import pydicom

        # 抹掉 SOPClassUID（旧持久化 JSON 无此键同理——空串）
        items = []
        for p in sorted(src_dir.glob("*.dcm")):
            ds = pydicom.dcmread(str(p))
            ds.SOPClassUID = ""
            items.append((p, ds))
        persisted = extract_source_instances(items, rows=32, cols=32)
        assert persisted["instances"][0]["sop_class_uid"] == ""
        source = source_info_from_persisted(
            persisted, "1.2.826.0.1.3680043.8.498.sopcls", "1.2.826.0.1.3680043.8.498.123", "PT"
        )
        mask_path = _make_mask(tmp_path / "mask.nii.gz")
        with pytest.raises(SegExportError, match="不默认 CT"):
            export_seg(mask_path, source, tmp_path / "seg.dcm")

    def test_verify_sop_class_mismatch_reports(self, tmp_path) -> None:
        """期望 SOP Class 与 SEG 引用不符 → verify 报错（逐字比对生效）。"""
        # 用单帧 PET 源（Enhanced MR 是多帧 IOD，导出阶段就被 highdicom 拒绝）
        src_dir = _dicom_dir_with_sop_class(tmp_path, PET)
        persisted = _persisted_from_dir(src_dir)
        source = source_info_from_persisted(
            persisted, "1.2.826.0.1.3680043.8.498.sopcls", "1.2.826.0.1.3680043.8.498.123", "MR"
        )
        mask_path = _make_mask(tmp_path / "mask.nii.gz")
        seg_path = export_seg(mask_path, source, tmp_path / "seg.dcm")
        rep = verify_seg(
            seg_path,
            source_mask_path=mask_path,
            expected_source_sop_class_uid="1.2.840.10008.5.1.4.1.1.999",
        )
        assert not rep.ok
        assert any("源 SOP Class" in e for e in rep.errors)


class TestSyntheticFallback:
    """合成源（无实例）DX/CR/MR/CT 走映射表 fallback。"""

    @pytest.mark.parametrize(
        "modality,sop_class",
        [
            ("DX", "1.2.840.10008.5.1.4.1.1.1.1"),
            ("CR", "1.2.840.10008.5.1.4.1.1.1"),
            ("MR", "1.2.840.10008.5.1.4.1.1.4"),
            ("CT", "1.2.840.10008.5.1.4.1.1.2"),
            ("X-ray", "1.2.840.10008.5.1.4.1.1.1"),  # "X-ray" → CR 别名归一
        ],
    )
    def test_synthetic_fallback_mapped(self, modality: str, sop_class: str) -> None:
        src = SourceSeriesInfo(
            series_uid="1.2.826.0.1.3680043.8.498.fb",
            study_uid="1.2.826.0.1.3680043.8.498.fbstudy",
            modality=modality,
            rows=32,
            columns=32,
        )
        images = _build_source_images(src, 32, 32, 1)
        assert images[0].SOPClassUID == sop_class
