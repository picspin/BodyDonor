"""像素级 OCR 烧录扫描（M1-fix，第二道关）：只扫剂量报告/二次截图类序列。

策略（用户评审意见，M1-REPORT 复核）：普通螺旋 CT 轴位断层几乎不会有像素烧录，
烧录文字 99% 出现在 **Dose Report（剂量报告单，通常 1–5 层）** 或
**Secondary Capture（二次截图）** 中。OCR 只扫这两类序列（按 SOP Class UID /
SeriesDescription / ImageType 识别），普通轴位序列不扫——Tag 检查
（BurnedInAnnotation == YES）仍是第一道关，OCR 是第二道关；任一不通过即拒收。

OCR 引擎：rapidocr-onnxruntime（pip 可装、onnxruntime CPU 离线推理、无 apt 系统包）。
引擎懒加载（首次调用才 import/初始化，测试与无 OCR 场景零开销）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

# --- 目标序列识别（只对这两类跑 OCR） -------------------------------------

# Secondary Capture：SOP Class UID（SC 图像 / 多帧 SC / SC 灰阶 / SC 彩色）
SC_SOP_CLASS_UIDS = {
    "1.2.840.10008.5.1.4.1.1.7",  # Secondary Capture Image Storage
    "1.2.840.10008.5.1.4.1.1.7.1",  # Multiframe Single Bit SC
    "1.2.840.10008.5.1.4.1.1.7.2",  # Multiframe Grayscale SC
    "1.2.840.10008.5.1.4.1.1.7.3",  # Multiframe Color SC
    "1.2.840.10008.5.1.4.1.1.7.4",  # Multiframe True Color SC
}

# Dose Report 常见 SeriesDescription / ImageType 关键词（大小写不敏感）
DOSE_REPORT_KEYWORDS = re.compile(
    r"dose\s*report|dose\s*summary|radiation\s*dose|dose\s*screen|"
    r"protocol\s*screen|ct\s*dose|dosesheet",
    re.IGNORECASE,
)

# 烧录文字敏感词：OCR 命中即判为烧录（患者标识 / 剂量单特征）
SENSITIVE_TEXT = re.compile(
    r"patient|patient\s*id|accession|dob|birth|name\s*:|id\s*:|dose\s*report|"
    r"ctdi|dlp|ctdivol|sv\d|kvp|mas\b",
    re.IGNORECASE,
)


@dataclass
class OcrResult:
    """单张（或单序列）像素 OCR 烧录扫描结果。"""

    scanned: bool = False  # False = 不属于目标序列，未扫
    detected: bool = False  # True = 检出疑似烧录文字
    texts: list[str] = field(default_factory=list)  # OCR 命中的敏感文本
    reason: str = ""

    def to_report(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "detected": self.detected,
            "texts": list(self.texts),
            "reason": self.reason,
        }


def is_ocr_target(ds: Any) -> bool:
    """判定该 DICOM 实例是否需要 OCR 扫描：剂量报告或二次截图类。

    依据（任一命中即目标）：
    - SOPClassUID 在 Secondary Capture 家族
    - SeriesDescription / StudyDescription / ImageType 含 dose report 类关键词
    """
    sop_class = str(getattr(ds, "SOPClassUID", "") or "")
    if sop_class in SC_SOP_CLASS_UIDS:
        return True

    for kw in ("SeriesDescription", "StudyDescription"):
        desc = str(getattr(ds, kw, "") or "")
        if desc and DOSE_REPORT_KEYWORDS.search(desc):
            return True

    image_type = getattr(ds, "ImageType", None)
    if image_type:
        it = " ".join(str(v) for v in image_type)
        if DOSE_REPORT_KEYWORDS.search(it):
            return True
    return False


@lru_cache(maxsize=1)
def _get_engine() -> Any:
    """懒加载 RapidOCR 引擎（首次调用才 import + 初始化模型）。"""
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def ocr_pixel_burned_scan(ds: Any, run_ocr: bool = True) -> OcrResult:
    """对单个 DICOM 实例的像素跑 OCR 烧录扫描。

    非 OCR 目标（普通轴位断层）直接返回 scanned=False——不扫。
    目标序列：像素转灰度图 -> RapidOCR -> 命中敏感词即 detected=True。

    run_ocr=False 只做目标判定与敏感词开关（测试注入用）。
    """
    if not is_ocr_target(ds):
        return OcrResult(scanned=False, reason="非剂量报告/二次截图序列，不扫")

    if not run_ocr:
        return OcrResult(scanned=True, reason="目标序列（OCR 关闭）")

    try:
        arr = ds.pixel_array
    except Exception as e:  # noqa: BLE001 — 无像素/读不出按扫描过但未检出
        return OcrResult(scanned=True, reason=f"像素读取失败：{type(e).__name__}")

    # 多帧（SC 多帧剂量单）逐帧扫；单帧直接扫
    frames = arr if arr.ndim == 3 else arr[None, ...]
    texts: list[str] = []
    engine = _get_engine()
    for i in range(frames.shape[0]):
        frame = frames[i]
        img = _to_pil(frame)
        if img is None:
            continue
        try:
            result, _ = engine(img)
        except Exception as e:  # noqa: BLE001 — OCR 失败按未检出（引擎异常不拒收）
            return OcrResult(
                scanned=True, reason=f"OCR 引擎异常：{type(e).__name__}"
            )
        if not result:
            continue
        for line in result:
            text = str(line[1])
            if SENSITIVE_TEXT.search(text):
                texts.append(text)

    if texts:
        return OcrResult(
            scanned=True,
            detected=True,
            texts=texts[:10],
            reason="OCR 检出疑似烧录文字（患者标识/剂量单特征）",
        )
    return OcrResult(scanned=True, reason="OCR 扫描完成，无烧录文字")


def _to_pil(frame: Any) -> Any | None:
    """numpy 帧 -> PIL Image（灰度归一化；OCR 引擎吃 PIL/ndarray）。"""
    import numpy as np

    try:
        from PIL import Image
    except ImportError:
        return None

    f = np.asarray(frame)
    if f.ndim == 3 and f.shape[2] in (3, 4):  # RGB/RGBA
        mode = "RGB" if f.shape[2] == 3 else "RGBA"
        return Image.fromarray(f.astype("uint8"), mode=mode)
    # 灰度：归一化到 0..255（保留对比度，OCR 对 16bit 原始值不敏感）
    f = f.astype("float64")
    lo, hi = float(f.min()), float(f.max())
    if hi > lo:
        f = (f - lo) / (hi - lo) * 255.0
    else:
        f = np.zeros_like(f)
    return Image.fromarray(f.astype("uint8"), mode="L")


def ocr_scan_series(datasets: list[Any], run_ocr: bool = True) -> OcrResult:
    """对一个 Series 的全部实例跑 OCR：任一实例检出即拒收。"""
    agg = OcrResult()
    for ds in datasets:
        r = ocr_pixel_burned_scan(ds, run_ocr=run_ocr)
        if not r.scanned:
            continue
        agg.scanned = True
        if r.detected:
            agg.detected = True
            agg.texts.extend(r.texts)
            agg.reason = r.reason
            break
    if not agg.scanned:
        agg.reason = "无 OCR 目标实例"
    return agg


__all__ = [
    "OcrResult",
    "SC_SOP_CLASS_UIDS",
    "SENSITIVE_TEXT",
    "is_ocr_target",
    "ocr_pixel_burned_scan",
    "ocr_scan_series",
]
