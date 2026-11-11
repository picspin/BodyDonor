"""M2：niivue 审阅 API 端点测试（meta/image/mask/seg-meta/save + affine 守卫）。"""

from __future__ import annotations

from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
from fastapi.testclient import TestClient
from tests.factories import make_nifti

from bodydonor.app import app
from bodydonor.pipeline import commit, scan
from bodydonor.storage import LocalFsStorage


@pytest.fixture
def committed_series(tmp_path, settings, monkeypatch, isolate_db_url):  # noqa: ANN001
    """commit 一个合成 NIfTI 序列（40 层，通过 <30 层防线）。

    DB URL 用 isolate_db_url（app.settings.database.url，与审阅端点同一个库）；
    app.storage 实例同步指向同一存储（审阅端点 _get_storage 用它）。
    """
    from bodydonor import app as app_module

    path = make_nifti(tmp_path / "img.nii.gz", shape=(40, 32, 32))
    settings.database.url = isolate_db_url
    outcome = scan(path, settings)
    storage = LocalFsStorage(tmp_path / "storage")
    monkeypatch.setattr(app_module, "storage", storage)
    committed = commit(outcome, settings, storage)
    sid = committed.selected_series_ids[0]

    # 落一个多类 mask（与 image 同 affine；label 1/2 两类）
    from sqlalchemy import select

    from bodydonor import db
    from bodydonor.models import Series

    engine = db._engine(settings.database.url)
    try:
        with engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            row = s.execute(select(Series).where(Series.series_id == sid)).scalar_one()
            key = row.storage_key
            s.close()
    finally:
        engine.dispose()

    img = nib.load(str(storage.root / key))
    mask_arr = np.zeros(img.shape, dtype=np.uint8)
    mask_arr[5:15, 5:15, 5:15] = 1
    mask_arr[20:28, 20:28, 20:28] = 2
    mask = nib.Nifti1Image(mask_arr, img.affine)
    mask_key = key.replace("image.nii.gz", "mask.nii.gz")
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False) as tf:
        tmp_name = tf.name
    try:
        nib.save(mask, tmp_name)
        payload = Path(tmp_name).read_bytes()
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    storage.save(mask_key, payload)

    return sid, storage, key, mask_key, settings


def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolate_db_url(monkeypatch, tmp_path: Path):  # noqa: ANN001
    """审阅端点用 app.py 全局 settings.database.url——测试指向临时库。

    app.settings 与 fixture 的 settings 不是同一对象；这里统一改 app.settings，
    committed_series fixture 也用 app.settings.database.url（同一个库）。
    """
    from bodydonor import app as app_module

    monkeypatch.setattr(
        app_module.settings,
        "database",
        type(app_module.settings.database)(url=f"sqlite:///{tmp_path}/review.db"),
    )
    return app_module.settings.database.url


class TestReviewPage:
    def test_review_page_served(self) -> None:
        """审阅页 HTML 可访问，含 niivue 引用。"""
        with client() as c:
            r = c.get("/review")
        assert r.status_code == 200
        assert "niivue" in r.text
        assert "BodyDonor" in r.text

    def test_static_js_served(self) -> None:
        """vendored niivue.umd.js 可访问（不走 CDN）。"""
        with client() as c:
            r = c.get("/static/niivue.umd.js")
        assert r.status_code == 200
        assert len(r.content) > 100_000

    def test_static_path_traversal_rejected(self) -> None:
        with client() as c:
            r = c.get("/static/..%2Freview.html")
        assert r.status_code in (404, 400)


class TestReviewMeta:
    def test_meta_with_mask(self, committed_series) -> None:  # noqa: ANN001
        sid, storage, key, mask_key, settings = committed_series
        with client() as c:
            r = c.get(f"/review/{sid}/meta")
        assert r.status_code == 200
        body = r.json()
        assert body["series_id"] == sid
        assert body["num_slices"] == 40
        assert body["has_mask"] is True

    def test_meta_unknown_series_404(self) -> None:
        with client() as c:
            r = c.get("/review/no-such-series/meta")
        assert r.status_code == 404

    def test_image_served(self, committed_series) -> None:  # noqa: ANN001
        import gzip

        sid, storage, *_ = committed_series
        with client() as c:
            r = c.get(f"/review/{sid}/image.nii.gz")
        assert r.status_code == 200
        assert len(r.content) > 100
        # 响应是 .nii.gz（gzip 压缩）；解压后 NIfTI magic 在 344 偏移
        raw = gzip.decompress(r.content)
        assert raw[344:348] in (b"n+1\x00", b"ni1\x00")

    def test_mask_served(self, committed_series) -> None:  # noqa: ANN001
        sid, *_ = committed_series
        with client() as c:
            r = c.get(f"/review/{sid}/mask.nii.gz")
        assert r.status_code == 200

    def test_seg_meta_lists_classes(self, committed_series) -> None:  # noqa: ANN001
        sid, *_ = committed_series
        with client() as c:
            r = c.get(f"/review/{sid}/seg-meta")
        assert r.status_code == 200
        body = r.json()
        classes = {s["label_value"] for s in body}
        assert classes == {1, 2}
        assert all(s["n_voxels"] > 0 for s in body)


class TestReviewSave:
    def test_save_deleted_class_new_version(self, committed_series, tmp_path) -> None:  # noqa: ANN001
        """整类删除 -> 新 MaskVersion（source=manual），mask 体素清零。"""
        sid, storage, key, mask_key, settings = committed_series
        with client() as c:
            r = c.post(
                f"/review/{sid}/save",
                json={"deleted_classes": ["label-1"], "drawing_nifti": None},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "manual"
        assert body["version"] == 1

        # 读回新 mask：label-1 清零，label-2 保留
        from sqlalchemy import select

        from bodydonor import db
        from bodydonor.models import MaskVersion

        engine = db._engine(settings.database.url)
        try:
            with engine.connect() as conn:
                from sqlalchemy.orm import Session

                s = Session(conn)
                mv = (
                    s.execute(select(MaskVersion).where(MaskVersion.series_id == sid))
                    .scalars()
                    .first()
                )
                s.close()
        finally:
            engine.dispose()
        assert mv is not None
        assert mv.source == "manual"
        new_img = nib.load(str(storage.root / mv.storage_key))
        arr = np.asarray(new_img.dataobj)
        assert int((arr == 1).sum()) == 0
        assert int((arr == 2).sum()) > 0

    def test_save_affine_mismatch_rejected(self, committed_series, tmp_path) -> None:  # noqa: ANN001
        """(b) affine 守卫：mask 与原图 affine 被篡改 -> raise（422/500，不静默重采样）。"""
        sid, storage, key, mask_key, settings = committed_series
        # 篡改磁盘上的 mask affine（平移不一致）
        img = nib.load(str(storage.root / key))
        bad_affine = img.affine.copy()
        bad_affine[0, 3] += 7.0
        bad_mask = nib.Nifti1Image(np.zeros(img.shape, dtype=np.uint8), bad_affine)
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False) as tf:
            tmp_name = tf.name
        try:
            nib.save(bad_mask, tmp_name)
            payload = Path(tmp_name).read_bytes()
        finally:
            Path(tmp_name).unlink(missing_ok=True)
        storage.save(mask_key, payload)

        with client() as c:
            r = c.post(
                f"/review/{sid}/save",
                json={"deleted_classes": ["label-1"], "drawing_nifti": None},
            )
        assert r.status_code in (422, 500)
        assert "affine" in r.text

    def test_save_drawing_erases(self, committed_series, tmp_path) -> None:  # noqa: ANN001
        """画笔粗修：drawing 非零体素从 mask 删除。"""
        sid, storage, key, mask_key, settings = committed_series
        img = nib.load(str(storage.root / key))
        draw_arr = np.zeros(img.shape, dtype=np.uint8)
        draw_arr[5:15, 5:15, 5:15] = 1  # 擦掉 label-1 区域
        draw = nib.Nifti1Image(draw_arr, img.affine)
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False) as tf:
            tmp_name = tf.name
        try:
            nib.save(draw, tmp_name)
            drawing_bytes = list(Path(tmp_name).read_bytes())
        finally:
            Path(tmp_name).unlink(missing_ok=True)

        with client() as c:
            r = c.post(
                f"/review/{sid}/save",
                json={"deleted_classes": [], "drawing_nifti": drawing_bytes},
            )
        assert r.status_code == 200

    def test_save_no_mask_422(self, tmp_path, settings, isolate_db_url) -> None:  # noqa: ANN001
        """无 mask 序列 -> 422。"""
        path = make_nifti(tmp_path / "img2.nii.gz", shape=(40, 32, 32))
        settings.database.url = isolate_db_url
        outcome = scan(path, settings)
        storage = LocalFsStorage(tmp_path / "storage3")
        committed = commit(outcome, settings, storage)
        sid = committed.selected_series_ids[0]
        with client() as c:
            r = c.post(
                f"/review/{sid}/save",
                json={"deleted_classes": ["label-1"], "drawing_nifti": None},
            )
        assert r.status_code == 422


class TestReviewPropagate:
    """M5 修正传播端点：本层形态 → 相邻层空白处，不越类。"""

    def test_propagate_to_neighbours(self, committed_series) -> None:  # noqa: ANN001
        # fixture mask：label-1 在 x/y/z 5:15（每层 100 体素）；取块边缘层
        # z=14：z=13 已有该类（不重复写），只向空白的 z=15 传播 100 体素
        sid, storage, key, mask_key, settings = committed_series
        with client() as c:
            r = c.post(
                f"/review/{sid}/propagate",
                json={"slice": 14, "class_label": 1, "radius": 1},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["propagated_voxels"] == 100
        assert body["per_slice"] == {"15": 100}
        assert body["source"] == "manual"
        img = nib.load(str(storage.root / body["key"]))
        arr = np.asarray(img.dataobj)
        assert int((arr[:, :, 15] == 1).sum()) == 100
        assert int((arr[:, :, 14] == 1).sum()) == 100

    def test_propagate_zero_refuses_empty_version(self, committed_series) -> None:  # noqa: ANN001
        sid, storage, key, mask_key, settings = committed_series
        with client() as c:
            r = c.post(
                f"/review/{sid}/propagate",
                json={"slice": 0, "class_label": 1, "radius": 1},
            )
        # z=0 层无 label-1：0 传播 → 422，不建空版本
        assert r.status_code == 422

    def test_propagate_bad_slice_422(self, committed_series) -> None:  # noqa: ANN001
        sid, storage, key, mask_key, settings = committed_series
        with client() as c:
            r = c.post(
                f"/review/{sid}/propagate",
                json={"slice": 99, "class_label": 1},
            )
        assert r.status_code == 422

    def test_propagate_unknown_series_404(self) -> None:
        with client() as c:
            r = c.post(
                "/review/no-such-series/propagate",
                json={"slice": 10, "class_label": 1},
            )
        assert r.status_code == 404


class TestReviewRiskQueue:
    """M5 异常驱动队列端点：M4 实测 Dice + 阶段 5 一致性 flag。"""

    def test_risk_queue_order(self, committed_series) -> None:  # noqa: ANN001
        sid, storage, key, mask_key, settings = committed_series
        with client() as c:
            r = c.get(f"/review/{sid}/risk-queue")
        assert r.status_code == 200
        body = r.json()
        q = body["queue"]
        assert len(q) == 15  # 学生 15 类
        assert q[0]["class"] == "prostate"  # M4 实测最低 0.6099 + flagged
        assert q[0]["risk"] == "high"
        assert q[-1]["class"] == "liver"  # 0.9433
        assert "student_holdout_results.json" in body["sources"]["per_class_dice"]

    def test_risk_queue_unknown_series_404(self) -> None:
        with client() as c:
            r = c.get("/review/no-such-series/risk-queue")
        assert r.status_code == 404
