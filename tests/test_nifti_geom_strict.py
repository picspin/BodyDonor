"""M1-fix：NIfTI 几何强约束测试（RAS+ 重排 + assert_affine_match 守卫）。"""

from __future__ import annotations

import nibabel as nib
import numpy as np
import pytest
import SimpleITK as sitk  # noqa: N813

from bodydonor.nifti_geom import (
    AffineMismatchError,
    assert_affine_match,
    reorient_ras,
)


class TestReorientRas:
    def test_las_image_reoriented_to_ras(self, tmp_path) -> None:  # noqa: ANN001
        """(a) LAS 方向 NIfTI -> RAS+ 重排，重排结果可 nib 读。"""
        rng = np.random.default_rng(5)
        arr = rng.integers(-500, 500, size=(16, 16, 8), dtype=np.int16)
        affine_las = np.array(
            [
                [-1.0, 0.0, 0.0, 10.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )
        img = nib.Nifti1Image(arr, affine_las)
        p = tmp_path / "las.nii.gz"
        nib.save(img, str(p))
        loaded = nib.load(str(p))
        assert nib.aff2axcodes(loaded.affine) == ("L", "A", "S")

        canon, desc = reorient_ras(loaded)
        assert nib.aff2axcodes(canon.affine) == ("R", "A", "S")
        assert "RAS" in desc
        assert "重排" in desc

    def test_ras_image_no_reorientation(self, tmp_path) -> None:  # noqa: ANN001
        """已是 RAS+ 的图 -> 原样返回 + 描述'无需重排'。"""
        rng = np.random.default_rng(6)
        arr = rng.integers(-500, 500, size=(16, 16, 8), dtype=np.int16)
        img = nib.Nifti1Image(arr, np.eye(4))
        canon, desc = reorient_ras(img)
        assert nib.aff2axcodes(canon.affine) == ("R", "A", "S")
        assert "无需重排" in desc

    def test_reorientation_preserves_voxel_values(self, tmp_path) -> None:  # noqa: ANN001
        """重排后体素值（按世界坐标）不变。"""
        rng = np.random.default_rng(7)
        arr = rng.integers(-500, 500, size=(16, 16, 8), dtype=np.int16)
        affine_las = np.array(
            [
                [-1.0, 0.0, 0.0, 10.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )
        img = nib.Nifti1Image(arr, affine_las)
        canon, _ = reorient_ras(img)
        # LAS -> RAS：i 轴（affine 第 0 列方向）翻转 -> arr[::-1]
        arr_canon = np.asarray(canon.dataobj)
        assert np.array_equal(arr_canon, arr[::-1])


class TestAssertAffineMatch:
    def test_matching_affine_passes(self, tmp_path) -> None:  # noqa: ANN001
        """(b) affine 一致（写入往返误差内）-> 正常通过。"""
        rng = np.random.default_rng(8)
        arr = rng.integers(-500, 500, size=(16, 16, 8), dtype=np.int16)
        affine = np.array(
            [
                [1.0, 0.0, 0.0, 5.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )
        img = nib.Nifti1Image(arr, affine)
        mask = nib.Nifti1Image(np.zeros_like(arr), affine)
        p1, p2 = tmp_path / "a.nii.gz", tmp_path / "m.nii.gz"
        nib.save(img, str(p1))
        nib.save(mask, str(p2))
        la, ma = nib.load(str(p1)), nib.load(str(p2))
        assert_affine_match(la, ma)  # 不抛 -> 通过
        assert_affine_match(la, ma, label="roundtrip")  # label 参数不干扰

    def test_mismatched_affine_raises(self, tmp_path) -> None:  # noqa: ANN001
        """(b) affine 不一致 -> AffineMismatchError（硬报错，禁止静默重采样）。"""
        rng = np.random.default_rng(9)
        arr = rng.integers(-500, 500, size=(16, 16, 8), dtype=np.int16)
        img = nib.Nifti1Image(arr, np.eye(4))
        # mask affine 平移不同
        mask_affine = np.eye(4)
        mask_affine[0, 3] = 7.0
        mask = nib.Nifti1Image(np.zeros_like(arr), mask_affine)
        with pytest.raises(AffineMismatchError, match="affine 不一致"):
            assert_affine_match(img, mask)

    def test_mismatched_orientation_raises(self, tmp_path) -> None:  # noqa: ANN001
        """方向不同（RAS vs LAS）-> AffineMismatchError。"""
        rng = np.random.default_rng(10)
        arr = rng.integers(-500, 500, size=(16, 16, 8), dtype=np.int16)
        img = nib.Nifti1Image(arr, np.eye(4))
        las = np.array(
            [
                [-1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )
        mask = nib.Nifti1Image(np.zeros_like(arr), las)
        with pytest.raises(AffineMismatchError):
            assert_affine_match(img, mask)

    def test_tolerance_boundary(self, tmp_path) -> None:  # noqa: ANN001
        """容差边界：亚 tolerance 的浮点误差放行，超 tolerance 拒绝。"""
        arr = np.zeros((8, 8, 4), dtype=np.int16)
        img = nib.Nifti1Image(arr, np.eye(4))
        # 误差 1e-5 < 默认 1e-4 -> 放行
        tiny = np.eye(4)
        tiny[0, 3] = 1e-5
        mask_tiny = nib.Nifti1Image(arr, tiny)
        assert_affine_match(img, mask_tiny)
        # 误差 1e-2 > 默认 1e-4 -> 拒绝
        big = np.eye(4)
        big[0, 3] = 1e-2
        mask_big = nib.Nifti1Image(arr, big)
        with pytest.raises(AffineMismatchError):
            assert_affine_match(img, mask_big)


class TestCommitPathReorientation:
    def test_commit_saves_ras_oriented_nifti(self, tmp_path, settings) -> None:  # noqa: ANN001
        """commit 落盘的 NIfTI 是 RAS+ 方向，报告写明重排。"""
        from bodydonor.pipeline import commit, scan
        from bodydonor.storage import LocalFsStorage

        # sitk 合成图 direction=identity（LPS），写出 NIfTI axcodes = LPS/LAS
        arr = np.zeros((40, 32, 32), dtype=np.int16)
        arr[10:20, 8:24, 8:24] = 1
        img = sitk.GetImageFromArray(arr)
        img.SetSpacing((1.0, 1.0, 1.0))
        p = tmp_path / "img.nii.gz"
        sitk.WriteImage(img, str(p))

        outcome = scan(p, settings, modality="CT")
        rec = outcome.report.series[0]
        assert rec.num_slices == 40

        settings.database.url = f"sqlite:///{tmp_path}/t.db"
        storage = LocalFsStorage(tmp_path / "storage")
        committed = commit(outcome, settings, storage)
        assert len(committed.case_ids) == 1

        # 落盘文件是 RAS+ 方向
        key = f"{rec.case_key[:60]}/{rec.series_id[:60]}/image.nii.gz"
        assert storage.exists(key)
        saved = nib.load(str(storage.root / key))
        axcodes = nib.aff2axcodes(saved.affine)
        assert axcodes == ("R", "A", "S"), f"落盘方向 {axcodes} 非 RAS+"
