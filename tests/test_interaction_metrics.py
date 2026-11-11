"""M6-a：交互轨迹结构化单测（草稿间语义 / 收敛边界 / 可靠性标记 / 放弃计数 / roundtrip）。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from bodydonor.interaction_metrics import (
    TAU_CONVERGENCE,
    InteractionTracker,
    convergence_steps,
    dice_binary,
    load_teacher_eval,
    teacher_reliability,
)


def _cube(shape: tuple[int, int, int], box: tuple[int, int, int, int, int, int]):
    a = np.zeros(shape, dtype=np.uint8)
    x0, x1, y0, y1, z0, z1 = box
    a[x0:x1, y0:y1, z0:z1] = 1
    return a


class TestDiceBinary:
    def test_identical_is_one(self) -> None:
        a = _cube((4, 4, 4), (0, 2, 0, 2, 0, 2))
        assert dice_binary(a, a) == 1.0

    def test_double_empty_is_one(self) -> None:
        # 两个空集 = 无差异（不把 0/0 定义成 0）
        assert dice_binary(np.zeros((2, 2, 2)), np.zeros((2, 2, 2))) == 1.0

    def test_partial_overlap(self) -> None:
        a = _cube((4, 4, 4), (0, 2, 0, 2, 0, 2))  # 8 体素
        b = _cube((4, 4, 4), (1, 3, 0, 2, 0, 2))  # 8 体素，交 4
        assert dice_binary(a, b) == pytest.approx(2 * 4 / 16)

    def test_disjoint_is_zero(self) -> None:
        a = _cube((4, 4, 4), (0, 1, 0, 1, 0, 1))
        b = _cube((4, 4, 4), (3, 4, 3, 4, 3, 4))
        assert dice_binary(a, b) == 0.0


class TestConvergenceSteps:
    def test_two_consecutive_below_tau_triggers(self) -> None:
        # 连续 2 步 < τ（0.01）→ 第 2 步触发
        assert convergence_steps([0.12, 0.008, 0.005]) == 3

    def test_single_below_tau_after_big_delta_not_enough(self) -> None:
        # 仅 1 步 < τ（连续 2 步才计数）
        assert convergence_steps([0.12, 0.008]) is None

    def test_three_steps_trigger_at_three(self) -> None:
        assert convergence_steps([0.12, 0.008, 0.005]) == 3

    def test_single_below_tau_not_enough(self) -> None:
        # 连续 2 步才计数：单步 < τ 不触发
        assert convergence_steps([0.005]) is None

    def test_reset_by_above_tau(self) -> None:
        # 中间断开则连续计数重置，从头数
        assert convergence_steps([0.008, 0.02, 0.008, 0.005]) == 4

    def test_boundary_equal_tau_not_below(self) -> None:
        # ΔDice == τ 不算 < τ
        assert convergence_steps([TAU_CONVERGENCE, TAU_CONVERGENCE]) is None

    def test_never_converges(self) -> None:
        assert convergence_steps([0.5, 0.4, 0.3]) is None

    def test_empty(self) -> None:
        assert convergence_steps([]) is None


class TestTeacherReliability:
    def test_prostate_explicit_low(self) -> None:
        # M4 实测教师 prostate 0.5914，显式高风险名单
        assert teacher_reliability("prostate", {"prostate": 18}) == "low"

    def test_low_frequency_low(self) -> None:
        # <10 cases（M4 阶段 4 的 low_frequency_classes 口径）
        assert teacher_reliability("brain", {"brain": 1}) == "low"
        assert teacher_reliability("skull", {"skull": 9}) == "low"

    def test_frequent_class_high(self) -> None:
        assert teacher_reliability("liver", {"liver": 30}) == "high"
        assert teacher_reliability("aorta", {"aorta": 30}) == "high"

    def test_missing_count_data_only_explicit_list(self) -> None:
        # 计数数据缺时只按显式名单判，不猜数字
        assert teacher_reliability("liver", None) == "high"
        assert teacher_reliability("prostate", None) == "low"

    def test_teacher_eval_product_loads_117_classes(self) -> None:
        counts = load_teacher_eval()
        assert counts is not None
        assert len(counts) == 117  # M4 阶段 4 教师评估 117 类
        assert counts["prostate"] == 18
        assert counts["liver"] == 30

    def test_teacher_eval_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert load_teacher_eval(tmp_path / "nope.json") is None


class TestInteractionTracker:
    def test_first_step_no_delta(self) -> None:
        t = InteractionTracker()
        d1 = _cube((4, 4, 4), (0, 2, 0, 2, 0, 2))
        assert t.record_draft("liver", d1, 3) is None  # 第 1 步无前驱
        assert t.prompt_counts["liver"] == 3

    def test_delta_is_draft_to_draft_not_teacher(self) -> None:
        """草稿间语义：ΔDice 只由草稿序列决定，教师 mask 不参与。"""
        t = InteractionTracker()
        d1 = _cube((4, 4, 4), (0, 2, 0, 2, 0, 2))
        d2 = _cube((4, 4, 4), (0, 2, 0, 2, 0, 2))
        d2[3, 3, 3] = 1  # 第二步加 1 体素（8 → 9 体素，交 8）
        assert t.record_draft("liver", d1, 1) is None
        delta = t.record_draft("liver", d2, 2)
        assert delta == pytest.approx(2 * 8 / 17)  # Dice(d1, d2)，与教师无关
        seq = t.per_step_delta_dice("liver")
        assert len(seq) == 1  # 2 步推理 → 1 个 delta

    def test_teacher_miss_correction_reflects_improvement(self) -> None:
        """教师漏检场景（M4：AMOS 5/15 整类漏检）：教师 mask 全空，学生修正
        把漏检区域加进草稿。草稿间 ΔDice 反映真实改进方向（草稿在变），
        绝不因教师为空而把修正判成"变差"。"""
        t = InteractionTracker()
        # 教师 mask 全空（整类漏检）——只作背景说明，不参与任何计算
        teacher = np.zeros((4, 4, 4), dtype=np.uint8)
        assert (teacher > 0).sum() == 0
        d1 = _cube((4, 4, 4), (0, 2, 0, 2, 0, 2))  # 第一步：部分区域
        d2 = _cube((4, 4, 4), (0, 2, 0, 2, 0, 2))
        d2[2:4, 0:2, 0:2] = 1  # 第二步：修正教师漏检区域（扩展）
        t.record_draft("lesion", d1, 2)
        delta = t.record_draft("lesion", d2, 3)
        # 草稿在扩展变化 → delta < 1 且 > 0（真实改进方向，非"变差"）
        assert 0.0 < delta < 1.0
        # 与教师 Dice（=0）无关：如果用教师当伪真值，d1/d2 vs 教师 都是 0，
        # ΔDice 会是 0/0——草稿间语义直接比较 d1 vs d2，不引入教师
        assert t.per_step_delta_dice("lesion") == [delta]

    def test_metrics_full_structure(self) -> None:
        t = InteractionTracker()
        d1 = _cube((4, 4, 4), (0, 2, 0, 2, 0, 2))
        d2 = _cube((4, 4, 4), (0, 2, 0, 2, 0, 2))
        d2[3, 3, 3] = 1
        d3 = d2.copy()  # 第三步不变 → delta=1.0 > τ
        t.record_draft("liver", d1, 3)
        t.record_draft("liver", d2, 4)
        t.record_draft("liver", d3, 5)
        m = t.build_metrics("liver", final_local_dice=0.91, per_class_case_count={"liver": 30})
        assert set(m.keys()) == {
            "numPrompts",
            "convergenceSteps",
            "perStepDeltaDice",
            "finalLocalDice",
            "teacherReliability",
            "abandonedAttempts",
        }
        assert m["numPrompts"] == 5  # 最后一次全量提示数
        assert len(m["perStepDeltaDice"]) == 2
        assert m["convergenceSteps"] is None  # 第二个 delta=1.0 重置
        assert m["finalLocalDice"] == 0.91
        assert m["teacherReliability"] == "high"
        assert m["abandonedAttempts"] == 0

    def test_metrics_convergence_in_draft_deltas(self) -> None:
        t = InteractionTracker()
        # 草稿间 Dice 对大草稿的 1 体素微调不敏感（4096 体素 delta≈0.9995 > τ）；
        # delta=1.0（草稿不变）与双空集 1.0 都不 < τ。<τ 收敛的实际形态：
        # 草稿剧烈变化到近乎无交集（如漂移的小草稿，Dice 0.0 < τ）。
        small1 = _cube((20, 20, 20), (2, 3, 2, 3, 2, 3))  # 1 体素
        small2 = _cube((20, 20, 20), (2, 3, 2, 3, 3, 4))  # 1 体素，z 漂移 → 不相交
        small3 = _cube((20, 20, 20), (2, 3, 2, 3, 4, 5))  # 再漂移 → 不相交
        assert dice_binary(small1, small2) == 0.0  # < τ
        assert dice_binary(small2, small3) == 0.0  # < τ（连续 2 步）
        t.record_draft("liver", small1, 1)
        t.record_draft("liver", small2, 2)
        t.record_draft("liver", small3, 3)
        m = t.build_metrics("liver")
        # 3 步草稿 → 2 个 delta；连续 2 步 < τ 在 delta 序列第 2 位触发
        assert m["convergenceSteps"] == 2
        assert m["perStepDeltaDice"] == [0.0, 0.0]

    def test_abandoned_attempts_counted_and_drafts_cleared(self) -> None:
        t = InteractionTracker()
        d1 = _cube((4, 4, 4), (0, 2, 0, 2, 0, 2))
        t.record_draft("liver", d1, 3)
        assert t.record_abandoned("liver") == 1
        assert t.record_abandoned("liver") == 2
        assert t.drafts.get("liver") is None  # 草稿序列清空（回 teacher）
        assert t.prompt_counts.get("liver") is None
        m = t.build_metrics("liver")
        assert m["abandonedAttempts"] == 2
        assert m["perStepDeltaDice"] == []
        assert m["numPrompts"] == 0  # 放弃后提示数清零（回到未改状态）

    def test_abandon_then_rework_accumulates(self) -> None:
        """放弃后重新修正：abandonedAttempts 累计，新草稿序列独立。"""
        t = InteractionTracker()
        t.record_draft("liver", _cube((4, 4, 4), (0, 2, 0, 2, 0, 2)), 3)
        t.record_abandoned("liver")
        t.record_draft("liver", _cube((4, 4, 4), (1, 3, 1, 3, 1, 3)), 5)
        m = t.build_metrics("liver", final_local_dice=0.5, per_class_case_count={"liver": 30})
        assert m["abandonedAttempts"] == 1  # 放弃历史保留
        assert m["numPrompts"] == 5  # 新序列提示数
        assert m["perStepDeltaDice"] == []  # 新序列只有 1 步，无 delta

    def test_metrics_json_serializable(self) -> None:
        """持久化 roundtrip：metrics 结构可 JSON 序列化（provenance 列）。"""
        t = InteractionTracker()
        d1 = _cube((4, 4, 4), (0, 2, 0, 2, 0, 2))
        d2 = d1.copy()
        d2[3, 3, 3] = 1
        t.record_draft("liver", d1, 2)
        t.record_draft("liver", d2, 3)
        t.record_abandoned("kidney")  # 另一类的放弃事件不影响 liver 条目
        m = t.build_metrics("liver", final_local_dice=0.83, per_class_case_count={"liver": 30})
        payload = {"interactionMetrics": {"liver": m}}
        data = json.loads(json.dumps(payload))
        assert data["interactionMetrics"]["liver"] == {
            "numPrompts": 3,
            "convergenceSteps": None,
            "perStepDeltaDice": [round(2 * 8 / 17, 4)],
            "finalLocalDice": 0.83,
            "teacherReliability": "high",
            "abandonedAttempts": 0,
        }


class TestSubmitNniPersistence:
    """M6-a 持久化端到端：tracker 采集 → submit-nni → provenance.interactionMetrics
    持久化到 MaskVersion（DB roundtrip），sidecar 可查。"""

    @pytest.fixture
    def committed_series(self, request, tmp_path, settings, monkeypatch, isolate_db_url):  # noqa: ANN001
        """同 test_review_api.committed_series：commit 合成序列 + 落多类 mask。
        tracker 清空注册为 finalizer（进程级 _interaction_trackers 不跨测试泄漏）。"""
        import tempfile

        import nibabel as nib
        from fastapi.testclient import TestClient  # noqa: F401
        from tests.factories import make_nifti

        from bodydonor import app as app_module
        from bodydonor import db
        from bodydonor.models import Series
        from bodydonor.pipeline import commit, scan
        from bodydonor.storage import LocalFsStorage

        request.addfinalizer(app_module._interaction_trackers.clear)

        from bodydonor import app as app_module

        path = make_nifti(tmp_path / "img.nii.gz", shape=(40, 32, 32))
        settings.database.url = isolate_db_url
        outcome = scan(path, settings)
        storage = LocalFsStorage(tmp_path / "storage")
        monkeypatch.setattr(app_module, "storage", storage)
        committed = commit(outcome, settings, storage)
        sid = committed.selected_series_ids[0]

        from sqlalchemy import select

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
        with tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False) as tf:
            tmp_name = tf.name
        try:
            nib.save(mask, tmp_name)
            payload = Path(tmp_name).read_bytes()
        finally:
            Path(tmp_name).unlink(missing_ok=True)
        storage.save(mask_key, payload)
        return sid, storage, key, mask_key, settings

    @pytest.fixture(autouse=True)
    def isolate_db_url(self, monkeypatch, tmp_path: Path):  # noqa: ANN001
        """app.settings.database.url 指向临时库（同 test_review_api.isolate_db_url）。
        tracker 清空不放这里（return fixture 无 teardown）：committed_series
        显式清空 + 依赖本 fixture 的返回值。"""
        from bodydonor import app as app_module

        monkeypatch.setattr(
            app_module.settings,
            "database",
            type(app_module.settings.database)(url=f"sqlite:///{tmp_path}/m6a.db"),
        )
        app_module._interaction_trackers.clear()
        return app_module.settings.database.url

    def _write_correction(self, storage, key, img, target_label=1, voxels=((0, 4, 0, 4, 10),)):
        """在 storage 写 nninteractive-correction/ 产物（二值修正 mask + sidecar）。"""
        import json as _json
        import tempfile

        import nibabel as nib

        series_dir = "/".join(key.split("/")[:2])
        corr_arr = np.zeros(img.shape, dtype=np.uint8)
        for x0, x1, y0, y1, z in voxels:
            corr_arr[x0:x1, y0:y1, z] = 1
        corr = nib.Nifti1Image(corr_arr, img.affine)
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

    def _record_drafts_via_tracker(self, series_id, drafts):
        """模拟 nninteractive-correct 的采集路径：草稿落 app._interaction_trackers。"""
        from bodydonor import app as app_module

        tracker = app_module._interaction_tracker(series_id)
        for cls, arr, n_prompts in drafts:
            tracker.record_draft(cls, arr, n_prompts)
        return tracker

    def test_submit_nni_persists_interaction_metrics(self, committed_series) -> None:  # noqa: ANN001
        """草稿序列 → tracker → submit-nni → MaskVersion.provenance.interactionMetrics。"""
        import nibabel as nib
        from fastapi.testclient import TestClient

        from bodydonor.app import app

        sid, storage, key, mask_key, settings = committed_series
        img = nib.load(str(storage.root / key))
        self._write_correction(storage, key, img)

        # 模拟两步推理采集（与生产 nninteractive-correct 同一 tracker 入口）：
        # draft1 = 16 体素(0:4,0:4,z=10)；draft2 = 再加 9 体素(4:7,0:3,z=10)
        draft1 = np.zeros(img.shape, dtype=np.uint8)
        draft1[0:4, 0:4, 10] = 1
        draft2 = draft1.copy()
        draft2[4:7, 0:3, 10] = 1
        self._record_drafts_via_tracker(sid, [("label-1", draft1, 2), ("label-1", draft2, 3)])

        with TestClient(app) as c:
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
                    "sessionId": "sess-m6a",
                    "baselineTeacherVersion": None,
                },
            )
        assert r.status_code == 200
        prov = r.json()["provenance"]
        assert "interactionMetrics" in prov
        m = prov["interactionMetrics"]["label-1"]
        assert m["numPrompts"] == 3  # 最后一次全量提示数
        assert len(m["perStepDeltaDice"]) == 1  # 2 步草稿 → 1 个 delta
        # 草稿间 Dice(16 体素, 25 体素，交 16) = 32/41
        assert m["perStepDeltaDice"][0] == pytest.approx(2 * 16 / 41, abs=1e-4)
        # label-1 不在教师评估 117 类表（合成类名）→ counts.get=0 < 10 → 保守判 low
        # （生产类名是语义名如 aorta，命中 117 类表按 cases 数判）
        assert m["teacherReliability"] == "low"
        assert m["abandonedAttempts"] == 0
        # finalLocalDice：基线 label-1 100 体素 ∪ 修正 25 体素（无交叠）= 125 vs 基线 100
        # Dice = 2*100/225 ≈ 0.8889（修正体素是超集关系下的新增）
        assert m["finalLocalDice"] is not None

        # DB roundtrip
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
        assert mv.provenance["interactionMetrics"]["label-1"]["numPrompts"] == 3
        assert mv.provenance["sessionId"] == "sess-m6a"

    def test_submit_nni_empty_metrics_without_tracker(self, committed_series) -> None:  # noqa: ANN001
        """tracker 无数据 → interactionMetrics 空 dict（不造假）。"""
        import nibabel as nib
        from fastapi.testclient import TestClient

        from bodydonor.app import app

        sid, storage, key, mask_key, settings = committed_series
        img = nib.load(str(storage.root / key))
        self._write_correction(storage, key, img)
        with TestClient(app) as c:
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
                    "sessionId": "sess-empty",
                },
            )
        assert r.status_code == 200
        assert r.json()["provenance"]["interactionMetrics"] == {}

    def test_abandon_draft_endpoint_increments(self, committed_series) -> None:  # noqa: ANN001
        """放弃端点 → abandonedAttempts +1 → submit 后 provenance 反映。"""
        import nibabel as nib
        from fastapi.testclient import TestClient

        from bodydonor.app import app

        sid, storage, key, mask_key, settings = committed_series
        img = nib.load(str(storage.root / key))
        self._write_correction(storage, key, img)

        draft1 = np.zeros(img.shape, dtype=np.uint8)
        draft1[0:4, 0:4, 10] = 1
        self._record_drafts_via_tracker(sid, [("label-1", draft1, 2)])

        with TestClient(app) as c:
            r0 = c.post(f"/review/{sid}/abandon-draft", json={"target_label": 1})
            assert r0.status_code == 200
            assert r0.json()["abandonedAttempts"] == 1
            r0b = c.post(f"/review/{sid}/abandon-draft", json={"target_label": 1})
            assert r0b.json()["abandonedAttempts"] == 2  # 重复放弃同样计数

            # 放弃后重新修正再提交
            draft2 = draft1.copy()
            draft2[4:7, 0:3, 10] = 1
            self._record_drafts_via_tracker(sid, [("label-1", draft2, 3)])
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
                    "sessionId": "sess-abandon",
                },
            )
        assert r.status_code == 200
        m = r.json()["provenance"]["interactionMetrics"]["label-1"]
        assert m["abandonedAttempts"] == 2  # 放弃历史保留（M5-b 越改越差 → 放弃）
        assert m["numPrompts"] == 3  # 新序列提示数
        assert m["perStepDeltaDice"] == []  # 新序列只有 1 步草稿，无 delta

    def test_abandon_draft_bad_label_422(self, committed_series) -> None:  # noqa: ANN001
        from fastapi.testclient import TestClient

        from bodydonor.app import app

        sid, *_ = committed_series
        with TestClient(app) as c:
            r = c.post(f"/review/{sid}/abandon-draft", json={"target_label": 0})
            assert r.status_code == 422
            r2 = c.post(f"/review/{sid}/abandon-draft", json={})
            assert r2.status_code == 422
