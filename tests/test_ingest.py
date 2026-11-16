"""M1 ingest 测试：三种输入、De-ID/烧录拒收、Series 选择器、NIfTI 几何校验。"""

from __future__ import annotations

import io

import numpy as np
import pytest
from tests.factories import (
    dicom_folder_zip,
    make_burned_folder,
    make_dicom_folder,
    make_localizer,
    make_nifti,
)

from bodydonor.ingest import ImportReport, IngestError, ingest
from bodydonor.nifti_geom import check_nifti_geometry
from bodydonor.series_selector import SeriesCandidate, apply_rules, select


class TestNiftiInput:
    def test_single_nifti_ingests_one_series(self, tmp_path) -> None:  # noqa: ANN001
        """输入 1：单个 NIfTI 直接入库，单元素列表。"""
        nii = make_nifti(tmp_path / "img.nii.gz")
        report = ingest(nii, modality="CT")
        assert isinstance(report, ImportReport)
        assert report.input_kind == "nifti"
        assert report.n_series == 1
        assert len(report.series) == 1
        assert report.series[0].modality == "CT"
        assert report.series[0].hu_range is not None

    def test_nifti_geometry_anisotropic_warning(self, tmp_path) -> None:  # noqa: ANN001
        """几何校验：affine 各向异性（spacing 5mm x 0.5mm = 10x）警告。"""
        nii = make_nifti(tmp_path / "aniso.nii.gz", spacing=(0.5, 0.5, 5.0))
        report = ingest(nii, modality="CT")
        warnings = report.series[0].geom_warnings
        assert any("各向异性" in w for w in warnings)

    def test_nifti_geometry_orthonormal_no_warning(self, tmp_path) -> None:  # noqa: ANN001
        """几何校验：正交轴位无警告。"""
        nii = make_nifti(tmp_path / "ortho.nii.gz")
        report = ingest(nii, modality="CT")
        assert report.series[0].geom_warnings == []

    def test_nifti_geom_checker_units(self, tmp_path) -> None:  # noqa: ANN001
        """nifti_geom.check_nifti_geometry：毫米单位 + 正交 -> 无警告；方向 axial。"""
        make_nifti(tmp_path / "g.nii.gz")
        # 走 nibabel 视角（写临时文件后 nib 读）
        import nibabel as nib

        nib_img = nib.load(str(tmp_path / "g.nii.gz"))
        rep = check_nifti_geometry(nib_img)
        assert rep.is_mm is True
        assert rep.is_orthogonal is True
        assert rep.orientation == "axial"

    def test_nifti_geom_non_orthogonal_warning(self, tmp_path) -> None:  # noqa: ANN001
        """几何校验：非正交方向警告（direction 列不正交）。"""
        import nibabel as nib

        rng = np.random.default_rng(3)
        arr = rng.integers(-500, 500, size=(32, 32, 16), dtype=np.int16)
        # 非正交 affine：列向量不正交
        affine = np.array(
            [
                [1.0, 0.5, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )
        img = nib.Nifti1Image(arr, affine)
        p = tmp_path / "skew.nii.gz"
        nib.save(img, str(p))
        loaded = nib.load(str(p))
        rep = check_nifti_geometry(loaded)
        assert rep.is_orthogonal is False
        assert any("非正交" in w for w in rep.warnings)

    def test_unsupported_input_raises(self, tmp_path) -> None:  # noqa: ANN001
        """不支持的输入 -> IngestError。"""
        f = tmp_path / "x.txt"
        f.write_text("nope")
        with pytest.raises(IngestError):
            ingest(f)


class TestDicomFolderInput:
    def test_dicom_folder_splits_by_series_uid(self, tmp_path) -> None:  # noqa: ANN001
        """输入 2：DICOM 文件夹按 SeriesInstanceUID 拆分为多个 Series。"""
        root = make_dicom_folder(tmp_path / "dicom", n_series=2, slices_per_series=40)
        report = ingest(root)
        assert report.input_kind == "dicom_folder"
        assert report.n_series == 2
        assert all(s.num_slices == 40 for s in report.series)
        assert all(s.modality == "CT" for s in report.series)
        assert all(s.orientation == "axial" for s in report.series)

    def test_dicom_folder_deid_strips_phi(self, tmp_path) -> None:  # noqa: ANN001
        """De-ID：DICOM 头患者字段在 ingest 第一步剥离。"""
        root = make_dicom_folder(tmp_path / "dicom", n_series=1, slices_per_series=40)
        report = ingest(root)
        rec = report.series[0]
        assert rec.deid_removed_count > 0  # PatientName/PatientID/AccessionNumber 等

    def test_burned_annotation_rejected(self, tmp_path) -> None:  # noqa: ANN001
        """像素烧录声明 YES -> 拒收（不是隔离留存）。"""
        root = make_burned_folder(tmp_path / "burned", slices=40)
        report = ingest(root)
        assert report.n_series == 0
        assert report.n_skipped == 40
        assert all("烧录" in sk["reason"] for sk in report.skipped)


class TestDicomModalityMissing:
    """W4 真修：DICOM Modality 标签五类 -> SeriesRecord.modality。"""

    def _ingest_one(self, tmp_path, ds) -> ImportReport:
        from tests.factories import save_dicom

        root = tmp_path / "dcm"
        for i in range(40):
            d = ds if i == 0 else ds.copy()
            d.InstanceNumber = i + 1
            save_dicom(d, root / f"slice{i:04d}.dcm")
        report = ingest(root)
        assert report.n_series == 1
        return report

    @pytest.mark.parametrize(
        ("modality_kw", "expected"),
        [
            ("CT", "CT"),  # 正常
            ("", None),  # 空串
            ("  ", None),  # 空白
            (None, None),  # None
        ],
    )
    def test_modality_label_values(self, tmp_path, modality_kw, expected) -> None:
        from tests.factories import make_dicom_dataset

        ds = make_dicom_dataset(series_uid="1.2.826.0.1.3680043.8.498.99")
        if modality_kw is None:
            del ds.Modality  # 标签缺失（pydicom 缺 tag，getattr 回 None）
        else:
            ds.Modality = modality_kw
        report = self._ingest_one(tmp_path, ds)
        assert report.series[0].modality == expected

    def test_modality_missing_tag_is_none_not_string(self, tmp_path) -> None:
        """缺失标签 -> Python None，不是字符串 "None"（W4 修复点）。"""
        from tests.factories import make_dicom_dataset

        ds = make_dicom_dataset(series_uid="1.2.826.0.1.3680043.8.498.98")
        del ds.Modality
        report = self._ingest_one(tmp_path, ds)
        rec = report.series[0]
        assert rec.modality is None


class TestZipInput:
    def test_dicom_zip_splits_series(self, tmp_path) -> None:  # noqa: ANN001
        """输入 3：DICOM zip（多序列批量）按 SeriesInstanceUID 拆分。"""
        root = make_dicom_folder(tmp_path / "dicom", n_series=3, slices_per_series=35)
        make_localizer(tmp_path / "dicom")
        data = dicom_folder_zip(root)
        report = ingest(io.BytesIO(data), source_name="batch.zip")
        assert report.input_kind == "dicom_zip"
        assert report.n_series == 4  # 3 series + 1 localizer

    def test_multi_series_zip_report(self, tmp_path) -> None:  # noqa: ANN001
        """多序列 zip 的导入报告包含每个 Series 的层厚/方向/HU。"""
        root = make_dicom_folder(tmp_path / "dicom", n_series=2, slices_per_series=35)
        report = ingest(io.BytesIO(dicom_folder_zip(root)))
        d = report.to_dict()
        assert d["n_series"] == 2
        for s in d["series"]:
            assert s["slice_thickness_mm"] == 1.0
            assert s["orientation"] == "axial"
            assert s["hu_range"] is not None


class TestSelector:
    def test_localizer_excluded(self) -> None:  # noqa: ANN001
        """规则：ImageType[0] == 'LOCALIZER' 排除。"""
        c = SeriesCandidate(series_id="s1", image_type=["LOCALIZER", "PRIMARY"], num_slices=50)
        out = apply_rules([c])
        assert out[0].excluded is True
        assert "LOCALIZER" in out[0].exclude_reason

    def test_derived_not_excluded_but_flagged_and_sorted_last(self) -> None:  # noqa: ANN001
        """规则：DERIVED 不硬排，排序置后 + 报告标注。"""
        orig = SeriesCandidate(
            series_id="orig", image_type=["ORIGINAL", "PRIMARY"], num_slices=100
        )
        derived = SeriesCandidate(
            series_id="der", image_type=["DERIVED", "PRIMARY"], num_slices=100
        )
        out = apply_rules([derived, orig])
        assert out[0].series_id == "orig"  # 原始在前
        assert out[1].series_id == "der"
        assert out[1].excluded is False
        assert out[1].is_derived is True
        assert any("DERIVED" in f for f in out[1].flags)

    def test_thin_series_excluded(self) -> None:  # noqa: ANN001
        """规则：层数 < 30 排除。"""
        c = SeriesCandidate(series_id="thin", image_type=["ORIGINAL"], num_slices=20)
        out = apply_rules([c])
        assert out[0].excluded is True
        assert "层数" in out[0].exclude_reason

    def test_non_axial_excluded_but_can_be_reselected(self) -> None:  # noqa: ANN001
        """规则：非轴位排除但允许用户勾回（规则过滤 ≠ 硬删）。"""
        sag = SeriesCandidate(
            series_id="sag", image_type=["ORIGINAL"], num_slices=50, orientation="sagittal"
        )
        ax = SeriesCandidate(
            series_id="ax", image_type=["ORIGINAL"], num_slices=50, orientation="axial"
        )
        out = apply_rules([sag, ax])
        assert out[0].series_id == "ax"
        sag_out = [c for c in out if c.series_id == "sag"][0]
        assert sag_out.excluded is True
        assert "可勾回" in sag_out.exclude_reason
        # 用户勾回
        picked = select(out, user_overrides=["sag"])
        assert picked == ["sag"]

    def test_select_default_excludes_all_excluded(self) -> None:  # noqa: ANN001
        """select 默认 = 未被排除的。"""
        cands = [
            SeriesCandidate(series_id="a", image_type=["ORIGINAL"], num_slices=50),
            SeriesCandidate(series_id="b", image_type=["LOCALIZER"], num_slices=50),
        ]
        out = apply_rules(cands)
        assert select(out) == ["a"]

    def test_select_unknown_override_raises(self) -> None:  # noqa: ANN001
        """勾选未知 series_id -> KeyError。"""
        cands = [SeriesCandidate(series_id="a", num_slices=50)]
        with pytest.raises(KeyError):
            select(apply_rules(cands), user_overrides=["zzz"])
