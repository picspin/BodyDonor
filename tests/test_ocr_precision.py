"""M2-5(c)：OCR 精度边界验证（低分辨率 + 斜排/旋转文字）。

M1-fix 的 rapidocr 只在 512×512 水平清晰文字上验证过。本测试补验证：
- 低分辨率：512 → 256 / 128 下采样
- 斜排/旋转：15° / 30° 旋转

记录检出率数字（M2-REPORT 引用），漏检如实记录精度边界，不调低阈值蒙混。
上传页声明仍保留为 UNKNOWN 场景防线。
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw
from tests.test_burned_ocr import _make_sc_dataset

from bodydonor.burned_ocr import ocr_pixel_burned_scan

# 精度验证结果记录表（M2-REPORT 实测引用；CONFRONT 表由测试运行填充）
RESULTS: list[dict[str, object]] = []


def _make_text_image(
    size: int = 512,
    rotation: float = 0.0,
    text: str = "DOSE REPORT\nPatient ID: PID-12345",
) -> np.ndarray:
    """合成烧录文字图：size×size 黑底白字，rotation 旋转角度。"""
    img = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(img)
    # 字号随分辨率缩放（保持相对大小一致）
    font_size = max(10, size // 16)
    try:
        from PIL import ImageFont

        font = ImageFont.load_default(font_size)
    except TypeError:
        font = None  # PIL 老版本 load_default 无 size 参数
    for i, line in enumerate(text.split("\n")):
        d.text((size // 8, size // 8 + i * int(font_size * 2.2)), line, fill=255, font=font)
    if rotation:
        img = img.rotate(rotation, resample=Image.BILINEAR)
    return np.array(img)


def _detect(arr: np.ndarray) -> tuple[bool, list[str]]:
    ds = _make_sc_dataset(arr)
    r = ocr_pixel_burned_scan(ds)
    return r.detected, r.texts


class TestOcrLowResolution:
    """低分辨率下采样验证（记录检出率）。"""

    @pytest.mark.parametrize("size", [512, 256, 128])
    def test_horizontal_text_at_resolution(self, size: int) -> None:
        """水平清晰文字在各分辨率的检出情况（数字进 M2-REPORT）。"""
        arr = _make_text_image(size=size)
        detected, texts = _detect(arr)
        RESULTS.append(
            {"case": f"horizontal_{size}px", "detected": detected, "n_texts": len(texts)}
        )
        # 如实记录：512 必检出（M1 基线）；256/128 记录实际数字，不调阈值
        if size == 512:
            assert detected is True

    def test_128px_boundary_is_documented(self) -> None:
        """128px 边界：检出与否都记录（精度边界如实，不调阈值蒙混）。"""
        arr = _make_text_image(size=128)
        detected, texts = _detect(arr)
        RESULTS.append(
            {"case": "horizontal_128px_boundary", "detected": detected, "n_texts": len(texts)}
        )


class TestOcrRotatedText:
    """斜排/旋转文字验证（15°/30°）。"""

    @pytest.mark.parametrize("angle", [0, 15, 30, 45])
    def test_rotated_text_detection(self, angle: float) -> None:
        """各旋转角度的检出情况（512×512，数字进 M2-REPORT）。"""
        arr = _make_text_image(size=512, rotation=angle)
        detected, texts = _detect(arr)
        RESULTS.append(
            {"case": f"rotated_{angle}deg_512px", "detected": detected, "n_texts": len(texts)}
        )
        if angle == 0:
            assert detected is True  # 基线


class TestOcrCombined:
    """组合（低分辨率 + 旋转）与漏检边界（如实记录，不调阈值蒙混）。"""

    def test_256px_rotated_30deg(self) -> None:
        arr = _make_text_image(size=256, rotation=30)
        detected, texts = _detect(arr)
        RESULTS.append(
            {"case": "rotated_30deg_256px", "detected": detected, "n_texts": len(texts)}
        )

    def test_miss_boundary_below_40px_documented(self) -> None:
        """漏检边界（实测）：≤36px 漏检（字号过小 OCR 不可读）。

        精度边界如实断言并记录（M2-REPORT 引用）——不调低阈值蒙混：
        - ≥40px：检出（边界内可靠）
        - ≤36px：**漏检**（rapidocr 对过小字号不可读）
        上传页声明（UNKNOWN 场景）仍是防线：OCR 不是万能，Tag 检查 + 声明勾选兜底。
        """
        for size, expect_detect in [(40, True), (36, False), (32, False)]:
            arr = _make_text_image(size=size)
            detected, texts = _detect(arr)
            RESULTS.append(
                {
                    "case": f"horizontal_{size}px_boundary",
                    "detected": detected,
                    "n_texts": len(texts),
                }
            )
            assert detected is expect_detect, (
                f"{size}px 实测 detected={detected}，与记录的精度边界不符（不调阈值，"
                "重新实测并更新 M2-REPORT 边界数字）"
            )

    def test_rotated_wide_range_detected(self) -> None:
        """旋转 15/30/60/90° 在 512px 全检出（实测数字进 M2-REPORT）。"""
        for angle in [15, 30, 60, 90]:
            arr = _make_text_image(size=512, rotation=angle)
            detected, texts = _detect(arr)
            RESULTS.append(
                {"case": f"rotated_{angle}deg_512px", "detected": detected, "n_texts": len(texts)}
            )
            assert detected is True, f"{angle}° 实测漏检（如实更新边界记录）"

    def test_results_table_printed(self, capsys) -> None:  # noqa: ANN001
        """运行末尾打印精度表（人工抄进 M2-REPORT）。"""
        print("\n=== OCR 精度验证结果 ===")
        for r in RESULTS:
            print(f"  {r['case']}: detected={r['detected']} texts={r['n_texts']}")
        captured = capsys.readouterr()
        assert "OCR 精度验证结果" in captured.out
