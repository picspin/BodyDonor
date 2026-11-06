"""M1 API 测试：Import Bundle 端点（scan/commit/selector）+ 100MB 流式计数 + 超限 403。"""

from __future__ import annotations

import io
import os

import pytest
from fastapi.testclient import TestClient
from tests.factories import dicom_folder_zip, make_dicom_folder, make_nifti, zip_bytes

from bodydonor.app import _scan_cache, app


@pytest.fixture
def client(tmp_path, monkeypatch):  # noqa: ANN001
    """TestClient + 隔离的 data_dir / database / storage。"""
    from bodydonor import app as app_module
    from bodydonor import db
    from bodydonor.storage import LocalFsStorage

    monkeypatch.setattr(app_module.settings, "data_dir", tmp_path / "data")
    url = f"sqlite:///{tmp_path}/t.db"
    app_module.settings.database.url = url
    db.init_db(url)
    app_module.storage = LocalFsStorage(tmp_path / "data" / "storage")
    _scan_cache.clear()
    with TestClient(app) as c:
        yield c
    _scan_cache.clear()


class TestIngestScanEndpoint:
    def test_scan_nifti_zip(self, client, tmp_path) -> None:  # noqa: ANN001
        """POST /ingest/scan：NIfTI zip -> 导入报告 + outcome_id。"""
        nii = make_nifti(tmp_path / "img.nii.gz")
        data = zip_bytes({"case1/img.nii.gz": nii.read_bytes()})
        r = client.post(
            "/ingest/scan",
            files={"file": ("batch.zip", data, "application/zip")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["report"]["n_series"] == 1
        assert body["outcome_id"].startswith("scan-")
        assert len(body["report"]["series"]) == 1

    def test_scan_dicom_zip(self, client, tmp_path) -> None:  # noqa: ANN001
        """POST /ingest/scan：DICOM zip 按系列拆分。"""
        root = make_dicom_folder(tmp_path / "dicom", n_series=2, slices_per_series=40)
        data = dicom_folder_zip(root)
        r = client.post(
            "/ingest/scan",
            files={"file": ("batch.zip", data, "application/zip")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["report"]["n_series"] == 2

    def test_scan_over_100mb_rejected_413(self, client) -> None:  # noqa: ANN001
        """100MB 限制三处之一：后端流式计数超限 -> 413。

        流式计数逻辑与 limit 值解耦：read_upload_stream(stream, 1MB) 对
        cloud_demo 的 100MB 同一代码路径（proposal §4.3 配置化）。
        端到端 413 用 monkeypatch 上限到 1MB 验证（见下方 module 级测试）。
        """
        from bodydonor.extract_guard import UploadLimitError, read_upload_stream

        big = b"\x00" * (2 * (1 << 20))  # 2 MiB
        with pytest.raises(UploadLimitError) as exc_info:
            read_upload_stream(io.BytesIO(big), max_upload_mb=1)
        assert "exceeds 1 MB" in str(exc_info.value)

    def test_upload_limit_on_prem_unlimited(self) -> None:
        """对照：on_prem（max_upload_mb=None）不限制。"""
        from bodydonor.extract_guard import read_upload_stream

        big = b"\x00" * (2 * (1 << 20))
        assert len(read_upload_stream(io.BytesIO(big), max_upload_mb=None)) == len(big)

    def test_scan_zip_bomb_rejected_422(self, client) -> None:  # noqa: ANN001
        """zip 炸弹 -> 422（三限预检）。"""
        bomb = zip_bytes({"bomb.bin": b"\x00" * (1 << 20)})  # 压缩比 > 1000x
        r = client.post(
            "/ingest/scan",
            files={"file": ("bomb.zip", bomb, "application/zip")},
        )
        assert r.status_code == 422
        assert "compression ratio" in r.json()["detail"]

    def test_scan_unsupported_content_422(self, client) -> None:  # noqa: ANN001
        """zip 内无 NIfTI/DICOM -> 422。"""
        r = client.post(
            "/ingest/scan",
            files={"file": ("x.zip", zip_bytes({"a.txt": b"hi"}), "application/zip")},
        )
        assert r.status_code == 422


class TestSelectorAndCommit:
    def test_selector_lists_candidates(self, client, tmp_path) -> None:  # noqa: ANN001
        """GET /ingest/{id}/selector：规则过滤结果 + 默认选中。"""
        root = make_dicom_folder(tmp_path / "dicom", n_series=1, slices_per_series=40)
        r = client.post(
            "/ingest/scan",
            files={"file": ("b.zip", dicom_folder_zip(root), "application/zip")},
        )
        oid = r.json()["outcome_id"]
        r2 = client.get(f"/ingest/{oid}/selector")
        assert r2.status_code == 200
        body = r2.json()
        assert len(body["candidates"]) == 1
        assert body["default_selected"] == [body["candidates"][0]["series_id"]]

    def test_selector_localizer_excluded(self, client, tmp_path) -> None:  # noqa: ANN001
        """选择器：LOCALIZER 在 API 列表里标 excluded。"""
        from tests.factories import make_localizer

        root = make_dicom_folder(tmp_path / "dicom", n_series=1, slices_per_series=40)
        make_localizer(root)
        r = client.post(
            "/ingest/scan",
            files={"file": ("b.zip", dicom_folder_zip(root), "application/zip")},
        )
        oid = r.json()["outcome_id"]
        body = client.get(f"/ingest/{oid}/selector").json()
        excluded = [c for c in body["candidates"] if c["excluded"]]
        assert len(excluded) == 1
        assert excluded[0]["exclude_reason"].startswith("LOCALIZER")

    def test_commit_persists_and_counts(self, client, tmp_path) -> None:  # noqa: ANN001
        """POST /ingest/{id}/commit：落库 + 试用计数。"""
        nii = make_nifti(tmp_path / "img.nii.gz")
        r = client.post(
            "/ingest/scan",
            files={
                "file": ("b.zip", zip_bytes({"c/img.nii.gz": nii.read_bytes()}), "application/zip")
            },
        )
        oid = r.json()["outcome_id"]
        r2 = client.post(f"/ingest/{oid}/commit")
        assert r2.status_code == 200
        body = r2.json()
        assert len(body["case_ids"]) == 1
        assert body["usage"]["recorded"] is True

        # 计数在 /license/usage 可见
        r3 = client.get("/license/usage")
        assert r3.status_code == 200
        states = r3.json()["states"]
        assert len(states) >= 1
        assert states[0]["yearly"]["used"] >= 1

    def test_commit_unknown_outcome_404(self, client) -> None:  # noqa: ANN001
        r = client.post("/ingest/scan-999/commit")
        assert r.status_code == 404

    def test_commit_with_include_reselect(self, client, tmp_path) -> None:  # noqa: ANN001
        """include=：用户勾回被排除的序列。"""
        from tests.factories import make_localizer

        root = make_dicom_folder(tmp_path / "dicom", n_series=1, slices_per_series=40)
        make_localizer(root)
        r = client.post(
            "/ingest/scan",
            files={"file": ("b.zip", dicom_folder_zip(root), "application/zip")},
        )
        oid = r.json()["outcome_id"]
        body = client.get(f"/ingest/{oid}/selector").json()
        loc = [c for c in body["candidates"] if c["excluded"]][0]["series_id"]
        r2 = client.post(f"/ingest/{oid}/commit", params={"include": loc})
        assert r2.status_code == 200
        assert r2.json()["selected_series_ids"] == [loc]


class TestTrialLimitEndpoint:
    def test_over_limit_403(self, client, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        """月度 20 例达上限 -> 第 21 例 commit 403。"""
        # 预填 20 例（当月）——绕过 import 路径直接写流水
        from bodydonor import app as app_module
        from bodydonor import db
        from bodydonor.usage_counter import fingerprint_hash, record_case_usage

        engine = db._engine(app_module.settings.database.url)
        fp = fingerprint_hash()
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            for i in range(20):
                record_case_usage(s, f"pre-{i}", fp)
            s.commit()
            s.close()
        engine.dispose()

        nii = make_nifti(tmp_path / "img.nii.gz")
        r = client.post(
            "/ingest/scan",
            files={
                "file": ("b.zip", zip_bytes({"c/img.nii.gz": nii.read_bytes()}), "application/zip")
            },
        )
        oid = r.json()["outcome_id"]
        r2 = client.post(f"/ingest/{oid}/commit")
        assert r2.status_code == 403
        assert "试用限制超限" in r2.json()["detail"]


class TestUploadLimitE2E:
    def test_413_over_cloud_demo_limit(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        """端到端：cloud_demo 形态 + 超过 max_upload_mb -> 413（后端流式计数）。"""
        from fastapi.testclient import TestClient as TC  # noqa: N817 — TC 测试内惯用

        from bodydonor import app as app_module
        from bodydonor import db
        from bodydonor.config import Settings
        from bodydonor.storage import LocalFsStorage

        url = f"sqlite:///{tmp_path}/t.db"
        settings = Settings(
            data_dir=tmp_path / "data",
            deployment_mode="cloud_demo",
        )
        settings.database.url = url
        monkeypatch.setattr(app_module, "settings", settings)
        monkeypatch.setattr(
            app_module, "storage", LocalFsStorage(tmp_path / "data" / "storage")
        )
        db.init_db(url)
        _scan_cache.clear()
        with TC(app_module.app) as c:
            # 不可压缩随机字节（101 MiB）：zip 本身 ~101 MB，zip 后仍超 100 MB 上限，
            # 触发流式计数（若用零字节，zip 压缩后 103KB 反而会先命中压缩比检查 422）
            big = os.urandom(101 * (1 << 20))
            r = c.post(
                "/ingest/scan",
                files={"file": ("big.zip", zip_bytes({"b.bin": big}), "application/zip")},
            )
            assert r.status_code == 413
            assert "exceeds 100 MB" in r.json()["detail"]
        _scan_cache.clear()
