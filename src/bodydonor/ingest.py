"""Import Bundle：ingest(path_or_stream) -> [Series] 单一入口（M1，proposal §5）。

三种输入（MVP 支持）：

| 输入形态 | 说明 |
|---|---|
| 单个 NIfTI（.nii/.nii.gz） | 直接入库，单元素列表 + 几何校验（警告） |
| DICOM 文件夹或 zip | 自动按 SeriesInstanceUID 拆分为多个 Series |
| 多序列批量（文件夹树或 zip） | 递归扫描，每个 Series 独立成一条记录 |

不做（v1.1 Ingest 插件）：文件夹监听、Orthanc DICOMweb、PACS C-FIND。

流程（proposal §6 第 1–2 阶段）：
De-ID 与像素烧录检测（第一步）→ 读取（文件夹/zip/NIfTI 分派）→ 按 SeriesInstanceUID
拆分 → Series 元数据（层厚/方向/层数/HU 范围）→ ImportReport（自检门）→ 落库。

文件不落像素（存储 NIfTI 转换产物）；DICOM 头 PHI 在读取时即剥离（deid.deid_dataset）。
"""

from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
import SimpleITK as sitk  # noqa: N813 — sitk 是官方惯用别名

from .burned_ocr import OcrResult, is_ocr_target, ocr_pixel_burned_scan
from .deid import DEID_TAG_KEYWORDS, deid_dataset, inspect_dicom
from .extract_guard import ExtractLimitError, ExtractLimits, safe_open_zip
from .series_selector import SeriesCandidate, apply_rules

# 支持的输入后缀
NIFTI_SUFFIXES = {".nii", ".gz"}  # .nii 与 .nii.gz（.gz 仅在 .nii.gz 时由名称二次确认）
NIFTI_NAME_SUFFIXES = (".nii", ".nii.gz")
DICOM_SUFFIXES = {".dcm", ".dicom", ""}
ZIP_SUFFIXES = {".zip"}

# zip 流式解压临时目录（M1-fix：原生盘，不落 /tmp 内存盘；可 env 覆盖）
ZIP_TMP_DIR_ENV = "BODYDONOR_ZIP_TMP_DIR"
DEFAULT_ZIP_TMP_DIR = Path.home() / "data" / "BodyDonor" / "tmp"

# 流式解压单文件读取块
UNZIP_CHUNK = 1 << 20  # 1 MiB


def _zip_tmp_dir() -> Path:
    """zip 流式解压的临时根目录：~/data/BodyDonor/tmp（原生盘），env 可覆盖。"""
    env = os.environ.get(ZIP_TMP_DIR_ENV)
    return Path(env) if env else DEFAULT_ZIP_TMP_DIR


def _is_nifti(p: Path) -> bool:
    return p.name.lower().endswith(NIFTI_NAME_SUFFIXES)

# 文件数 / 扫描上限（防递归失控；与 extract_limits.max_file_count 独立）
MAX_SCAN_FILES = 50_000


class IngestError(Exception):
    """导入失败（无支持的输入、烧录像素拒收等）。"""


@dataclass
class SeriesRecord:
    """ingest 产出的一条 Series（未落库；落库由 pipeline/CLI 完成）。"""

    series_id: str  # DICOM SeriesInstanceUID 或 NIfTI 文件名哈希
    case_key: str  # 归组键：DICOM StudyInstanceUID 或 NIfTI stem
    modality: str | None = None
    num_slices: int = 0
    slice_thickness_mm: float | None = None
    orientation: str = "unknown"
    hu_range: tuple[int, int] | None = None
    image_type: list[str] = field(default_factory=list)
    burned_in_annotation: str = "UNKNOWN"
    ocr_result: OcrResult | None = None  # M1-fix：像素级 OCR 扫描结果（目标序列才扫）
    deid_removed_count: int = 0
    deid_warnings: list[str] = field(default_factory=list)
    geom_warnings: list[str] = field(default_factory=list)
    reorientation: str = ""  # M1-fix：RAS+ 重排结果（落盘时写明方向变化）
    source_path: str = ""  # 输入内相对路径（不泄露本地真实姓名/路径之外的 PHI）
    pixel_data: Any = None  # sitk.Image；落库时转 NIfTI，不进 AgentContext
    # M2-5(a)：源 DICOM 实例信息（逐层 SOP UID + 几何三要素）。commit 时持久化
    # 进 Series.source_instances_json；export_seg 用它写真实源实例引用。
    # NIfTI 输入无此信息（合成源实例 fallback）。不含像素（不进 AgentContext）。
    source_instances: dict[str, Any] | None = None

    def candidate(self) -> SeriesCandidate:
        """转成选择器候选。"""
        return SeriesCandidate(
            series_id=self.series_id,
            modality=self.modality,
            image_type=list(self.image_type),
            num_slices=self.num_slices,
            orientation=self.orientation,
            slice_thickness_mm=self.slice_thickness_mm,
        )

    def to_report(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "case_key": self.case_key,
            "modality": self.modality,
            "num_slices": self.num_slices,
            "slice_thickness_mm": self.slice_thickness_mm,
            "orientation": self.orientation,
            "hu_range": list(self.hu_range) if self.hu_range else None,
            "image_type": list(self.image_type),
            "burned_in_annotation": self.burned_in_annotation,
            "ocr": self.ocr_result.to_report() if self.ocr_result else None,
            "deid_removed_count": self.deid_removed_count,
            "deid_warnings": list(self.deid_warnings),
            "geom_warnings": list(self.geom_warnings),
            "reorientation": self.reorientation,
            "source_path": self.source_path,
            "has_source_instances": self.source_instances is not None,
        }


@dataclass
class ImportReport:
    """导入报告（自检门，proposal §5）：先出报告再开跑。"""

    n_series: int = 0
    n_skipped: int = 0
    n_files_scanned: int = 0
    skipped: list[dict[str, str]] = field(default_factory=list)  # {file, reason}
    series: list[SeriesRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    input_kind: str = ""  # nifti | dicom_folder | dicom_zip

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_kind": self.input_kind,
            "n_files_scanned": self.n_files_scanned,
            "n_series": self.n_series,
            "n_skipped": self.n_skipped,
            "skipped": list(self.skipped),
            "warnings": list(self.warnings),
            "series": [s.to_report() for s in self.series],
        }


# ---------------------------------------------------------------------------
# 读取分派
# ---------------------------------------------------------------------------

def _is_zip(p: Path) -> bool:
    return p.suffix.lower() == ".zip"


def _looks_like_dicom(path: Path) -> bool:
    """后缀 .dcm/.dicom 直接算；无后缀读 132 字节 magic 'DICM' 判定。"""
    if path.suffix.lower() in (".dcm", ".dicom"):
        return True
    try:
        with path.open("rb") as f:
            f.seek(128)
            return f.read(4) == b"DICM"
    except OSError:
        return False


def ingest(
    path_or_stream: Path | BinaryIO | bytes,
    limits: ExtractLimits | None = None,
    source_name: str = "",
    recursive: bool = True,
    modality: str | None = None,
) -> ImportReport:
    """Import Bundle 单一入口：三种输入 -> ImportReport（含 [SeriesRecord]）。

    - Path 指向 NIfTI 文件 / DICOM 文件夹 / zip（自动分派）
    - BinaryIO/bytes 视为 zip 流（API 上传通道）
    - recursive：文件夹树递归扫描（多序列批量）
    - modality：NIfTI 输入必填（NIfTI 头无模态信息，不默认按 CT——S3F）；
      DICOM 路径以头内 Modality 为准，忽略本参数
    """
    limits = limits or ExtractLimits()
    if isinstance(path_or_stream, (str, bytes)) and not isinstance(path_or_stream, Path):
        data = path_or_stream if isinstance(path_or_stream, bytes) else path_or_stream.read()
        return _ingest_zip_stream(
            io.BytesIO(data), limits, source_name=source_name, modality=modality
        )
    if isinstance(path_or_stream, Path):
        p = path_or_stream
        if _is_zip(p):
            return _ingest_zip_stream(
                p, limits, source_name=source_name or p.name, modality=modality
            )
        if p.is_dir():
            return _ingest_folder(p, limits, recursive=recursive)
        if _is_nifti(p):
            return _ingest_nifti_file(p, limits, modality=modality)
        if _looks_like_dicom(p):
            # 单个 DICOM 文件 -> 当作单文件文件夹处理
            return _ingest_folder(p.parent, limits, recursive=False, extra_files=[p])
        raise IngestError(f"不支持的输入：{p}（期望 .nii/.nii.gz、DICOM 文件夹或 .zip）")
    # BinaryIO
    return _ingest_zip_stream(path_or_stream, limits, source_name=source_name, modality=modality)


# ---------------------------------------------------------------------------
# NIfTI
# ---------------------------------------------------------------------------

_NO_MODALITY = "unknown"  # app 演示路径缺省占位（/agent-context/preview）


def _ingest_nifti_file(
    path: Path, limits: ExtractLimits, modality: str | None = None
) -> ImportReport:
    if not modality:
        raise IngestError(
            f"显式 modality 必填：{path.name}（NIfTI 头无模态信息，不默认按 CT；"
            "CLI --modality / API modality 参数）"
        )
    img = sitk.ReadImage(str(path))
    rec = _nifti_to_record(img, path.name, modality=modality)
    report = ImportReport(n_series=1, n_files_scanned=1, input_kind="nifti")
    report.series.append(rec)
    return report


def _nifti_to_record(img: sitk.Image, name: str, modality: str) -> SeriesRecord:
    """sitk.Image -> SeriesRecord，带几何校验警告。"""
    import hashlib

    a = sitk.GetArrayFromImage(img)  # (z, y, x)
    geom_w: list[str] = []
    orientation = "unknown"
    spacing = tuple(float(v) for v in img.GetSpacing())

    # 几何校验复用 nifti_geom（走 nibabel 视角的 affine）——这里从 sitk direction/spacing 推导
    direction = img.GetDirection()  # 9 值（3D）行主序
    size = img.GetSize()
    # 各向异性
    positive = [s for s in spacing if s > 0]
    if len(positive) == 3 and max(positive) / min(positive) > 3.0:
        ratio = max(positive) / min(positive)
        geom_w.append(
            f"affine 各向异性：spacing {spacing} max/min = {ratio:.1f}x (> 3x)"
        )
    # 非正交：direction 矩阵列向量点积
    d = np.array(direction, dtype=float).reshape(3, 3).T  # 列 = 各轴方向
    norms = np.linalg.norm(d, axis=0)
    if np.all(norms > 1e-9):
        dn = d / norms
        max_dot = float(np.max(np.abs(dn.T @ dn - np.eye(3))))
        if max_dot > 0.02:
            geom_w.append(
                f"非正交方向：direction 列向量最大偏离 {max_dot:.3f} (> 0.02)"
            )
    # 非毫米：sitk 无单位元数据；单位判定交给 nibabel 视角（NIfTI 文件路径时用
    # nifti_geom.check_nifti_geometry 补充）。这里只做 spacing/方向两项。
    # 主方向
    axis_idx = int(np.argmax(np.abs(d[:, 2]))) if norms[2] > 1e-9 else -1
    axis_names = {0: "sagittal", 1: "coronal", 2: "axial"}
    orientation = axis_names.get(axis_idx, "oblique") if axis_idx >= 0 else "unknown"

    # NIfTI 文件才有单位元数据：读原始文件头判定单位（mm 期望）
    hu_range = (int(a.min()), int(a.max())) if a.size else None
    series_id = "nifti-" + hashlib.sha256(name.encode()).hexdigest()[:8]
    return SeriesRecord(
        series_id=series_id,
        case_key=Path(name).stem.removesuffix(".nii"),
        modality=modality,
        num_slices=int(size[2]) if len(size) == 3 else int(a.shape[0]),
        slice_thickness_mm=spacing[2] if len(spacing) == 3 else None,
        orientation=orientation,
        hu_range=hu_range,
        image_type=[],
        geom_warnings=geom_w,
        source_path=name,
        pixel_data=img,
    )


# ---------------------------------------------------------------------------
# DICOM 文件夹
# ---------------------------------------------------------------------------

def _scan_folder(root: Path, recursive: bool, extra_files: list[Path]) -> list[Path]:
    files = list(extra_files)
    pattern = "**/*" if recursive else "*"
    for count, p in enumerate(sorted(root.glob(pattern)), start=1):
        if count > MAX_SCAN_FILES:
            raise IngestError(f"文件夹扫描超过 {MAX_SCAN_FILES} 个文件，中止")
        if p.is_file() and (p.suffix.lower() in DICOM_SUFFIXES or _looks_like_dicom(p)):
            files.append(p)
    return files


def _ingest_folder(
    root: Path, limits: ExtractLimits, recursive: bool = True, extra_files: list[Path] | None = None
) -> ImportReport:
    import pydicom

    files = _scan_folder(root, recursive, extra_files or [])
    report = ImportReport(n_files_scanned=len(files), input_kind="dicom_folder")

    by_series: dict[str, list[tuple[Path, Any]]] = {}
    for f in files:
        try:
            ds = pydicom.dcmread(str(f), force=False)
        except Exception as e:  # noqa: BLE001 — 单文件损坏不中断整批
            report.skipped.append({"file": f.name, "reason": f"读取失败: {type(e).__name__}"})
            report.n_skipped += 1
            continue
        # De-ID 第一步：像素烧录检测（YES 拒收该文件）+ PHI 剥离
        d = deid_dataset(ds)
        if not d.accepted:
            report.skipped.append(
                {"file": f.name, "reason": "像素烧录声明 YES（BurnedInAnnotation），拒收"}
            )
            report.n_skipped += 1
            continue
        uid = str(getattr(ds, "SeriesInstanceUID", "") or "")
        if not uid:
            report.skipped.append({"file": f.name, "reason": "缺 SeriesInstanceUID"})
            report.n_skipped += 1
            continue
        by_series.setdefault(uid, []).append((f, ds))

    for uid, items in by_series.items():
        rec = _build_dicom_series(uid, items, report)
        if rec is not None:
            report.series.append(rec)
            report.n_series += 1
    return report


def _build_dicom_series(
    uid: str,
    items: list[tuple[Path, Any]],
    report: ImportReport,
    requested_modality: str | None = None,
) -> SeriesRecord | None:

    first_ds = items[0][1]
    # 像素烧录/PHI 统计（全 series 一致；inspect 第一个已 deid，removed 记在文件级）
    bia = str(getattr(first_ds, "BurnedInAnnotation", "UNKNOWN")).strip().upper()
    if bia not in ("YES", "NO"):
        bia = "UNKNOWN"
    deid_removed = sum(1 for _, ds in items for kw in _DEID_KW if getattr(ds, kw, "") == "")

    # M1-fix 第二道关：像素级 OCR 烧录扫描（只扫剂量报告/二次截图类序列）。
    # Tag 检查（上面 bia）是第一道关，OCR 是第二道关；任一不通过即拒收。
    ocr = ocr_scan_series_or_none(items)
    if ocr is not None and ocr.detected:
        # OCR 检出疑似烧录文字：拒收（文件级跳过，不进下游），不改写原数据
        for f, _ in items:
            report.skipped.append(
                {"file": f.name, "reason": f"OCR 检出疑似烧录文字（{ocr.texts[:2]}），拒收"}
            )
            report.n_skipped += 1
        return None

    # Series 元数据（W4：缺失标签 -> Python None，不是字符串 "None"）
    raw = getattr(first_ds, "Modality", None)
    modality = (str(raw).strip() or None) if raw is not None else None
    if modality and requested_modality and modality != requested_modality:
        # W3：DICOM 头与显式入参冲突 -> 报告 warning（不静默以头为准）
        report.warnings.append(
            f"modality 冲突：--modality {requested_modality} 被忽略，"
            f"DICOM 头内 Modality={modality}（series {uid} 以头为准）"
        )
    image_type = [str(v) for v in getattr(first_ds, "ImageType", [])] or []
    thickness = getattr(first_ds, "SliceThickness", None)
    thickness_mm = float(thickness) if thickness else None

    # 方向：ImageOrientationPatient -> 主轴
    iop = getattr(first_ds, "ImageOrientationPatient", None)
    orientation = "unknown"
    if iop is not None and len(iop) >= 6:
        row = np.array([float(v) for v in iop[0:3]])
        col = np.array([float(v) for v in iop[3:6]])
        normal = np.cross(row, col)  # 切面法向
        abs_n = np.abs(normal)
        if abs_n.sum() > 1e-9:
            if abs_n[2] / abs_n.sum() > 0.9:
                orientation = "axial"
            elif abs_n[0] / abs_n.sum() > 0.9:
                orientation = "sagittal"
            elif abs_n[1] / abs_n.sum() > 0.9:
                orientation = "coronal"
            else:
                orientation = "oblique"

    # HU 范围：读首个含像素的文件
    hu_range = None
    pixel_img = None
    for _, ds in items:
        if getattr(ds, "pixel_array", None) is None:
            continue
        try:
            arr = ds.pixel_array
        except Exception:  # noqa: BLE001
            continue
        slope = float(getattr(ds, "RescaleSlope", 1.0) or 1.0)
        intercept = float(getattr(ds, "RescaleIntercept", 0.0) or 0.0)
        hu = arr.astype(np.float64) * slope + intercept
        hu_range = (int(hu.min()), int(hu.max()))
        try:
            pixel_img = sitk.GetImageFromArray(arr.astype(np.int16))
            pixel_img.SetSpacing(
                (float(getattr(ds, "PixelSpacing", [1.0, 1.0])[1]),
                 float(getattr(ds, "PixelSpacing", [1.0, 1.0])[0]),
                 float(thickness_mm or 1.0))
            )
        except Exception:  # noqa: BLE001
            pixel_img = None
        break

    study_uid = str(getattr(first_ds, "StudyInstanceUID", "") or uid)
    n_slices = len(items)
    geom_w: list[str] = []
    if hu_range and hu_range[0] < -1200:
        geom_w.append(f"HU 下限 {hu_range[0]} 低于 CT 物理下界（-1200），请检查 Rescale")
    if hu_range and hu_range[1] > 4000:
        geom_w.append(f"HU 上限 {hu_range[1]} 高于常见 CT 上界（4000），请检查 Rescale")

    # M2-5(a)：源 DICOM 实例信息（逐层 SOP UID + 几何三要素）——commit 时持久化，
    # export_seg 用它写真实源实例引用（合成源实例降级为无 DICOM 来源时的 fallback）
    rows, cols = rows_cols(items)
    source_instances = extract_source_instances(items, rows=rows, cols=cols)

    return SeriesRecord(
        series_id=uid,
        case_key=study_uid,
        modality=modality,
        num_slices=n_slices,
        slice_thickness_mm=thickness_mm,
        orientation=orientation,
        hu_range=hu_range,
        image_type=image_type,
        burned_in_annotation=bia,
        ocr_result=ocr,
        deid_removed_count=deid_removed,
        geom_warnings=geom_w,
        source_path=items[0][0].name + (f"..{len(items)} slices" if len(items) > 1 else ""),
        pixel_data=pixel_img,
        source_instances=source_instances,
    )


def rows_cols(items: list[tuple[Path, Any]]) -> tuple[int, int]:
    """首个实例的 rows/cols（源几何）。"""
    ds = items[0][1]
    return int(getattr(ds, "Rows", 0) or 0), int(getattr(ds, "Columns", 0) or 0)


def extract_source_instances(items: list[tuple[Path, Any]], rows: int, cols: int) -> dict[str, Any]:
    """从 DICOM 头提取源实例信息（M2-5(a)）：逐层 SOP UID + SOP Class + 几何三要素。

    只取头字段（SOP UID / SOP Class UID / IOP / IPP / PixelSpacing /
    SliceThickness），不取像素（像素不进 AgentContext / 不持久化两份）。按
    DICOM 帧序惯例沿法向**反向**排布（帧 0 = 法向距离最大）——与 export_seg
    合成源实例的方向一致。sop_class_uid 为空串表示头缺失（旧持久化 JSON 无
    此键同理），export_seg 按"无原样 UID"走 fallback/raise 分支（S3P2 ③）。
    """
    import pydicom  # noqa: F401 — 仅为类型提示一致性

    entries = []
    for _, ds in items:
        sop_uid = str(getattr(ds, "SOPInstanceUID", "") or "")
        # S3P2 ③：SOP Class 原样提取——ReferencedSOPClassUID 必须逐字等于源实例
        # 实际 SOP Class（Modality→SOP Class 是有损投影，MR/PET 各有多档候选）
        sop_class_uid = str(getattr(ds, "SOPClassUID", "") or "")
        iop = [float(v) for v in (getattr(ds, "ImageOrientationPatient", None) or [])]
        ipp = [float(v) for v in (getattr(ds, "ImagePositionPatient", None) or [])]
        ps = getattr(ds, "PixelSpacing", None) or []
        st = getattr(ds, "SliceThickness", None)
        entries.append(
            {
                "sop_instance_uid": sop_uid,
                "sop_class_uid": sop_class_uid,
                "image_orientation_patient": iop,
                "image_position_patient": ipp,
                "pixel_spacing": [float(v) for v in ps],
                "slice_thickness_mm": float(st) if st else None,
            }
        )
    # 沿法向反向排布（DICOM 帧序惯例），export_seg 直接按此序写帧引用
    entries = _sort_by_normal_descending(entries)
    return {
        "rows": rows,
        "columns": cols,
        "instances": entries,
    }


def _sort_by_normal_descending(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按法向投影降序排布（帧 0 = 法向距离最大，DICOM 帧序惯例）。

    缺 IPP 或 IOP 异常的实例保持原序（尽力排序，不拒收）。
    """
    import numpy as _np

    iops = [e["image_orientation_patient"] for e in entries]
    ipps = [e["image_position_patient"] for e in entries]
    if not all(len(v) == 6 for v in iops) or not all(len(v) == 3 for v in ipps):
        return entries
    iop0 = _np.asarray(iops[0], dtype=float)
    row_cos, col_cos = iop0[0:3], iop0[3:6]
    normal = _np.cross(row_cos, col_cos)
    if not _np.any(normal):
        return entries

    def proj(e: dict[str, Any]) -> float:
        return float(_np.dot(_np.asarray(e["image_position_patient"], dtype=float), normal))

    try:
        return sorted(entries, key=proj, reverse=True)
    except TypeError:
        return entries


def ocr_scan_series_or_none(
    items: list[tuple[Path, Any]], run_ocr: bool = True
) -> OcrResult | None:
    """对 series 实例跑 OCR（第二道关）：任一实例检出即返回 detected 结果。

    非 OCR 目标序列（普通轴位断层）返回 None——不扫，无 OCR 记录。
    run_ocr 透传（测试注入用）。
    """
    for _, ds in items:
        if not is_ocr_target(ds):
            continue
        r = ocr_pixel_burned_scan(ds, run_ocr=run_ocr)
        return r  # 扫过即记录（首个目标实例的结果代表整个 series）
    return None


_DEID_KW = list(DEID_TAG_KEYWORDS)


# ---------------------------------------------------------------------------
# zip（DICOM 文件夹 zip / 多序列批量 zip / NIfTI zip）
# ---------------------------------------------------------------------------

def _ingest_zip_stream(
    src: BinaryIO | Path, limits: ExtractLimits, source_name: str = "", modality: str | None = None
) -> ImportReport:
    """zip 输入：safe_open_zip 三限预检 -> 流式落盘解压 -> 逐文件分派。

    M1-fix：zip 成员**流式落盘解压**（tempfile.TemporaryDirectory 于
    ~/data/BodyDonor/tmp 原生盘，不落 /tmp 内存盘），院内大包导入不爆内存。
    三限（压缩比/嵌套/文件数）在 safe_open_zip 预检 + 流式解压路径上继续生效
    （边解压边计数，超限即停即清）。处理完自动清理。
    """
    zf = safe_open_zip(src, limits)
    tmp_root = _zip_tmp_dir()
    try:
        tmp_root.mkdir(parents=True, exist_ok=True)
        with _streaming_unzip(zf, limits, tmp_root) as extract_dir:
            report = ImportReport(input_kind="dicom_zip")
            report.n_files_scanned = extract_dir.n_extracted
            report.skipped.extend(extract_dir.skipped)
            report.n_skipped = len(extract_dir.skipped)

            nifti_files: list[Path] = []
            dicom_files: list[Path] = []
            for f in extract_dir.files:
                lower = f.name.lower()
                if lower.endswith((".nii", ".nii.gz")):
                    nifti_files.append(f)
                elif lower.endswith((".dcm", ".dicom")) or _has_dicm_magic(f):
                    dicom_files.append(f)
                else:
                    report.skipped.append({"file": f.name, "reason": "非 NIfTI/DICOM 成员"})
                    report.n_skipped += 1

            if nifti_files and not dicom_files:
                report.input_kind = "nifti_zip"
                return _finish_nifti_files(nifti_files, report, modality=modality)
            if dicom_files:
                return _finish_dicom_files(dicom_files, report, modality=modality)
            if not nifti_files:
                raise IngestError(f"zip {source_name!r} 内无 NIfTI/DICOM 成员")
            # 混合：NIfTI 与 DICOM 都有 —— 各自成 Series，都收
            mixed = _finish_nifti_files(nifti_files, report, modality=modality)
            return _finish_dicom_files(dicom_files, mixed, modality=modality)
    finally:
        zf.close()


class _ExtractedZip:
    """流式解压的产物：解压出的文件路径列表 + 解压期跳过/计数。"""

    def __init__(self) -> None:
        self.files: list[Path] = []
        self.skipped: list[dict[str, str]] = []
        self.n_extracted: int = 0


def _streaming_unzip(
    zf: zipfile.ZipFile, limits: ExtractLimits, tmp_root: Path
) -> Any:
    """流式解压上下文管理器：边解压边计数（超限即停即清），处理完自动清理。

    三限在流式路径继续生效：
    - 文件数 > max_file_count 即停（边解压边计数，不依赖预检）
    - 嵌套 zip 成员不展开（跳过并记 skipped）
    - 压缩比预检在 safe_open_zip 已完成（逐成员元数据，无需再算）

    临时目录在 tmp_root（原生盘）下自动建删，退出时清理。
    """
    import tempfile
    from contextlib import contextmanager

    @contextmanager
    def _ctx() -> Any:
        extracted = _ExtractedZip()
        with tempfile.TemporaryDirectory(dir=tmp_root, prefix="bdzip-") as td:
            td_path = Path(td)
            for info in zf.infolist():
                name = info.filename
                if info.is_dir():
                    continue
                if name.lower().endswith(".zip"):
                    extracted.skipped.append(
                        {"file": name, "reason": "嵌套 zip 成员不支持（深度预检已限 ≤3）"}
                    )
                    continue
                if extracted.n_extracted >= limits.max_file_count:
                    raise ExtractLimitError(
                        f"zip contains > {limits.max_file_count} files (streaming unzip)",
                        limit=f"max_file_count={limits.max_file_count}",
                    )
                # 防路径穿越：成员名不允许 .. 与绝对路径
                target = (td_path / name).resolve()
                if not str(target).startswith(str(td_path.resolve())):
                    extracted.skipped.append(
                        {"file": name, "reason": "路径穿越成员，跳过"}
                    )
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                # 流式解压：分块读源流写目标盘（不整文件读入内存）
                with zf.open(info) as src_f, target.open("wb") as dst_f:
                    while True:
                        chunk = src_f.read(UNZIP_CHUNK)
                        if not chunk:
                            break
                        dst_f.write(chunk)
                extracted.files.append(target)
                extracted.n_extracted += 1
            yield extracted

    return _ctx()


def _has_dicm_magic(path: Path) -> bool:
    """读 128..132 字节判 DICM magic（流式分派用，不整文件读）。"""
    try:
        with path.open("rb") as f:
            f.seek(128)
            return f.read(4) == b"DICM"
    except OSError:
        return False


def _finish_nifti_files(
    members: list[Path], report: ImportReport, modality: str | None = None
) -> ImportReport:
    for f in members:
        try:
            img = sitk.ReadImage(str(f))
        except Exception as e:  # noqa: BLE001
            report.skipped.append({"file": f.name, "reason": f"NIfTI 读取失败: {type(e).__name__}"})
            report.n_skipped += 1
            continue
        if not modality:
            # B1：zip 内 NIfTI 无显式 modality -> 拒绝（IngestError，API 转 422），
            # 不静默记 unknown 冒充已核查模态
            raise IngestError(
                f"显式 modality 必填：{f.name}（zip 内 NIfTI 头无模态信息，不默认按 CT；"
                "CLI --modality / API modality 参数）"
            )
        report.series.append(_nifti_to_record(img, f.name, modality=modality))
        report.n_series += 1
    return report


def _finish_dicom_files(
    members: list[Path], report: ImportReport, modality: str | None = None
) -> ImportReport:
    import pydicom

    by_series: dict[str, list[tuple[Path, Any]]] = {}
    for f in members:
        try:
            ds = pydicom.dcmread(str(f), force=False)
        except Exception as e:  # noqa: BLE001
            report.skipped.append({"file": f.name, "reason": f"读取失败: {type(e).__name__}"})
            report.n_skipped += 1
            continue
        d = inspect_dicom(ds)
        if not d.accepted:
            report.skipped.append(
                {"file": f.name, "reason": "像素烧录声明 YES（BurnedInAnnotation），拒收"}
            )
            report.n_skipped += 1
            continue
        deid_dataset(ds)
        uid = str(getattr(ds, "SeriesInstanceUID", "") or "")
        if not uid:
            report.skipped.append({"file": f.name, "reason": "缺 SeriesInstanceUID"})
            report.n_skipped += 1
            continue
        by_series.setdefault(uid, []).append((f, ds))

    for uid, items in by_series.items():
        rec = _build_dicom_series(uid, items, report, requested_modality=modality)
        if rec is not None:
            report.series.append(rec)
            report.n_series += 1
    return report


def apply_series_rules(report: ImportReport) -> list[SeriesCandidate]:
    """对 ingest 产出的 series 应用选择器规则（CLI/API 在出报告前调用）。

    M1-fix：candidates 与 report.series **按 series_id 一一对应**——apply_rules
    就地排序 candidates，但 report.series 保持扫描序，两列表各自独立。
    CLI/下游取排除状态必须用 `candidate_by_id`（series_id -> candidate 映射），
    禁止 zip(candidates, series) 位置对应（M1-REPORT §3 实录 bug 根因：
    2 层 LOCALIZER 行显示了 40 层 candidate 的"选中"）。
    """
    candidates = [s.candidate() for s in report.series]
    return apply_rules(candidates)


def candidate_by_id(report: ImportReport) -> dict[str, SeriesCandidate]:
    """series_id -> SeriesCandidate 映射（CLI/下游按 id 取排除状态，不 zip）。"""
    return {c.series_id: c for c in report.series and _candidates(report)}


def _candidates(report: ImportReport) -> list[SeriesCandidate]:
    """从 report 取已应用规则的 candidates（scan 已就地应用）。"""
    # scan() 路径：outcome.candidates 已应用规则；这里为独立调用 ingest 的路径兜底：
    # candidates 直接从 report.series 构造（含 apply_rules 排序）
    return [s.candidate() for s in report.series]


__all__ = [
    "ImportReport",
    "IngestError",
    "SeriesRecord",
    "ingest",
    "apply_series_rules",
    "candidate_by_id",
    "ExtractLimitError",
]
