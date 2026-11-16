"""S3F 第 1 项：显式 modality，不硬编码 CT。

- NIfTI 摄入无显式 modality -> IngestError（不静默按 CT）
- 显式 MR/PET/X-ray 走通，SeriesRecord.modality 取传入值（合成数据）
- CLI --modality 可选（纯 DICOM 不强制）；zip/API NIfTI 缺省 -> IngestError（B1）
- app /agent-context/preview 演示元数据显式 modality 入参（缺省 unknown 占位）
- export_seg.SourceSeriesInfo.modality 必填（无 "CT" 缺省）
- 全部合成 fixture（无本地产物依赖，CI 可跑）
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from tests.factories import make_nifti, zip_bytes

from bodydonor.app import _scan_cache, app
from bodydonor.export_seg import SourceSeriesInfo
from bodydonor.ingest import IngestError, ingest

# ---------------------------------------------------------------------------
# NIfTI 显式 modality（ingest 层）
# ---------------------------------------------------------------------------


class TestNiftiExplicitModality:
    def test_no_modality_raises(self, tmp_path) -> None:  # noqa: ANN001
        """NIfTI 无显式 modality -> IngestError，不静默按 CT。"""
        nii = make_nifti(tmp_path / "img.nii.gz")
        with pytest.raises(IngestError, match="modality"):
            ingest(nii)
        with pytest.raises(IngestError, match="modality"):
            ingest(nii, modality=None)

    def test_empty_modality_raises(self, tmp_path) -> None:  # noqa: ANN001
        """空串 modality 同样拒绝（不静默按 CT）。"""
        nii = make_nifti(tmp_path / "img.nii.gz")
        with pytest.raises(IngestError, match="modality"):
            ingest(nii, modality="")

    def test_explicit_mr_pet_xray_ingest(self, tmp_path) -> None:  # noqa: ANN001
        """显式 MR/PET/X-ray 摄入走通，modality 取传入值（合成数据）。"""
        for mod in ("MR", "PET", "X-ray"):
            nii = make_nifti(tmp_path / f"{mod.lower()}.nii.gz")
            report = ingest(nii, modality=mod)
            assert report.input_kind == "nifti"
            assert report.n_series == 1
            assert report.series[0].modality == mod

    def test_zip_nifti_no_modality_raises(self, tmp_path) -> None:  # noqa: ANN001
        """B1：zip 内 NIfTI 无显式 modality -> IngestError（不静默记 unknown）。"""
        nii = make_nifti(tmp_path / "img.nii.gz")
        data = zip_bytes({"c/img.nii.gz": nii.read_bytes()})
        with pytest.raises(IngestError, match="modality"):
            ingest(io.BytesIO(data))

    def test_zip_nifti_explicit_modality(self, tmp_path) -> None:  # noqa: ANN001
        """zip 内 NIfTI 显式 modality -> 传入值。"""
        nii = make_nifti(tmp_path / "img.nii.gz")
        data = zip_bytes({"c/img.nii.gz": nii.read_bytes()})
        report = ingest(io.BytesIO(data), modality="MR")
        assert report.series[0].modality == "MR"

    def test_dicom_folder_ignores_modality_param(self, tmp_path) -> None:  # noqa: ANN001
        """DICOM 路径以头内 Modality 为准（modality 参数不改变 DICOM 行为）。"""
        from tests.factories import make_dicom_folder

        root = make_dicom_folder(tmp_path / "dicom", n_series=1, slices_per_series=10)
        r1 = ingest(root)
        r2 = ingest(root, modality="PET")
        assert r1.n_series == r2.n_series == 1
        # W6：头内标签为准——modality 字段等于 DICOM 头内值，与入参无关
        assert r1.series[0].modality == "CT"
        assert r2.series[0].modality == "CT"


class TestCliImportModality:
    def test_cli_dicom_import_without_modality_ok(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        """W5/W2：纯 DICOM 导入不带 --modality 走通（CLI 参数可选，头内为准）。"""
        from tests.factories import make_dicom_folder
        from typer.testing import CliRunner

        import bodydonor.cli as cli_mod

        root = make_dicom_folder(tmp_path / "dicom", n_series=1, slices_per_series=10)
        monkeypatch.setattr(cli_mod, "_settings", _settings_no_write(tmp_path))
        runner = CliRunner()
        result = runner.invoke(cli_mod.app, ["import", str(root), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "Series" in result.output

    def test_cli_nifti_import_without_modality_fails(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        """W5/B1：NIfTI 导入不带 --modality -> ingest 层报错，exit 1。"""
        from tests.factories import make_nifti
        from typer.testing import CliRunner

        import bodydonor.cli as cli_mod

        nii = make_nifti(tmp_path / "img.nii.gz")
        monkeypatch.setattr(cli_mod, "_settings", _settings_no_write(tmp_path))
        runner = CliRunner()
        result = runner.invoke(cli_mod.app, ["import", str(nii), "--dry-run"])
        assert result.exit_code == 1
        assert "modality" in result.output

    def test_cli_nifti_import_with_modality_ok(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        """W5：NIfTI 导入带 --modality MR 走通（dry-run）。"""
        from tests.factories import make_nifti
        from typer.testing import CliRunner

        import bodydonor.cli as cli_mod

        nii = make_nifti(tmp_path / "img.nii.gz")
        monkeypatch.setattr(cli_mod, "_settings", _settings_no_write(tmp_path))
        runner = CliRunner()
        result = runner.invoke(
            cli_mod.app, ["import", str(nii), "--dry-run", "--modality", "MR"]
        )
        assert result.exit_code == 0, result.output
        assert "MR" in result.output


# ---------------------------------------------------------------------------
# export_seg.SourceSeriesInfo.modality 必填
# ---------------------------------------------------------------------------


def _settings_no_write(tmp_path):  # noqa: ANN001
    """CLI 测试 settings：data_dir 指 tmp（dry-run 不落盘，不读项目真实配置）。"""
    from bodydonor.config import load_settings

    def _f():
        s = load_settings()
        s.data_dir = str(tmp_path / "data")
        return s

    return _f


class TestSourceSeriesInfoModality:
    def test_modality_required_no_ct_default(self) -> None:
        """modality 无 "CT" 缺省：dataclass 必填字段（省略即 TypeError）。"""
        with pytest.raises(TypeError):
            SourceSeriesInfo(
                series_uid="1.2.3",
                study_uid="1.2.4",
                rows=32,
                columns=32,
            )

    def test_modality_value_rounds_into_synthetic_source(self) -> None:
        """合成源实例的 Modality 标签取 source.modality（非 CT 占位）。"""
        from bodydonor.export_seg import _build_source_images

        src = SourceSeriesInfo(
            series_uid="1.2.826.0.1.mr.src",
            study_uid="1.2.826.0.1.mr.study",
            modality="MR",
            rows=32,
            columns=32,
        )
        images = _build_source_images(src, rows=32, columns=32, n_slices=4)
        assert all(str(i.Modality) == "MR" for i in images)

    def test_xray_internal_alias_maps_to_cr(self) -> None:
        """S3 收口轮 ⑥：内部别名 "X-ray" 非 DICOM CS 合法值——合成源实例按
        CR 规范化（源信息保留语义见 test_export_seg.py DX/CR 用例：真实 DX/CR
        源各自保留，不再一律转 CR）。"""
        from bodydonor.export_seg import _build_source_images

        src = SourceSeriesInfo(
            series_uid="1.2.826.0.1.cr.src",
            study_uid="1.2.826.0.1.cr.study",
            modality="X-ray",
            rows=32,
            columns=32,
        )
        images = _build_source_images(src, rows=32, columns=32, n_slices=4)
        assert all(str(i.Modality) == "CR" for i in images)
        # S3R4 W-1：SOP Class 与 Modality 一致——X-ray 先归一化为 CR 再查映射，
        # 不得落默认 CT Storage（同一实例 Modality=CR 而 SOPClass=CT 自相矛盾）
        assert all(
            str(i.SOPClassUID) == "1.2.840.10008.5.1.4.1.1.1" for i in images
        )


# ---------------------------------------------------------------------------
# app 演示路径：/agent-context/preview modality 显式入参
# ---------------------------------------------------------------------------


class TestAppPreviewModality:
    def test_preview_modality_param_flows_through(self) -> None:
        """modality 查询参数进 AgentContext（显式 MR 不再是硬编码 CT）。"""
        with TestClient(app) as c:
            r = c.post(
                "/agent-context/preview",
                params={"case_id": "c-1", "series_id": "s-1", "modality": "MR"},
            )
            assert r.status_code == 200
            assert r.json()["context"]["series_metadata"]["modality"] == "MR"

    def test_preview_default_is_unknown_not_ct(self) -> None:
        """缺省占位是 unknown，不冒充真实 CT。"""
        with TestClient(app) as c:
            r = c.post("/agent-context/preview", params={"case_id": "c-1", "series_id": "s-1"})
            assert r.status_code == 200
            assert r.json()["context"]["series_metadata"]["modality"] == "unknown"


# ---------------------------------------------------------------------------
# API /ingest/scan：modality 参数接线
# ---------------------------------------------------------------------------


class TestApiScanModality:
    def test_scan_zip_nifti_unknown_without_param(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        """B1：API zip NIfTI 无 modality 参数 -> 422（IngestError 转 422）；带参 200。"""
        from bodydonor import app as app_module
        from bodydonor.storage import LocalFsStorage

        monkeypatch.setattr(app_module.settings, "data_dir", tmp_path / "data")
        app_module.settings.database.url = f"sqlite:///{tmp_path}/t.db"
        app_module.storage = LocalFsStorage(tmp_path / "data" / "storage")
        _scan_cache.clear()
        nii = make_nifti(tmp_path / "img.nii.gz")
        data = zip_bytes({"c/img.nii.gz": nii.read_bytes()})
        with TestClient(app) as c:
            r = c.post("/ingest/scan", files={"file": ("b.zip", data, "application/zip")})
            assert r.status_code == 422
            assert "modality" in r.json()["detail"]
            r2 = c.post(
                "/ingest/scan",
                files={"file": ("b.zip", data, "application/zip")},
                params={"modality": "PET"},
            )
            assert r2.status_code == 200
            assert r2.json()["report"]["series"][0]["modality"] == "PET"
        _scan_cache.clear()
