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

    def test_save_second_input_includes_first_correction(self, committed_series) -> None:  # noqa: ANN001
        """连续两次保存：第二次以最新 MaskVersion 为基底，第一次修正保留。

        写路径落 mask_v{N}.nii.gz 不覆盖 mask.nii.gz 后，读基底必须是最新
        MaskVersion——否则第二次保存回到原始版、第一次修正丢失（回归测试）。
        顺带覆盖版本文件 key 派生规则（mask_v{N}.nii.gz）。
        """
        sid, storage, key, mask_key, settings = committed_series
        img = nib.load(str(storage.root / key))

        def _drawing_bytes(z: int) -> list[int]:
            """单层单行擦除画笔（z 层 x 5:15, y 5:6，擦掉 label-1 的一角）。"""
            draw_arr = np.zeros(img.shape, dtype=np.uint8)
            draw_arr[5:15, 5:6, z] = 1
            draw = nib.Nifti1Image(draw_arr, img.affine)
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False) as tf:
                tmp_name = tf.name
            try:
                nib.save(draw, tmp_name)
                return list(Path(tmp_name).read_bytes())
            finally:
                Path(tmp_name).unlink(missing_ok=True)

        with client() as c:
            r1 = c.post(
                f"/review/{sid}/save",
                json={"deleted_classes": [], "drawing_nifti": _drawing_bytes(6)},
            )
            assert r1.status_code == 200
            v1 = r1.json()["version"]
            assert v1 == 1
            assert r1.json()["key"].endswith("mask_v1.nii.gz")  # key 派生规则

            r2 = c.post(
                f"/review/{sid}/save",
                json={"deleted_classes": [], "drawing_nifti": _drawing_bytes(7)},
            )
            assert r2.status_code == 200
            v2 = r2.json()["version"]
            assert v2 == 2
            assert r2.json()["key"].endswith("mask_v2.nii.gz")

        # 第二次 mask：第一次修正位置（z=6）与第二次（z=7）的体素都被擦除
        img_v2 = nib.load(str(storage.root / r2.json()["key"]))
        arr2 = np.asarray(img_v2.dataobj)
        assert int(arr2[5:15, 5:6, 6].sum()) == 0  # 第一次修正仍在
        assert int(arr2[5:15, 5:6, 7].sum()) == 0  # 第二次修正已应用
        # 基线区域其余部分未受影响（label-1 每层 100 体素，擦掉 10）
        assert int((arr2[:, :, 6] == 1).sum()) == 90
        assert int((arr2[:, :, 7] == 1).sum()) == 90
        # 原始 mask.nii.gz 未被覆盖（写路径规则）
        orig = np.asarray(nib.load(str(storage.root / mask_key)).dataobj)
        assert int((orig[:, :, 6] == 1).sum()) == 100

        # 历史行未改写：v1 的 storage_key 仍指向 mask_v1.nii.gz
        from sqlalchemy import select

        from bodydonor import db
        from bodydonor.models import MaskVersion

        engine = db._engine(settings.database.url)
        try:
            with engine.connect() as conn:
                from sqlalchemy.orm import Session

                s = Session(conn)
                rows = (
                    s.execute(
                        select(MaskVersion)
                        .where(MaskVersion.series_id == sid)
                        .order_by(MaskVersion.version)
                    )
                    .scalars()
                    .all()
                )
                s.close()
        finally:
            engine.dispose()
        assert [r.version for r in rows] == [1, 2]
        assert [r.storage_key for r in rows] == [
            key.replace("image.nii.gz", "mask_v1.nii.gz"),
            key.replace("image.nii.gz", "mask_v2.nii.gz"),
        ]


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


class TestReviewPageM5:
    """M5 前端静态检查：只断言标记/文案存在于 served HTML，不谎称渲染实测。

    真实渲染（niivue 画布、键盘交互）须浏览器实测，见 M5-REPORT §3.3。
    """

    M5_MARKERS = [
        "mode-badge",  # M5-a 角落状态徽标
        "mode-prompt-pos",
        "mode-prompt-neg",
        "mode-bbox",
        "mode-scribble",
        "prompt-preview",  # M5-a 悬停预览
        "draft-list",  # M5-b per-class 草稿
        "btn-submit-nni",
        "nnInteractive · 未确认",  # M5-c 草稿角标
        "nnI ✓",  # M5-c 确认角标
        "render-quick",  # 自适应渲染开关
        "render-fine",
        "btn-strip",  # 一键剥离
        "risk-list",  # 异常驱动队列面板
        "btn-propagate",  # 修正传播
        "applyM5Defaults",
    ]

    def test_review_page_contains_m5_markers(self) -> None:
        with client() as c:
            r = c.get("/review")
        assert r.status_code == 200
        missing = [m for m in self.M5_MARKERS if m not in r.text]
        assert not missing, f"review.html 缺 M5 标记：{missing}"

    def test_review_page_prompts_contract(self) -> None:
        """前端发全量 prompts（point/bbox/scribble），不再发旧 point/negative_point。"""
        with client() as c:
            r = c.get("/review")
        assert r.status_code == 200
        assert "prompts: merged.map" in r.text  # m5RunInference 发 prompts 列表
        assert "negative_point" not in r.text  # 旧字段已不再发送
        # 旧"仅支持点提示" toast 已改（接通后不再提示）
        assert "当前后端仅支持点提示" not in r.text
    """M5-e 提交端点：修正 mask 合并进基线 → MaskVersion(source=nninteractive_guided)。"""

    def _write_correction(  # noqa: ANN001
        self, storage, key, img, target_label=1, voxels=((0, 4, 0, 4, 10),)
    ) -> str:
        """在 storage 写 nninteractive-correction/ 产物（二值修正 mask + sidecar）。"""
        import json as _json

        series_dir = "/".join(key.split("/")[:2])
        corr_arr = np.zeros(img.shape, dtype=np.uint8)
        for x0, x1, y0, y1, z in voxels:
            corr_arr[x0:x1, y0:y1, z] = 1
        corr = nib.Nifti1Image(corr_arr, img.affine)
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False) as tf:
            tmp_name = tf.name
        try:
            nib.save(corr, tmp_name)
            payload = Path(tmp_name).read_bytes()
        finally:
            Path(tmp_name).unlink(missing_ok=True)
        storage.save(f"{series_dir}/nninteractive-correction/correction_mask.nii.gz", payload)
        sidecar = {
            "source": "nninteractive_guided",
            "license": "CC BY-NC-SA 4.0",
            "target_label": target_label,
            "provenance": {
                "sessionId": "sess-1",
                "baselineTeacherVersion": None,
                "prompts": [],
                "acceptedClasses": [],
            },
        }
        storage.save(
            f"{series_dir}/nninteractive-correction/correction_source.json",
            _json.dumps(sidecar).encode("utf-8"),
        )
        return series_dir

    def test_submit_nni_creates_guided_version(self, committed_series) -> None:  # noqa: ANN001
        sid, storage, key, mask_key, settings = committed_series
        img = nib.load(str(storage.root / key))
        self._write_correction(storage, key, img)  # 16 体素在 (0:4, 0:4, z=10)
        with client() as c:
            r = c.post(
                f"/review/{sid}/submit-nni",
                json={
                    "target_label": 1,
                    "prompts": [
                        {
                            "type": "point",
                            "x": 2,
                            "y": 2,
                            "z": 10,
                            "include": True,
                            "class": "label-1",
                        }
                    ],
                    "acceptedClasses": ["label-1"],
                    "sessionId": "sess-test",
                    "baselineTeacherVersion": None,
                },
            )
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "nninteractive_guided"
        assert body["n_corrected_voxels"] == 16
        assert body["provenance"]["sessionId"] == "sess-test"
        assert body["provenance"]["acceptedClasses"] == ["label-1"]
        assert len(body["provenance"]["prompts"]) == 1
        assert body["provenance"]["targetLabel"] == 1

        from sqlalchemy import select

        from bodydonor import db
        from bodydonor.models import MaskVersion

        engine = db._engine(settings.database.url)
        try:
            with engine.connect() as conn:
                from sqlalchemy.orm import Session

                s = Session(conn)
                mv = (
                    s.execute(
                        select(MaskVersion)
                        .where(MaskVersion.series_id == sid)
                        .order_by(MaskVersion.version.desc())
                    )
                    .scalars()
                    .first()
                )
                s.close()
        finally:
            engine.dispose()
        assert mv is not None and mv.source == "nninteractive_guided"
        assert mv.provenance["prompts"][0]["x"] == 2  # prompts 可重放
        merged = nib.load(str(storage.root / mv.storage_key))
        arr = np.asarray(merged.dataobj)
        assert int((arr[0:4, 0:4, 10] == 1).sum()) == 16  # 修正体素已合并
        assert int((arr[5:15, 5:15, 10] == 1).sum()) == 100  # 基线 label-1 保留

    def test_submit_nni_without_correction_422(self, committed_series) -> None:  # noqa: ANN001
        sid, storage, key, mask_key, settings = committed_series
        with client() as c:
            r = c.post(
                f"/review/{sid}/submit-nni",
                json={
                    "target_label": 1,
                    "prompts": [
                        {
                            "type": "point",
                            "x": 2,
                            "y": 2,
                            "z": 10,
                            "include": True,
                            "class": "label-1",
                        }
                    ],
                    "acceptedClasses": ["label-1"],
                },
            )
        assert r.status_code == 422

    def test_submit_nni_second_submit_includes_first_correction(self, committed_series) -> None:  # noqa: ANN001
        """连续两次 submit-nni：第二次以最新 MaskVersion 为基底，第一次修正保留。

        修正 mask A（z=10）先提交建 v1；再写修正 mask B（z=11）提交——第二次
        合并必须基于 v1（含 A 的体素），否则 A 丢失（读路径回归测试）。
        """
        sid, storage, key, mask_key, settings = committed_series
        img = nib.load(str(storage.root / key))
        self._write_correction(storage, key, img, voxels=((0, 4, 0, 4, 10),))
        with client() as c:
            r1 = c.post(
                f"/review/{sid}/submit-nni",
                json={
                    "target_label": 1,
                    "prompts": [
                        {
                            "type": "point",
                            "x": 2,
                            "y": 2,
                            "z": 10,
                            "include": True,
                            "class": "label-1",
                        }
                    ],
                    "acceptedClasses": ["label-1"],
                    "sessionId": "sess-acc-1",
                },
            )
            assert r1.status_code == 200
            v1 = r1.json()["version"]
            assert v1 == 1

            # 第二次修正产物：不同位置（z=11）
            self._write_correction(storage, key, img, voxels=((0, 4, 0, 4, 11),))
            r2 = c.post(
                f"/review/{sid}/submit-nni",
                json={
                    "target_label": 1,
                    "prompts": [
                        {
                            "type": "point",
                            "x": 2,
                            "y": 2,
                            "z": 11,
                            "include": True,
                            "class": "label-1",
                        }
                    ],
                    "acceptedClasses": ["label-1"],
                    "sessionId": "sess-acc-2",
                },
            )
            assert r2.status_code == 200
            v2 = r2.json()["version"]
            assert v2 == 2

        merged = nib.load(str(storage.root / r2.json()["key"]))
        arr = np.asarray(merged.dataobj)
        assert int((arr[0:4, 0:4, 10] == 1).sum()) == 16  # 第一次修正保留
        assert int((arr[0:4, 0:4, 11] == 1).sum()) == 16  # 第二次修正已应用
        assert int((arr[5:15, 5:15, 10] == 1).sum()) == 100  # 基线 label-1 保留

    def test_submit_nni_target_mismatch_422(self, committed_series) -> None:  # noqa: ANN001
        sid, storage, key, mask_key, settings = committed_series
        img = nib.load(str(storage.root / key))
        self._write_correction(storage, key, img, target_label=2)
        with client() as c:
            r = c.post(
                f"/review/{sid}/submit-nni",
                json={
                    "target_label": 1,
                    "prompts": [
                        {
                            "type": "point",
                            "x": 2,
                            "y": 2,
                            "z": 10,
                            "include": True,
                            "class": "label-1",
                        }
                    ],
                    "acceptedClasses": ["label-1"],
                },
            )
        assert r.status_code == 422

    def test_save_human_source_and_provenance(self, committed_series) -> None:  # noqa: ANN001
        sid, storage, key, mask_key, settings = committed_series
        with client() as c:
            r = c.post(
                f"/review/{sid}/save",
                json={
                    "deleted_classes": ["label-2"],
                    "drawing_nifti": None,
                    "source": "human",
                    "provenance": {
                        "sessionId": "s1",
                        "baselineTeacherVersion": None,
                        "prompts": [],
                        "acceptedClasses": [],
                    },
                },
            )
        assert r.status_code == 200
        assert r.json()["source"] == "human"

    def test_save_rejects_guided_source(self, committed_series) -> None:  # noqa: ANN001
        sid, storage, key, mask_key, settings = committed_series
        with client() as c:
            r = c.post(
                f"/review/{sid}/save",
                json={
                    "deleted_classes": [],
                    "drawing_nifti": None,
                    "source": "nninteractive_guided",
                },
            )
        assert r.status_code == 422

    def test_submit_nni_resolves_baseline_version(self, committed_series) -> None:  # noqa: ANN001
        """未带 baselineTeacherVersion 时，后端查最新 MaskVersion 版本号补上。"""
        sid, storage, key, mask_key, settings = committed_series
        img = nib.load(str(storage.root / key))
        self._write_correction(storage, key, img)
        with client() as c:
            # 先 save 一次，建 v1（manual）作为修正所基于的显示基线
            r0 = c.post(
                f"/review/{sid}/save",
                json={"deleted_classes": [], "drawing_nifti": None},
            )
            assert r0.status_code == 200 and r0.json()["version"] == 1
            r = c.post(
                f"/review/{sid}/submit-nni",
                json={
                    "target_label": 1,
                    "prompts": [
                        {
                            "type": "point",
                            "x": 2,
                            "y": 2,
                            "z": 10,
                            "include": True,
                            "class": "label-1",
                        }
                    ],
                    "acceptedClasses": ["label-1"],
                    "sessionId": "sess-base",
                    # baselineTeacherVersion 不带：后端自动解析
                },
            )
        assert r.status_code == 200
        assert r.json()["provenance"]["baselineTeacherVersion"] == 1


class TestNninteractiveCorrectPrompts:
    """M5-e 后接线：/nninteractive-correct 接 prompts 列表（point/bbox/scribble）。

    mock correct_image（真实推理见 tests/test_nninteractive_backend.py + 真机
    CPU 小 patch 验证脚本）；本类只测端点契约：三种 prompt 的 JSON 校验、
    全量透传、旧 point/negative_point 兼容。
    """

    def _patch_endpoint(self, monkeypatch, captured) -> None:  # noqa: ANN001
        from bodydonor.nninteractive_backend import CorrectionResult

        def fake_correct(image_path, out_dir, prompts, **kwargs):
            captured["prompts"] = prompts
            captured["kwargs"] = kwargs
            return CorrectionResult(
                job_id=kwargs.get("job_id") or "nni-test",
                image_path=str(image_path),
                mask_path=str(out_dir / "correction_mask.nii.gz"),
                sidecar_path=str(out_dir / "correction_source.json"),
                n_target_voxels=8,
                prompts=prompts,
            )

        # 端点函数体内 from .nninteractive_backend import correct_image——
        # import 发生在调用时，patch 源模块属性即可
        import bodydonor.nninteractive_backend as nni_backend

        monkeypatch.setattr(nni_backend, "correct_image", fake_correct)

    def test_prompts_list_passthrough(self, committed_series, monkeypatch) -> None:  # noqa: ANN001
        sid, storage, key, mask_key, settings = committed_series
        captured: dict = {}
        self._patch_endpoint(monkeypatch, captured)
        with client() as c:
            r = c.post(
                f"/review/{sid}/nninteractive-correct",
                json={
                    "prompts": [
                        {"type": "point", "x": 10, "y": 10, "z": 10, "include": True},
                        {"type": "point", "x": 6, "y": 6, "z": 6, "include": False},
                        {"type": "bbox", "bbox": [[8, 12], [8, 12], [10, 11]]},
                        {"type": "scribble", "points": [[9, 9, 10], [10, 10, 10]]},
                    ],
                    "target_label": 1,
                },
            )
        assert r.status_code == 200
        assert len(captured["prompts"]) == 4
        by_type = {p["type"] for p in captured["prompts"]}
        assert by_type == {"point", "bbox", "scribble"}
        assert captured["prompts"][1]["include"] is False
        assert captured["prompts"][2]["bbox"] == [[8.0, 12.0], [8.0, 12.0], [10.0, 11.0]]
        assert captured["prompts"][3]["points"] == [[9.0, 9.0, 10.0], [10.0, 10.0, 10.0]]

    def test_prompts_bad_type_422(self, committed_series, monkeypatch) -> None:  # noqa: ANN001
        sid, storage, key, mask_key, settings = committed_series
        captured: dict = {}
        self._patch_endpoint(monkeypatch, captured)
        with client() as c:
            r = c.post(
                f"/review/{sid}/nninteractive-correct",
                json={"prompts": [{"type": "lasso", "points": [[1, 2, 3]]}]},
            )
        assert r.status_code == 422

    def test_prompts_bbox_bad_shape_422(self, committed_series, monkeypatch) -> None:  # noqa: ANN001
        sid, storage, key, mask_key, settings = committed_series
        captured: dict = {}
        self._patch_endpoint(monkeypatch, captured)
        with client() as c:
            r = c.post(
                f"/review/{sid}/nninteractive-correct",
                json={"prompts": [{"type": "bbox", "bbox": [[8, 12], [8, 12]]}]},
            )
        assert r.status_code == 422

    def test_prompts_scribble_empty_422(self, committed_series, monkeypatch) -> None:  # noqa: ANN001
        sid, storage, key, mask_key, settings = committed_series
        captured: dict = {}
        self._patch_endpoint(monkeypatch, captured)
        with client() as c:
            r = c.post(
                f"/review/{sid}/nninteractive-correct",
                json={"prompts": [{"type": "scribble", "points": []}]},
            )
        assert r.status_code == 422

    def test_old_point_contract_still_works(self, committed_series, monkeypatch) -> None:  # noqa: ANN001
        sid, storage, key, mask_key, settings = committed_series
        captured: dict = {}
        self._patch_endpoint(monkeypatch, captured)
        with client() as c:
            r = c.post(
                f"/review/{sid}/nninteractive-correct",
                json={
                    "point": {"x": 10, "y": 10, "z": 10},
                    "negative_point": {"x": 6, "y": 6, "z": 6},
                    "target_label": 1,
                },
            )
        assert r.status_code == 200
        assert len(captured["prompts"]) == 2
        assert captured["prompts"][0]["include"] is True
        assert captured["prompts"][1]["include"] is False
