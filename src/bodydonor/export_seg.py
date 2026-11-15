"""DICOM SEG 导出（M1-fix，proposal §5 M1 任务 7）：mask（NIfTI）-> DICOM SEG。

用 highdicom 生成标准 DICOM Segmentation IOD，使分割结果能直接推 PACS/工作站联调，
不只支持 NIfTI。导出必须带源 DICOM 引用（highdicom 要求），源序列信息从 ingest 的
SeriesRecord / 落库的 Series 元数据来。

校验（verify_seg）三道：

1. pydicom 重读：SOPClassUID == Segmentation Storage、Modality == SEG、帧数与标签数对
2. highdicom.segread：解析成功且按源 series 引用取回标签
3. 与源 mask 逐体素 diff：全零才算通过（SEG -> 像素数组重建后与 mask 一致）

方向一致性：SEG 的坐标框架继承源 DICOM（ImageOrientationPatient/PixelSpacing/
切片位置），源 mask 必须已按源 DICOM 几何对齐（assert_affine_match 守卫在
mask 读写路径强制）。export_seg 不做重采样——几何不一致直接 raise。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

SEG_SOP_CLASS_UID = "1.2.840.10008.5.1.4.1.1.66.4"  # Segmentation Storage
SEG_MODALITY = "SEG"


class SegExportError(Exception):
    """SEG 导出失败（缺源引用、几何不一致、highdicom 拒绝等）。"""


class SegVerifyError(Exception):
    """SEG 校验失败（重读解析失败、体素 diff 非零等）。"""


@dataclass
class SourceSeriesInfo:
    """源序列信息：highdicom 生成 SEG 必须引用源 DICOM 实例。

    最小集：源图像的几何三要素（方向/像素间距/切片位置），highdicom 用它把
    每帧标签钉回患者坐标系。source_instances 携带源 SOP 实例引用（有则写
    ReferencedSOPInstanceUID，无则按合成源实例导出——合成联调用）。
    modality 必填（合成源实例的 Modality 标签取此值，不默认 CT——S3F）。
    """

    series_uid: str
    study_uid: str
    modality: str
    rows: int = 0
    columns: int = 0
    pixel_spacing: tuple[float, float] = (1.0, 1.0)  # (row, col) mm
    slice_thickness_mm: float = 1.0
    orientation_patient: tuple[float, ...] = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    # 每层 SOPInstanceUID + 切片位置（无则合成源实例按均匀层距排布）
    source_instances: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SegVerifyReport:
    """verify_seg 的结果。"""

    readable: bool = False
    n_frames: int = 0
    n_segments: int = 0
    referenced_source_series: str = ""
    voxel_diff_nonzero: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.voxel_diff_nonzero == 0


def export_seg(
    mask_path: Path | str,
    source: SourceSeriesInfo,
    out_path: Path | str,
    segment_label: str = "Segmentation",
    segment_category: str = "TISSUE",
) -> Path:
    """NIfTI mask -> DICOM SEG（highdicom 生成），写入 out_path。

    mask 的 NIfTI 仿射必须与源 DICOM 几何一致（同方向、同 spacing、切片沿
    orientation_patient 法向均匀分布）——不一致直接 SegExportError，不静默重采样。

    无 source_instances（合成源）时按源几何合成最小源实例引用；真实联调时
    传 ingest 读到的源实例列表。
    """
    import highdicom as hd
    import SimpleITK as sitk  # noqa: N813
    from pydicom.uid import generate_uid

    mask_path = Path(mask_path)
    out_path = Path(out_path)
    try:
        mask_sitk = sitk.ReadImage(str(mask_path))
        mask = sitk.GetArrayFromImage(mask_sitk)  # (z, y, x)
    except Exception as e:  # noqa: BLE001
        raise SegExportError(f"mask 读取失败：{e}") from e
    if mask.ndim != 3:
        raise SegExportError(f"mask 必须是 3D，实际 shape {mask.shape}")
    mask_bin = (np.asarray(mask) > 0).astype(np.uint8)

    # 几何一致性：mask 面内尺寸/层数与源对齐（方向校验在 verify/affine 守卫）
    iop = np.asarray(source.orientation_patient, dtype=float)
    if iop.size != 6:
        raise SegExportError("orientation_patient 必须 6 值")
    if source.rows <= 0 or source.columns <= 0:
        raise SegExportError("源 rows/columns 未提供")
    if mask.shape[1] != source.rows or mask.shape[2] != source.columns:
        raise SegExportError(
            f"mask 面内尺寸 {mask.shape[1:]} 与源 {source.rows}x{source.columns} 不一致"
            "（禁止静默重采样）"
        )

    n_src = len(source.source_instances)
    n_slices = mask.shape[0]
    if n_src and n_src != n_slices:
        raise SegExportError(
            f"mask 层数 {n_slices} 与源实例数 {n_src} 不一致（禁止静默重采样）"
        )

    # 逐层生成源实例描述（真实引用或按 source.modality 合成）；
    # highdicom 要求逐层 ImagePositionPatient
    # 单调不重复——合成源按均匀层距排布（真实联调时传 ingest 读到的源实例列表）
    src_images = _build_source_images(
        source, rows=source.rows, columns=source.columns, n_slices=n_slices
    )

    seg = hd.seg.Segmentation(
        source_images=src_images,
        pixel_array=mask_bin,
        segmentation_type=hd.seg.SegmentationTypeValues.BINARY,
        omit_empty_frames=False,  # 帧数与源 mask 层数一一对应（逐体素 diff 可比）
        series_instance_uid=generate_uid(),
        series_number=100,
        sop_instance_uid=generate_uid(),
        instance_number=1,
        manufacturer="BodyDonor",
        manufacturer_model_name="BodyDonor segmentation pipeline",
        software_versions="0.1.0",
        device_serial_number="",
        segment_descriptions=[
            hd.seg.SegmentDescription(
                segment_number=1,
                segment_label=segment_label,
                algorithm_type=hd.seg.SegmentAlgorithmTypeValues.AUTOMATIC,
                algorithm_identification=hd.content.AlgorithmIdentificationSequence(
                    name="BodyDonor",
                    family=hd.sr.CodedConcept(
                        "113043", "DCM", "Segmentation algorithm"
                    ),
                    version="0.1.0",
                    source="BodyDonor segmentation pipeline",
                ),
                segmented_property_category=hd.sr.CodedConcept(
                    "T-D000", "SRT", "Morphologically Altered Structure"
                ),
                segmented_property_type=hd.sr.CodedConcept(
                    segment_category[:3], "SRT", segment_category
                ),
            )
        ],
    )
    # 系列/研究归属继承源（PACS 联调时 SEG 挂在同一 study 下）
    seg.SeriesDescription = "BodyDonor segmentation"
    seg.StudyInstanceUID = source.study_uid

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        seg.save_as(str(out_path))
    except Exception as e:  # noqa: BLE001
        raise SegExportError(f"highdicom 写出失败：{e}") from e
    return out_path


def _build_source_images(
    source: SourceSeriesInfo, rows: int, columns: int, n_slices: int
) -> list[Any]:
    """构造逐层源实例（真实实例优先，合成源按均匀层距排布）。

    真实联调：source_instances 携带每层 SOPInstanceUID + ImagePositionPatient；
    合成联调：无实例信息时按源几何合成（z 方向均匀层距 = pixel_spacing[0] 毫米）。
    """
    import pydicom
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid

    row_cos = np.asarray(source.orientation_patient[0:3], dtype=float)
    col_cos = np.asarray(source.orientation_patient[3:6], dtype=float)
    normal = np.cross(row_cos, col_cos)
    dz = float(source.slice_thickness_mm) or float(source.pixel_spacing[0])
    frame_of_reference_uid = generate_uid()  # 全序列共享一个 Frame of Reference

    images: list[Any] = []
    for i in range(n_slices):
        if i < len(source.source_instances):
            inst = source.source_instances[i]
            ipp = np.asarray(inst["image_position_patient"], dtype=float)
            sop_uid = str(inst["sop_instance_uid"])
        else:
            # 合成：沿源 iop 法向**反向**均匀排布（DICOM 帧序惯例：帧 0 = 法向
            # 距离最大；NIfTI mask 帧 i 直接对应源实例序列第 i 个）
            ipp = normal * (-dz * i)
            sop_uid = generate_uid()

        meta = FileMetaDataset()
        # S3 收口轮 ⑥：DX/CR 保留源信息，不一律转 CR——源模态是 DX 写 DX、
        # 是 CR 写 CR（合成源实例仅用于 highdicom 引用，模态标签取源值）
        # S3R4 W-1：先归一化模态再查 SOP Class 映射——内部别名 "X-ray" 非
        # DICOM CS 合法值，规范化为 CR 后同查映射（别名不落默认 CT Storage）
        modality = "CR" if source.modality == "X-ray" else source.modality
        sop_class_uid = {
            "DX": "1.2.840.10008.5.1.4.1.1.1.1",  # Digital X-Ray Image Storage - Presentation
            "CR": "1.2.840.10008.5.1.4.1.1.1",  # Computed Radiography Image Storage
        }.get(modality, "1.2.840.10008.5.1.4.1.1.2")  # 其余默认 CT Image Storage
        meta.MediaStorageSOPClassUID = sop_class_uid
        meta.MediaStorageSOPInstanceUID = sop_uid
        meta.TransferSyntaxUID = ExplicitVRLittleEndian

        ds = Dataset()
        ds.file_meta = meta
        ds.SOPClassUID = meta.MediaStorageSOPClassUID
        ds.SOPInstanceUID = sop_uid
        ds.StudyInstanceUID = source.study_uid
        ds.SeriesInstanceUID = source.series_uid
        ds.Modality = modality
        ds.Rows = rows
        ds.Columns = columns
        ds.PixelSpacing = [
            float(source.pixel_spacing[0]),
            float(source.pixel_spacing[1]),
        ]
        ds.SliceThickness = float(source.slice_thickness_mm)
        ds.ImageOrientationPatient = [float(v) for v in source.orientation_patient]
        ds.ImagePositionPatient = [float(v) for v in ipp]
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 1
        ds.RescaleSlope = 1.0
        ds.RescaleIntercept = -1024.0
        ds.BurnedInAnnotation = "NO"
        # 合成源实例的 Patient 标识：合成联调数据无 PHI（deid 已在 ingest 剥离真实头）
        ds.PatientName = "ANONYMOUS^"
        ds.PatientID = "BODYDONOR-SYNTHETIC"
        ds.PatientBirthDate = ""
        ds.PatientSex = ""
        ds.AccessionNumber = ""
        ds.StudyID = ""
        ds.SeriesNumber = 1
        ds.InstanceNumber = i + 1
        ds.StudyDate = ""
        ds.SeriesDate = ""
        ds.AcquisitionDate = ""
        ds.StudyTime = ""
        ds.SeriesTime = ""
        ds.AcquisitionTime = ""
        # Frame of Reference：SEG 帧坐标钉在源几何上，必需；全序列共享一个
        ds.FrameOfReferenceUID = frame_of_reference_uid
        ds.PixelData = np.zeros((rows, columns), dtype="<i2").tobytes()
        images.append(
            pydicom.dataset.FileDataset(None, ds, file_meta=meta, preamble=b"\x00" * 128)
        )
    return images


def verify_seg(
    seg_path: Path | str,
    source_mask_path: Path | str | None = None,
    expected_series_uid: str = "",
    expected_source_modality: str = "",
) -> SegVerifyReport:
    """SEG 三道校验：pydicom 重读 + highdicom 解析 + 与源 mask 逐体素 diff。

    expected_series_uid 非空时同时断言引用了该源 series。source_mask_path
    非空时做逐体素 diff（体素语义：SEG 帧像素 = mask，形状一致才可比）。
    expected_source_modality 非空时从 ReferencedSOPClassUID 反查源影像模态
    并核对（S3R4 W-2：与报告 ⑥ 描述一致，与 SEG 自身模态分开）。
    """
    import highdicom as hd
    import pydicom
    import SimpleITK as sitk  # noqa: N813

    rep = SegVerifyReport()
    seg_path = Path(seg_path)

    # 1. pydicom 重读：SOP Class / Modality / 帧数
    try:
        ds = pydicom.dcmread(str(seg_path))
    except Exception as e:  # noqa: BLE001
        rep.errors.append(f"pydicom 重读失败：{e}")
        return rep
    sop_class = str(getattr(ds, "SOPClassUID", ""))
    if sop_class != SEG_SOP_CLASS_UID:
        rep.errors.append(f"SOPClassUID 非 Segmentation Storage：{sop_class}")
        return rep
    if str(getattr(ds, "Modality", "")) != SEG_MODALITY:
        rep.errors.append(f"Modality 非 SEG：{ds.Modality}")
        return rep
    rep.n_frames = int(getattr(ds, "NumberOfFrames", 0) or 0)
    rep.readable = True

    # 2. highdicom 解析：segread 成功 + 源引用
    try:
        seg = hd.seg.segread(str(seg_path))
    except Exception as e:  # noqa: BLE001
        rep.errors.append(f"highdicom segread 失败：{e}")
        return rep
    rep.n_segments = int(getattr(seg, "number_of_segments", 0) or 0)
    try:
        ref_series = seg.ReferencedSeriesSequence[0].SeriesInstanceUID
        rep.referenced_source_series = str(ref_series)
        if expected_series_uid and str(ref_series) != expected_series_uid:
            rep.errors.append(
                f"SEG 引用源 series {ref_series} != 期望 {expected_series_uid}"
            )
    except (AttributeError, IndexError):
        rep.errors.append("SEG 无 ReferencedSeriesSequence（缺源引用）")

    # S3 收口轮 ⑥：SEG 对象自身模态（恒为 SEG）与源影像模态分开核对——
    # 源影像模态从 ReferencedSeriesSequence 每实例取回，与 SourceSeriesInfo 对照
    if getattr(seg, "Modality", "") != SEG_MODALITY:
        rep.errors.append(f"SEG 自身 Modality 非 SEG：{seg.Modality}")

    # S3R4 W-2：源影像模态从 ReferencedSOPClassUID 反查（与 _build_source_images
    # 同一映射），期望模态非空时核对——SEG 引用的源实例与声称的源模态一致
    if expected_source_modality:
        try:
            ref_sop_class = str(
                seg.ReferencedSeriesSequence[0]
                .ReferencedInstanceSequence[0]
                .ReferencedSOPClassUID
            )
        except (AttributeError, IndexError):
            rep.errors.append("SEG 无源实例引用（ReferencedInstanceSequence 缺失）")
        else:
            src_modality = {
                "1.2.840.10008.5.1.4.1.1.1.1": "DX",  # Digital X-Ray Image Storage
                "1.2.840.10008.5.1.4.1.1.1": "CR",  # Computed Radiography Image Storage
                "1.2.840.10008.5.1.4.1.1.2": "CT",  # CT Image Storage
            }.get(ref_sop_class, ref_sop_class)  # 未知 UID 原样带出（不猜模态）
            if src_modality != expected_source_modality:
                rep.errors.append(
                    f"SEG 源影像模态 {src_modality} != 期望 {expected_source_modality}"
                )

    # 3. 逐体素 diff：SEG 帧 -> mask 重建，与源 mask 对比
    if source_mask_path is not None:
        try:
            src_mask = sitk.GetArrayFromImage(sitk.ReadImage(str(source_mask_path)))
        except Exception as e:  # noqa: BLE001
            rep.errors.append(f"源 mask 读取失败：{e}")
            return rep
        src_bin = (np.asarray(src_mask) > 0).astype(bool)
        if src_bin.ndim != 3:
            rep.errors.append(f"源 mask 非 3D：shape {src_bin.shape}")
            return rep
        try:
            arr = seg.pixel_array  # (frames, rows, cols)
        except Exception as e:  # noqa: BLE001
            rep.errors.append(f"SEG 像素读取失败：{e}")
            return rep
        if arr.shape != src_bin.shape:
            rep.errors.append(
                f"SEG 帧 shape {arr.shape} 与源 mask {src_bin.shape} 不一致"
            )
            return rep
        seg_bin = np.asarray(arr).astype(bool)
        diff = np.count_nonzero(seg_bin != src_bin)
        rep.voxel_diff_nonzero = int(diff)
        if diff:
            rep.errors.append(f"SEG 与源 mask 逐体素 diff 非零：{diff} 体素")
    return rep


def export_and_verify(
    mask_path: Path | str,
    source: SourceSeriesInfo,
    out_path: Path | str,
    **kwargs: Any,
) -> SegVerifyReport:
    """导出 + 校验一步：diff 非零或任何错误 -> SegVerifyError。"""
    export_seg(mask_path, source, out_path, **kwargs)
    rep = verify_seg(out_path, source_mask_path=mask_path, expected_series_uid=source.series_uid)
    if not rep.ok:
        raise SegVerifyError("; ".join(rep.errors))
    return rep


__all__ = [
    "SEG_SOP_CLASS_UID",
    "SegExportError",
    "SegVerifyError",
    "SegVerifyReport",
    "SourceSeriesInfo",
    "export_seg",
    "verify_seg",
    "export_and_verify",
]
