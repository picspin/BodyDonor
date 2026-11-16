"""M1-fix：选择器 bug 回归（2 层序列被排除）+ commit 路径 <min_slices 防线测试。

M1-REPORT §3 实录 bug 根因：apply_rules 就地排序 candidates，但 report.series
保持扫描序；CLI 用 zip(candidates, series) 位置对应 -> 2 层 LOCALIZER 行显示了
40 层 candidate 的"选中"。回归测试断言：candidates 与 report.series 的排除状态
按 series_id 一一对应（不 zip）。
"""

from __future__ import annotations

import pytest
from tests.factories import make_dicom_folder, make_localizer, make_nifti

from bodydonor.ingest import apply_series_rules, ingest
from bodydonor.pipeline import SliceGuardError, guard_min_slices
from bodydonor.series_selector import SeriesCandidate, select


class TestSelectorRegression:
    def test_two_slice_series_excluded_regression(self, tmp_path) -> None:  # noqa: ANN001
        """回归：2 层序列（M1-REPORT §3 第一条）必须被排除，不显示'选中'。"""
        # 让 LOCALIZER 目录先被扫描（M1-REPORT §3 的扫描顺序）
        loc_root = tmp_path / "dicom" / "a_localizer"
        make_localizer(loc_root, series_uid="1.2.826.0.1.3680043.8.498.9.1")
        make_dicom_folder(tmp_path / "dicom", n_series=2, slices_per_series=40)

        report = ingest(tmp_path / "dicom")
        assert report.n_series == 3
        candidates = apply_series_rules(report)

        # 关键断言：排除状态按 series_id 对应（不 zip 位置）
        by_id = {c.series_id: c for c in candidates}
        for rec in report.series:
            cand = by_id[rec.series_id]
            reason = cand.exclude_reason or ""
            if rec.num_slices < 30:
                assert cand.excluded is True, (
                    f"{rec.series_id} 层数 {rec.num_slices} < 30 必须排除"
                )
                assert "层数" in reason or "LOCALIZER" in reason
            else:
                # 40 层非轴位原始序列不应因别人的规则被误标
                assert cand.excluded is False or "LOCALIZER" in reason

        # 2 层序列本身被排除
        two_slice = [c for c in candidates if c.num_slices == 2]
        assert len(two_slice) == 1
        assert two_slice[0].excluded is True

    def test_two_slice_series_default_select_excludes(self, tmp_path) -> None:  # noqa: ANN001
        """2 层序列不在默认选中列表。"""
        loc_root = tmp_path / "dicom" / "a_localizer"
        make_localizer(loc_root, series_uid="1.2.826.0.1.3680043.8.498.9.1")
        make_dicom_folder(tmp_path / "dicom", n_series=2, slices_per_series=40)
        report = ingest(tmp_path / "dicom")
        candidates = apply_series_rules(report)
        picked = select(candidates)
        assert all(
            next(c for c in candidates if c.series_id == p).num_slices >= 30 for p in picked
        )


def _guard_cands() -> list[SeriesCandidate]:
    """防线测试的候选：40 层正常 + 2 层被排除。"""
    return [
        SeriesCandidate(series_id="s40", num_slices=40),
        SeriesCandidate(
            series_id="s2", num_slices=2, excluded=True, exclude_reason="层数 2 < 30"
        ),
    ]


class TestSliceGuard:
    def test_guard_rejects_thin_series(self) -> None:
        """防线：<30 层序列试图交给下游（未勾选）-> SliceGuardError。"""
        cands = _guard_cands()
        with pytest.raises(SliceGuardError, match="层数"):
            guard_min_slices(cands, selected=["s40", "s2"])

    def test_guard_allows_explicit_override(self) -> None:
        """防线：用户显式 --include 勾选 -> 放行。"""
        cands = _guard_cands()
        # 显式勾选（user_overrides 含 s2）
        guard_min_slices(cands, selected=["s2"], user_overrides=["s2"])

    def test_guard_passes_normal_series(self) -> None:
        """对照：>=30 层正常序列 -> 无异常。"""
        cands = [SeriesCandidate(series_id="s40", num_slices=40)]
        guard_min_slices(cands, selected=["s40"])

    def test_commit_path_rejects_thin_series(self, tmp_path, settings) -> None:  # noqa: ANN001
        """端到端（纵深防御）：选择器被绕过时 commit 路径兜底拒绝 <30 层序列。

        正常路径 apply_rules 已排除 <30 层（默认 selected 不含 thin），
        防线防的是规则被绕过（如手工构造 outcome 直接 commit）的场景。
        """
        from bodydonor.pipeline import commit, scan
        from bodydonor.storage import LocalFsStorage

        # shape 是 (z,y,x) 数组序 -> (10,64,64) = 10 层 < 30
        nii = make_nifti(tmp_path / "thin.nii.gz", shape=(10, 64, 64))
        outcome = scan(nii, settings, modality="CT")
        assert outcome.report.series[0].num_slices == 10

        # 正常路径：默认 selected 不含 thin（规则已排除），commit 不落库
        settings.database.url = f"sqlite:///{tmp_path}/t1.db"
        storage = LocalFsStorage(tmp_path / "s1")
        committed = commit(outcome, settings, storage)
        assert committed.selected_series_ids == []

        # 纵深防御：手工绕过规则把 thin 塞进 selected -> guard 兜底拒绝
        from bodydonor.series_selector import SeriesCandidate

        outcome.selected_series_ids = [outcome.report.series[0].series_id]
        outcome.candidates = [
            SeriesCandidate(
                series_id=outcome.report.series[0].series_id,
                num_slices=10,
                excluded=False,  # 模拟规则被绕过
            )
        ]
        with pytest.raises(SliceGuardError):
            commit(outcome, settings, storage)

    def test_commit_path_override_allows_thin_series(self, tmp_path, settings) -> None:  # noqa: ANN001
        """对照：用户显式勾选覆盖 -> commit 放行 thin 序列。"""
        from bodydonor.pipeline import commit, scan
        from bodydonor.storage import LocalFsStorage

        nii = make_nifti(tmp_path / "thin.nii.gz", shape=(10, 64, 64))
        outcome = scan(nii, settings, modality="CT")
        sid = outcome.report.series[0].series_id

        settings.database.url = f"sqlite:///{tmp_path}/t2.db"
        storage = LocalFsStorage(tmp_path / "s2")
        committed = commit(outcome, settings, storage, user_overrides=[sid])
        assert committed.selected_series_ids == [sid]
        assert len(committed.case_ids) == 1
