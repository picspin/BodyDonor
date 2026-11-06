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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
import SimpleITK as sitk  # noqa: N813 — sitk 是官方惯用别名

from .deid import DEID_TAG_KEYWORDS, deid_dataset, inspect_dicom
from .extract_guard import ExtractLimitError, ExtractLimits, safe_open_zip
from .series_selector import SeriesCandidate, apply_rules

# 支持的输入后缀
NIFTI_SUFFIXES = {".nii", ".gz"}  # .nii 与 .nii.gz（.gz 仅在 .nii.gz 时由名称二次确认）
NIFTI_NAME_SUFFIXES = (".nii", ".nii.gz")
DICOM_SUFFIXES = {".dcm", ".dicom", ""}
ZIP_SUFFIXES = {".zip"}


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
    deid_removed_count: int = 0
    deid_warnings: list[str] = field(default_factory=list)
    geom_warnings: list[str] = field(default_factory=list)
    source_path: str = ""  # 输入内相对路径（不泄露本地真实姓名/路径之外的 PHI）
    pixel_data: Any = None  # sitk.Image；落库时转 NIfTI，不进 AgentContext

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
            "deid_removed_count": self.deid_removed_count,
            "deid_warnings": list(self.deid_warnings),
            "geom_warnings": list(self.geom_warnings),
            "source_path": self.source_path,
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
) -> ImportReport:
    """Import Bundle 单一入口：三种输入 -> ImportReport（含 [SeriesRecord]）。

    - Path 指向 NIfTI 文件 / DICOM 文件夹 / zip（自动分派）
    - BinaryIO/bytes 视为 zip 流（API 上传通道）
    - recursive：文件夹树递归扫描（多序列批量）
    """
    limits = limits or ExtractLimits()
    if isinstance(path_or_stream, (str, bytes)) and not isinstance(path_or_stream, Path):
        data = path_or_stream if isinstance(path_or_stream, bytes) else path_or_stream.read()
        return _ingest_zip_stream(io.BytesIO(data), limits, source_name=source_name)
    if isinstance(path_or_stream, Path):
        p = path_or_stream
        if _is_zip(p):
            return _ingest_zip_stream(p, limits, source_name=source_name or p.name)
        if p.is_dir():
            return _ingest_folder(p, limits, recursive=recursive)
        if _is_nifti(p):
            return _ingest_nifti_file(p, limits)
        if _looks_like_dicom(p):
            # 单个 DICOM 文件 -> 当作单文件文件夹处理
            return _ingest_folder(p.parent, limits, recursive=False, extra_files=[p])
        raise IngestError(f"不支持的输入：{p}（期望 .nii/.nii.gz、DICOM 文件夹或 .zip）")
    # BinaryIO
    return _ingest_zip_stream(path_or_stream, limits, source_name=source_name)


# ---------------------------------------------------------------------------
# NIfTI
# ---------------------------------------------------------------------------

def _ingest_nifti_file(path: Path, limits: ExtractLimits) -> ImportReport:
    img = sitk.ReadImage(str(path))
    rec = _nifti_to_record(img, path.name)
    report = ImportReport(n_series=1, n_files_scanned=1, input_kind="nifti")
    report.series.append(rec)
    return report


def _nifti_to_record(img: sitk.Image, name: str) -> SeriesRecord:
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
        modality="CT",
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
    uid: str, items: list[tuple[Path, Any]], report: ImportReport
) -> SeriesRecord | None:

    first_ds = items[0][1]
    # 像素烧录/PHI 统计（全 series 一致；inspect 第一个已 deid，removed 记在文件级）
    bia = str(getattr(first_ds, "BurnedInAnnotation", "UNKNOWN")).strip().upper()
    if bia not in ("YES", "NO"):
        bia = "UNKNOWN"
    deid_removed = sum(1 for _, ds in items for kw in _DEID_KW if getattr(ds, kw, "") == "")

    # Series 元数据
    modality = str(getattr(first_ds, "Modality", "") or None) or None
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
        deid_removed_count=deid_removed,
        geom_warnings=geom_w,
        source_path=items[0][0].name + (f"..{len(items)} slices" if len(items) > 1 else ""),
        pixel_data=pixel_img,
    )


_DEID_KW = list(DEID_TAG_KEYWORDS)


# ---------------------------------------------------------------------------
# zip（DICOM 文件夹 zip / 多序列批量 zip / NIfTI zip）
# ---------------------------------------------------------------------------

def _ingest_zip_stream(
    src: BinaryIO | Path, limits: ExtractLimits, source_name: str = ""
) -> ImportReport:
    """zip 输入：先 safe_open_zip 三限预检，再在内存中按内容分派。

    zip 成员不落盘（内存处理）；NIfTI 成员直接 sitk 读，DICOM 成员 pydicom 读。
    """
    zf = safe_open_zip(src, limits)
    try:
        names = [i.filename for i in zf.infolist() if not i.is_dir()]
        # 二次防护：嵌套 zip 内容不递归解析（>3 层在 safe_open_zip 已拒收；
        # ≤3 层的嵌套 zip 成员按不支持跳过，报入 skipped）
        report = ImportReport(n_files_scanned=len(names), input_kind="dicom_zip")

        nifti_members: list[tuple[str, bytes]] = []
        dicom_members: list[tuple[str, bytes]] = []
        for info in zf.infolist():
            name = info.filename
            lower = name.lower()
            if lower.endswith(".zip"):
                report.skipped.append(
                    {"file": name, "reason": "嵌套 zip 成员不支持（深度预检已限 ≤3）"}
                )
                report.n_skipped += 1
                continue
            data = zf.read(info)
            if lower.endswith((".nii", ".nii.gz")):
                nifti_members.append((name, data))
            elif lower.endswith((".dcm", ".dicom")) or data[128:132] == b"DICM":
                dicom_members.append((name, data))
            else:
                report.skipped.append({"file": name, "reason": "非 NIfTI/DICOM 成员"})
                report.n_skipped += 1

        if nifti_members and not dicom_members:
            report.input_kind = "nifti_zip"
            return _finish_nifti_members(nifti_members, report)
        if dicom_members:
            return _finish_dicom_members(dicom_members, report)
        if not nifti_members and not dicom_members:
            raise IngestError(f"zip {source_name!r} 内无 NIfTI/DICOM 成员")
        # 混合：NIfTI 与 DICOM 都有 —— 各自成 Series，都收
        mixed = _finish_nifti_members(nifti_members, report)
        mixed = _finish_dicom_members(dicom_members, report)
        return mixed
    finally:
        zf.close()


def _finish_nifti_members(
    members: list[tuple[str, bytes]], report: ImportReport
) -> ImportReport:
    for name, data in members:
        try:
            img = _read_nifti_bytes(data)
        except Exception as e:  # noqa: BLE001
            report.skipped.append({"file": name, "reason": f"NIfTI 读取失败: {type(e).__name__}"})
            report.n_skipped += 1
            continue
        report.series.append(_nifti_to_record(img, Path(name).name))
        report.n_series += 1
    return report


def _read_nifti_bytes(data: bytes) -> sitk.Image:
    """从内存字节读 NIfTI（sitk 需要文件名——走临时 IO 通道）。"""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        return sitk.ReadImage(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _finish_dicom_members(
    members: list[tuple[str, bytes]], report: ImportReport
) -> ImportReport:
    import io as _io

    import pydicom

    by_series: dict[str, list[tuple[str, Any]]] = {}
    for name, data in members:
        try:
            ds = pydicom.dcmread(_io.BytesIO(data), force=False)
        except Exception as e:  # noqa: BLE001
            report.skipped.append({"file": name, "reason": f"读取失败: {type(e).__name__}"})
            report.n_skipped += 1
            continue
        d = inspect_dicom(ds)
        if not d.accepted:
            report.skipped.append(
                {"file": name, "reason": "像素烧录声明 YES（BurnedInAnnotation），拒收"}
            )
            report.n_skipped += 1
            continue
        deid_dataset(ds)
        uid = str(getattr(ds, "SeriesInstanceUID", "") or "")
        if not uid:
            report.skipped.append({"file": name, "reason": "缺 SeriesInstanceUID"})
            report.n_skipped += 1
            continue
        by_series.setdefault(uid, []).append((name, ds))

    for uid, items in by_series.items():
        rec = _build_dicom_series_from_members(uid, items, report)
        if rec is not None:
            report.series.append(rec)
            report.n_series += 1
    return report


def _build_dicom_series_from_members(
    uid: str, items: list[tuple[str, Any]], report: ImportReport
) -> SeriesRecord | None:
    """zip 成员版 series 构建：复用 _build_dicom_series 的逻辑（Path 适配）。"""
    wrapped = [(_FakePath(n), ds) for n, ds in items]
    return _build_dicom_series(uid, wrapped, report)


class _FakePath:
    """只提供 .name 的 Path 替身（zip 成员名）。"""

    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name


def apply_series_rules(report: ImportReport) -> list[SeriesCandidate]:
    """对 ingest 产出的 series 应用选择器规则（CLI/API 在出报告前调用）。"""
    candidates = [s.candidate() for s in report.series]
    return apply_rules(candidates)


__all__ = [
    "ImportReport",
    "IngestError",
    "SeriesRecord",
    "ingest",
    "apply_series_rules",
    "ExtractLimitError",
]
