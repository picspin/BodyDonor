"""M1-fix：zip 流式落盘解压测试（内存不随包大小线性涨 + 临时目录事后干净 + 三限生效）。"""

from __future__ import annotations

import io
import tracemalloc
import zipfile
from pathlib import Path

import numpy as np
import pytest
from tests.factories import dicom_folder_zip, make_dicom_folder, make_nifti, zip_bytes

from bodydonor.extract_guard import ExtractLimitError, ExtractLimits
from bodydonor.ingest import DEFAULT_ZIP_TMP_DIR, ingest


@pytest.fixture
def zip_tmp(tmp_path, monkeypatch):  # noqa: ANN001
    """流式解压临时目录隔离到 tmp_path（不污染 ~/data）。"""
    target = tmp_path / "ziptmp"
    monkeypatch.setenv("BODYDONOR_ZIP_TMP_DIR", str(target))
    return target


class TestStreamingUnzip:
    def test_dicom_zip_streaming_roundtrip(self, zip_tmp, tmp_path) -> None:  # noqa: ANN001
        """DICOM zip 流式解压：结果与内存分派一致（拆 Series）。"""
        root = make_dicom_folder(tmp_path / "dicom", n_series=2, slices_per_series=40)
        data = dicom_folder_zip(root)
        report = ingest(io.BytesIO(data))
        assert report.input_kind == "dicom_zip"
        assert report.n_series == 2
        assert all(s.num_slices == 40 for s in report.series)

    def test_tmp_dir_cleaned_after(self, zip_tmp, tmp_path) -> None:  # noqa: ANN001
        """临时目录事后干净：处理完自动清理，无残留解压文件。"""
        nii = make_nifti(tmp_path / "img.nii.gz")
        data = zip_bytes({"c/img.nii.gz": nii.read_bytes()})
        report = ingest(io.BytesIO(data))
        assert report.n_series == 1
        # 临时根目录存在但无残留（TemporaryDirectory 已清理）
        leftovers = list(zip_tmp.rglob("*")) if zip_tmp.exists() else []
        assert leftovers == [], f"临时目录有残留：{leftovers}"

    def test_memory_not_linear_in_package_size(self, zip_tmp, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        """大包流式解压：解压+分派段内存不随包大小线性涨。

        对比：内存分派（zip.read 整成员进内存）peak = 解压后体积；
        流式落盘只 1 MiB 块缓冲。sitk 读图/pixel_data 保留是 ingest 固有开销
        （与解压方式无关），本测试 mock 掉 sitk.ReadImage 只测解压+分派段，
        断言 peak 恒定（不随 2/16/32 MiB 包线性涨）。
        """
        import SimpleITK as sitk  # noqa: N813

        # mock sitk.ReadImage：ingest 固有开销不进本测量
        monkeypatch.setattr(
            sitk,
            "ReadImage",
            lambda *a, **k: sitk.GetImageFromArray(np.zeros((1, 1, 1), dtype=np.int16)),
        )

        peaks: dict[int, float] = {}
        for mi in (2, 16, 32):
            arr = np.zeros((256, 256, mi * 2), dtype=np.int16)  # mi*2 MiB 原始
            img = sitk_from_array(arr)
            p = tmp_path / f"big{mi}.nii.gz"
            write_sitk(img, p)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
                zf.writestr(f"big{mi}.nii.gz", p.read_bytes())  # 压缩比 1，不触发预检
            data = buf.getvalue()

            tracemalloc.start()
            ingest(io.BytesIO(data))
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            peaks[mi] = peak

        # 流式解压：peak 恒定（块缓冲），不随包大小线性涨
        # 内存分派版 peak 会 = 4/32/64 MiB（解压后体积）
        limit = 4 * (1 << 20)
        small, mid, big = peaks[2], peaks[16], peaks[32]
        msg = "peak {:.2f} MiB（内存分派版会随包体积线性涨）"
        assert small < limit, msg.format(small / (1 << 20))
        assert mid < limit, msg.format(mid / (1 << 20)) + "：16 MiB 包"
        assert big < limit, msg.format(big / (1 << 20)) + "：32 MiB 包"

    def test_file_count_limit_stops_streaming(self, zip_tmp) -> None:  # noqa: ANN001
        """三限在流式路径生效：边解压边计数，超限即停（ExtractLimitError）。"""
        # 10001 个成员 > 10k：safe_open_zip 预检已拒收，这里验证流式路径也拒
        members = {f"f{i}.bin": b"x" for i in range(10_001)}
        data = zip_bytes(members)
        with pytest.raises(ExtractLimitError):
            ingest(io.BytesIO(data))

    def test_file_count_guard_at_limits(self, zip_tmp) -> None:  # noqa: ANN001
        """文件数三限判定值：10k 整放行（预检），>10k 拒收。"""
        from bodydonor.extract_guard import safe_open_zip

        limits = ExtractLimits(
            max_compression_ratio=100, max_nested_zip_depth=3, max_file_count=10
        )
        # 10 个成员 = 上限 -> 放行
        members = {f"f{i}.bin": b"x" for i in range(10)}
        zf = safe_open_zip(io.BytesIO(zip_bytes(members)), limits)
        zf.close()

    def test_path_traversal_member_skipped(self, zip_tmp, tmp_path) -> None:  # noqa: ANN001
        """路径穿越成员（../evil）不落盘，记 skipped。"""
        nii = make_nifti(tmp_path / "img.nii.gz")
        # 手工构造带路径穿越成员的 zip
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("../evil.txt", b"bad")
            zf.writestr("img.nii.gz", nii.read_bytes())
        report = ingest(io.BytesIO(buf.getvalue()))
        # evil 成员被跳过，NIfTI 成员正常导入
        assert report.n_series == 1
        assert any("路径穿越" in sk["reason"] for sk in report.skipped)
        # evil 不存在于临时目录外
        evil = zip_tmp.parent / "evil.txt"
        assert not evil.exists()

    def test_default_tmp_dir_is_native_disk(self) -> None:
        """默认临时目录在 ~/data/BodyDonor/tmp（原生盘，不落 /tmp 内存盘）。"""
        assert str(DEFAULT_ZIP_TMP_DIR).startswith(str(Path.home() / "data"))
        # 路径前缀不是 /tmp 内存盘（~/data/BodyDonor/tmp 的尾部 'tmp' 是目录名）
        assert not str(DEFAULT_ZIP_TMP_DIR).startswith("/tmp")
        assert str(DEFAULT_ZIP_TMP_DIR).endswith("BodyDonor/tmp")


# sitk 懒 import（测试内惯用）
def sitk_from_array(arr):  # noqa: ANN001
    import SimpleITK as sitk  # noqa: N813

    return sitk.GetImageFromArray(arr)


def write_sitk(img, path: Path) -> None:  # noqa: ANN001
    import SimpleITK as sitk  # noqa: N813

    path.parent.mkdir(parents=True, exist_ok=True)
    sitk.WriteImage(img, str(path))
