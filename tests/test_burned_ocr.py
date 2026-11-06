"""M1-fix：像素级 OCR 烧录扫描测试（合成烧录文字图验证检出 + 目标序列识别 + 轴位不扫）。"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from bodydonor.burned_ocr import (
    is_ocr_target,
    ocr_pixel_burned_scan,
    ocr_scan_series,
)


def _make_sc_dataset(
    pixel_arr: np.ndarray,
    sop_class: str = "1.2.840.10008.5.1.4.1.1.7",
    series_description: str = "",
):
    """构造 Secondary Capture DICOM Dataset（含像素）。"""
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = sop_class
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.Rows, ds.Columns = pixel_arr.shape[:2]
    ds.PixelData = pixel_arr.tobytes()
    ds.SamplesPerPixel = 1 if pixel_arr.ndim == 2 else pixel_arr.shape[2]
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    if series_description:
        ds.SeriesDescription = series_description
    return ds


def _make_dose_report_pixels(text: str = "DOSE REPORT\nPatient ID: PID-12345") -> np.ndarray:
    """合成一张黑底白字烧录文字图（剂量单样式，512x512，OCR 可读）。"""
    img = Image.new("L", (512, 512), 0)
    d = ImageDraw.Draw(img)
    for i, line in enumerate(text.split("\n")):
        d.text((30, 30 + i * 40), line, fill=255)
    return np.array(img)


class TestOcrTarget:
    def test_secondary_capture_is_target(self) -> None:
        """SC SOP Class UID -> OCR 目标。"""
        ds = _make_sc_dataset(np.zeros((64, 64), dtype=np.uint8))
        assert is_ocr_target(ds) is True

    def test_dose_report_description_is_target(self) -> None:
        """SeriesDescription 含 dose report -> OCR 目标（即使 SOP Class 是 CT）。"""
        ds = _make_sc_dataset(
            np.zeros((64, 64), dtype=np.uint8),
            sop_class="1.2.840.10008.5.1.4.1.1.2",  # CT Image Storage
            series_description="Dose Report SR",
        )
        assert is_ocr_target(ds) is True

    def test_axial_ct_not_target(self) -> None:
        """普通轴位 CT（CT SOP Class、无 dose 关键词）-> 不扫。"""
        ds = _make_sc_dataset(
            np.zeros((64, 64), dtype=np.uint8),
            sop_class="1.2.840.10008.5.1.4.1.1.2",
        )
        ds.ImageType = ["ORIGINAL", "PRIMARY", "AXIAL"]
        ds.SeriesDescription = "Axial Chest 1.0mm"
        assert is_ocr_target(ds) is False


class TestOcrDetection:
    def test_synthetic_burned_text_detected(self) -> None:
        """合成烧录文字图（剂量单）-> OCR 检出（detected=True + 敏感词命中）。"""
        pixels = _make_dose_report_pixels()
        ds = _make_sc_dataset(pixels)  # SC -> OCR 目标
        r = ocr_pixel_burned_scan(ds)
        assert r.scanned is True
        assert r.detected is True
        assert len(r.texts) > 0
        assert any(
            SENSITIVE in t.upper()
            for t in r.texts
            for SENSITIVE in ("DOSE", "PATIENT", "ID")
        )

    def test_clean_sc_not_detected(self) -> None:
        """空白 SC 图（无文字）-> OCR 扫描完成，未检出。"""
        ds = _make_sc_dataset(np.zeros((512, 512), dtype=np.uint8))
        r = ocr_pixel_burned_scan(ds)
        assert r.scanned is True
        assert r.detected is False
        assert r.texts == []

    def test_axial_not_scanned(self) -> None:
        """普通轴位序列 -> scanned=False（不跑 OCR，不产生 OCR 记录）。"""
        ds = _make_sc_dataset(
            _make_dose_report_pixels(),
            sop_class="1.2.840.10008.5.1.4.1.1.2",
        )
        ds.ImageType = ["ORIGINAL", "PRIMARY", "AXIAL"]
        r = ocr_pixel_burned_scan(ds)
        assert r.scanned is False
        assert r.detected is False


class TestOcrSeries:
    def test_series_any_instance_detected(self) -> None:
        """Series 级：任一实例检出即拒收（detected=True）。"""
        pixels = _make_dose_report_pixels()
        clean = _make_sc_dataset(np.zeros((512, 512), dtype=np.uint8))
        burned = _make_sc_dataset(pixels)
        r = ocr_scan_series([clean, burned])
        assert r.scanned is True
        assert r.detected is True

    def test_series_no_target(self) -> None:
        """全轴位序列（无目标实例）-> 无 OCR 记录。"""
        ds = _make_sc_dataset(
            np.zeros((64, 64), dtype=np.uint8),
            sop_class="1.2.840.10008.5.1.4.1.1.2",
        )
        ds.ImageType = ["ORIGINAL", "PRIMARY", "AXIAL"]
        r = ocr_scan_series([ds])
        assert r.scanned is False


class TestIngestOcrRejection:
    def test_dose_report_series_rejected_by_ingest(self, tmp_path) -> None:  # noqa: ANN001
        """端到端：剂量报告序列（Tag UNKNOWN）OCR 检出 -> ingest 拒收（skipped）。"""
        from pydicom.dataset import Dataset, FileMetaDataset
        from pydicom.uid import ExplicitVRLittleEndian, generate_uid

        pixels = _make_dose_report_pixels()
        root = tmp_path / "dose"
        root.mkdir(parents=True)
        for i in range(2):
            meta = FileMetaDataset()
            meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT
            meta.MediaStorageSOPInstanceUID = generate_uid()
            meta.TransferSyntaxUID = ExplicitVRLittleEndian
            ds = Dataset()
            ds.file_meta = meta
            ds.SOPClassUID = meta.MediaStorageSOPClassUID
            ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
            ds.StudyInstanceUID = "1.2.826.0.1.3680043.8.498.123"
            ds.SeriesInstanceUID = "1.2.826.0.1.3680043.8.498.dose"
            ds.Modality = "CT"
            ds.SeriesDescription = "Dose Report"  # OCR 目标序列
            ds.Rows, ds.Columns = 512, 512
            ds.PixelData = pixels.tobytes()
            ds.SamplesPerPixel = 1
            ds.PhotometricInterpretation = "MONOCHROME2"
            ds.BitsAllocated = 8
            ds.BitsStored = 8
            ds.HighBit = 7
            ds.PixelRepresentation = 0
            # BurnedInAnnotation 缺失（UNKNOWN）—— Tag 第一道关不拦，OCR 第二道关拦
            ds.save_as(str(root / f"dose{i}.dcm"), enforce_file_format=True)

        from bodydonor.extract_guard import ExtractLimits
        from bodydonor.ingest import ingest

        report = ingest(root, limits=ExtractLimits())
        assert report.n_series == 0
        assert report.n_skipped == 2
        assert all("OCR" in sk["reason"] for sk in report.skipped)

    def test_axial_series_untouched_by_ocr(self, tmp_path) -> None:  # noqa: ANN001
        """对照：普通轴位序列（无 dose/SC 特征）不跑 OCR，正常导入。"""
        from tests.factories import make_dicom_folder

        from bodydonor.extract_guard import ExtractLimits
        from bodydonor.ingest import ingest

        root = make_dicom_folder(tmp_path / "axial", n_series=1, slices_per_series=40)
        report = ingest(root, limits=ExtractLimits())
        assert report.n_series == 1
        assert report.n_skipped == 0
        assert report.series[0].ocr_result is None  # 非 OCR 目标，无扫描记录
