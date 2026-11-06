"""测试共享 fixture + 合成 DICOM/NIfTI/zip 工厂（M1）。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pytest
import SimpleITK as sitk  # noqa: N813 — sitk 官方惯用别名

from bodydonor.config import Settings


@pytest.fixture
def settings() -> Settings:
    """默认 settings（on_prem，标准白名单）。"""
    return Settings()


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data" / "BodyDonor"


def make_nifti(
    path: Path,
    shape: tuple[int, int, int] = (64, 64, 40),
    spacing: tuple[float, float, float] = (1.0, 1.0, 1.0),
    hu_min: int = -1000,
    hu_max: int = 500,
) -> Path:
    """写一个合成 CT NIfTI（轴位、正交、毫米）。"""
    rng = np.random.default_rng(42)
    arr = rng.integers(hu_min, hu_max, size=shape, dtype=np.int16)
    img = sitk.GetImageFromArray(arr)
    img.SetSpacing(spacing)
    path.parent.mkdir(parents=True, exist_ok=True)
    sitk.WriteImage(img, str(path))
    return path


def make_dicom_dataset(
    series_uid: str,
    study_uid: str = "1.2.826.0.1.3680043.8.498.123",
    modality: str = "CT",
    num_slices: int = 40,
    image_type: tuple[str, ...] = ("ORIGINAL", "PRIMARY", "AXIAL"),
    thickness: float = 1.0,
    iop: tuple[float, ...] = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0),  # 轴位
    burned_in: str = "NO",
    patient_name: str = "TEST^PATIENT",
    rescale_slope: float = 1.0,
    rescale_intercept: float = -1024.0,
    rows: int = 64,
    cols: int = 64,
):
    """构造一个合成 DICOM Dataset（pydicom FileDataset，含像素）。"""
    import pydicom
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT Image Storage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.Modality = modality
    ds.ImageType = list(image_type)
    ds.SliceThickness = thickness
    ds.ImageOrientationPatient = list(iop)
    ds.BurnedInAnnotation = burned_in
    ds.PatientName = patient_name
    ds.PatientID = "PID-12345"
    ds.AccessionNumber = "ACC-67890"
    ds.RescaleSlope = rescale_slope
    ds.RescaleIntercept = rescale_intercept
    ds.PixelSpacing = [1.0, 1.0]
    ds.Rows = rows
    ds.Columns = cols
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 1  # signed
    rng = np.random.default_rng(7)
    ds.PixelData = rng.integers(0, 2000, size=(rows, cols), dtype="<i2").tobytes()
    return pydicom.dataset.FileDataset(
        None, ds, file_meta=meta, preamble=b"\x00" * 128
    )


def save_dicom(ds, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(str(path), enforce_file_format=True)
    return path


def make_dicom_folder(
    root: Path,
    n_series: int = 2,
    slices_per_series: int = 40,
    **kwargs,
) -> Path:
    """生成多序列 DICOM 文件夹（每个 series 一个子目录）。"""
    for si in range(n_series):
        uid = f"1.2.826.0.1.3680043.8.498.1.{si + 1}"
        for i in range(slices_per_series):
            ds = make_dicom_dataset(series_uid=uid, **kwargs)
            ds.InstanceNumber = i + 1
            save_dicom(ds, root / f"series{si + 1}" / f"slice{i + 1:04d}.dcm")
    return root


def make_localizer(
    root: Path, series_uid: str = "1.2.826.0.1.3680043.8.498.2.1"
) -> Path:
    """生成一个 LOCALIZER 序列（2 层定位像）。"""
    for i in range(2):
        ds = make_dicom_dataset(
            series_uid=series_uid,
            image_type=("LOCALIZER", "PRIMARY", "AXIAL"),
        )
        ds.InstanceNumber = i + 1
        save_dicom(ds, root / "localizer" / f"loc{i + 1}.dcm")
    return root / "localizer"


def make_burned_folder(root: Path, slices: int = 40) -> Path:
    """生成像素烧录声明 YES 的 DICOM 文件夹。"""
    uid = "1.2.826.0.1.3680043.8.498.3.1"
    for i in range(slices):
        ds = make_dicom_dataset(series_uid=uid, burned_in="YES")
        ds.InstanceNumber = i + 1
        save_dicom(ds, root / f"burned{i + 1:04d}.dcm")
    return root


def zip_bytes(members: dict[str, bytes]) -> bytes:
    """内存 zip（测试用）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def dicom_folder_zip(root: Path) -> bytes:
    """把 DICOM 文件夹打成内存 zip。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(root.rglob("*.dcm")):
            zf.writestr(str(p.relative_to(root)), p.read_bytes())
    return buf.getvalue()
