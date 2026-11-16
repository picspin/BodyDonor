"""zip 炸弹三 case 负测试（M1 验收，proposal §5、GRILL-REVIEW 1.9）。

高压缩比（>100×）、嵌套 zip（>3 层）、超多文件（>10k）——各一条，
每条断言 ExtractLimitError 且错误类型对应正确 limit。
"""

from __future__ import annotations

import io
import zipfile

import pytest
from tests.factories import make_nifti, zip_bytes

from bodydonor.extract_guard import ExtractLimitError, ExtractLimits, safe_open_zip


def _make_nested_zip(depth: int, leaf_bytes: bytes) -> bytes:
    """构造 depth 层嵌套 zip（最内层包 leaf_bytes）。"""
    inner = leaf_bytes
    for _ in range(depth):
        inner = zip_bytes({"inner.zip": inner})
    return inner


class TestHighCompressionRatio:
    def test_high_compression_ratio_rejected(self) -> None:
        """case 1：压缩比 > 100× 拒收（1 MiB 高度可压缩数据 -> >100 MiB 展开比）。"""
        # 1 MiB 零字节 deflate 后 < 1 KiB：比值 > 1000×，稳定超过 100×
        members = {"huge.bin": b"\x00" * (1 << 20)}
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes(members)))
        info = zf.infolist()[0]
        assert info.file_size / info.compress_size > 100  # 前置：构造确实 >100×

        limits = ExtractLimits(
            max_compression_ratio=100, max_file_count=10_000, max_nested_zip_depth=3
        )
        with pytest.raises(ExtractLimitError) as exc_info:
            safe_open_zip(io.BytesIO(zip_bytes(members)), limits)
        assert "max_compression_ratio" in exc_info.value.limit


class TestNestedZip:
    def test_nested_zip_over_3_layers_rejected(self) -> None:
        """case 2：嵌套 zip > 3 层拒收（真实构造 4 层嵌套）。"""
        payload = zip_bytes({"data.bin": b"x" * 1024})
        bomb = _make_nested_zip(4, payload)  # 4 层 > 3 层上限

        limits = ExtractLimits(
            max_compression_ratio=100, max_file_count=10_000, max_nested_zip_depth=3
        )
        with pytest.raises(ExtractLimitError) as exc_info:
            safe_open_zip(io.BytesIO(bomb), limits)
        assert "max_nested_zip_depth" in exc_info.value.limit

    def test_nested_zip_within_3_layers_accepted(self) -> None:
        """对照：≤3 层嵌套不拒收（预检通过，成员按不支持跳过是 ingest 层行为）。"""
        payload = zip_bytes({"data.bin": b"x" * 1024})
        bomb = _make_nested_zip(3, payload)  # 3 层 = 上限，放行

        limits = ExtractLimits(
            max_compression_ratio=100, max_file_count=10_000, max_nested_zip_depth=3
        )
        zf = safe_open_zip(io.BytesIO(bomb), limits)
        zf.close()


class TestFileCount:
    def test_over_10k_files_rejected(self) -> None:
        """case 3：文件数 > 10k 拒收（10001 个条目）。"""
        members = {f"f{i}.bin": b"x" for i in range(10_001)}
        limits = ExtractLimits(
            max_compression_ratio=100, max_file_count=10_000, max_nested_zip_depth=3
        )
        with pytest.raises(ExtractLimitError) as exc_info:
            safe_open_zip(io.BytesIO(zip_bytes(members)), limits)
        assert "max_file_count" in exc_info.value.limit

    def test_at_10k_files_accepted(self) -> None:
        """对照：10k 整不拒收（边界值）。"""
        members = {f"f{i}.bin": b"x" for i in range(10_000)}
        limits = ExtractLimits(
            max_compression_ratio=100, max_file_count=10_000, max_nested_zip_depth=3
        )
        zf = safe_open_zip(io.BytesIO(zip_bytes(members)), limits)
        zf.close()


class TestIngestIntegration:
    def test_ingest_zip_rejects_bomb(self, tmp_path) -> None:  # noqa: ANN001
        """端到端：ingest() 对压缩比炸弹 zip 抛 ExtractLimitError（不产出报告）。"""
        from bodydonor.ingest import ingest

        members = {"bomb.bin": b"\x00" * (1 << 20)}
        bomb = zip_bytes(members)
        with pytest.raises(ExtractLimitError):
            ingest(io.BytesIO(bomb))

    def test_normal_nifti_zip_accepted(self, tmp_path) -> None:  # noqa: ANN001
        """对照：正常 NIfTI zip 通过三限预检并拆出 Series。"""
        from bodydonor.ingest import ingest

        nii = make_nifti(tmp_path / "case1" / "img.nii.gz")
        members = {"case1/img.nii.gz": nii.read_bytes()}
        report = ingest(io.BytesIO(zip_bytes(members)), modality="CT")
        assert report.n_series == 1
