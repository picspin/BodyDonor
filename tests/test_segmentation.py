"""M2：TotalSegmentator worker 测试（白名单 + 防线 + merge 守卫 + API）。

GPU 推理不进 CI（proposal §11）：真卡基线在 M2-REPORT 手工汇报。
本文件只测分发层逻辑（白名单/防线/合并/落盘），TS CLI 调用 mock。
"""

from __future__ import annotations

from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
from fastapi.testclient import TestClient

from bodydonor.config import Settings, load_settings
from bodydonor.segmentation import (
    SegmentWorkerError,
    check_worker_task,
    merge_ts_output,
    ts_venv_cli,
)


@pytest.fixture
def settings() -> Settings:
    """真实 settings.yaml（含白名单）。"""
    return load_settings()


class TestWhitelist:
    def test_total_allowed(self, settings: Settings) -> None:
        """total 在白名单，放行。"""
        check_worker_task("total", settings)  # 不 raise

    def test_brain_aneurysm_blocked(self, settings: Settings) -> None:
        """brain_aneurysm 一票否决（CC BY-NC，无商业授权渠道）。"""
        with pytest.raises(SegmentWorkerError, match="BLOCKED"):
            check_worker_task("brain_aneurysm", settings)

    def test_not_in_whitelist_rejected(self, settings: Settings) -> None:
        """不在白名单的任务拒绝（licensed tasks 需显式批准）。"""
        with pytest.raises(SegmentWorkerError, match="whitelist"):
            check_worker_task("coronary_arteries", settings)

    def test_whitelist_negative_tests_still_pass(self, settings: Settings) -> None:
        """CI 白名单负测试继续生效（白名单不许被 worker 绕过）。"""
        from bodydonor.license_registry import LicenseRegistry

        ok, _ = LicenseRegistry(settings).check_task("total")
        assert ok is True
        ok, msg = LicenseRegistry(settings).check_task("brain_aneurysm")
        assert ok is False
        assert "BLOCKED" in msg


class TestMergeOutput:
    def _make_image_and_ts_out(self, tmp_path: Path) -> tuple[Path, Path]:
        img = nib.Nifti1Image(np.zeros((32, 32, 8), dtype=np.int16), np.eye(4))
        image_path = tmp_path / "img.nii.gz"
        nib.save(img, str(image_path))
        out_dir = tmp_path / "ts_out"
        out_dir.mkdir()
        # 两个类 mask（与原图同 affine）
        m1 = np.zeros((32, 32, 8), dtype=np.uint8)
        m1[5:10, 5:10, 2:5] = 1
        m2 = np.zeros((32, 32, 8), dtype=np.uint8)
        m2[15:20, 15:20, 2:5] = 1
        nib.save(nib.Nifti1Image(m1, np.eye(4)), str(out_dir / "liver.nii.gz"))
        nib.save(nib.Nifti1Image(m2, np.eye(4)), str(out_dir / "spleen.nii.gz"))
        return image_path, out_dir

    def test_merge_multiclass(self, tmp_path: Path) -> None:
        """每类 NIfTI 合并为多类标签图，类名列表。"""
        image_path, out_dir = self._make_image_and_ts_out(tmp_path)
        merged, names = merge_ts_output(out_dir, image_path)
        assert names == ["liver", "spleen"]
        arr = np.asarray(merged.dataobj)
        assert set(np.unique(arr)) == {0, 1, 2}
        assert int((arr == 1).sum()) > 0
        assert int((arr == 2).sum()) > 0

    def test_merge_affine_mismatch_raises(self, tmp_path: Path) -> None:
        """(b) affine 守卫：类 mask 与原图 affine 不一致 -> AffineMismatchError。"""
        image_path, out_dir = self._make_image_and_ts_out(tmp_path)
        # 篡改 spleen 的 affine（平移不一致）
        bad = np.eye(4)
        bad[0, 3] = 7.0
        spleen_path = out_dir / "spleen.nii.gz"
        nib.save(nib.Nifti1Image(np.zeros((32, 32, 8), dtype=np.uint8), bad), str(spleen_path))
        with pytest.raises(Exception, match="affine 不一致"):
            merge_ts_output(out_dir, image_path, label="mismatch-test")

    def test_merge_empty_out_dir_raises(self, tmp_path: Path) -> None:
        image_path = tmp_path / "img.nii.gz"
        nib.save(nib.Nifti1Image(np.zeros((8, 8, 4), dtype=np.int16), np.eye(4)), str(image_path))
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(SegmentWorkerError, match="无 mask"):
            merge_ts_output(empty, image_path)


class TestVenvCli:
    def test_cli_path_exists(self) -> None:
        """独立 venv 的 TotalSegmentator CLI 存在（Q7 隔离装好）。"""
        p = ts_venv_cli(Settings())
        assert Path(p).exists()
        assert ".venv-totalseg" in p

    def test_main_venv_not_shared(self) -> None:
        """主 .venv 不含 TotalSegmentator（隔离验证）。"""
        import sysconfig

        main_venv = Path(sysconfig.get_paths()["purelib"])
        assert not (main_venv / "totalsegmentator").exists(), (
            f"TotalSegmentator 泄漏进主环境：{main_venv}"
        )


class TestSegmentApi:
    @pytest.fixture(autouse=True)
    def isolate(self, monkeypatch, tmp_path: Path):  # noqa: ANN001
        """API 用临时库 + 临时存储；TS 调用 mock。"""
        from bodydonor import app as app_module

        monkeypatch.setattr(
            app_module.settings,
            "database",
            type(app_module.settings.database)(url=f"sqlite:///{tmp_path}/seg.db"),
        )
        monkeypatch.setattr(
            app_module.settings, "data_dir", tmp_path / "data"
        )
        # mock segment_series（TS CLI 不进 CI）：白名单语义保留——blocked 任务
        # 由 mock 抛 SegmentWorkerError（与真实分发层 check_worker_task 一致）
        def _fake_segment(sid, s, task="total", device="gpu"):  # noqa: ANN001
            if task not in ("total", "total_mr", "body"):
                # 模拟 license_registry.check_task 的白名单拒绝（BLOCKED/not in）
                raise SegmentWorkerError(
                    f"任务白名单拒绝：task '{task}' is BLOCKED (license: see "
                    "LICENSES-THIRD-PARTY.md)"
                    if task == "brain_aneurysm"
                    else f"任务白名单拒绝：task '{task}' not in whitelist"
                )
            return _FakeResult(sid, task)

        monkeypatch.setattr(
            "bodydonor.segmentation.segment_series", _fake_segment
        )

    def test_segments_create_ok(self) -> None:
        with TestClient(__import__("bodydonor.app", fromlist=["app"]).app) as c:
            r = c.post("/segments", params={"series_id": "nifti-abc", "task": "total"})
        assert r.status_code == 200
        body = r.json()
        assert body["series_id"] == "nifti-abc"
        assert body["task"] == "total"

    def test_segments_blocked_task_422(self) -> None:
        """blocked 任务 → 422（白名单在分发层，不许绕过）。"""
        with TestClient(__import__("bodydonor.app", fromlist=["app"]).app) as c:
            r = c.post(
                "/segments",
                params={"series_id": "nifti-abc", "task": "brain_aneurysm"},
            )
        assert r.status_code == 422
        assert "BLOCKED" in r.text


class _FakeResult:
    def __init__(self, sid: str, task: str) -> None:
        self.series_id = sid
        self.case_id = "case-x"
        self.task = task
        self.mask_key = f"case-x/{sid}/mask.nii.gz"
        self.n_classes = 42
        self.class_names = ["liver"]
        self.elapsed_s = 1.0
        self.peak_memory_mi_b = None
        self.tmp_output = ""

    def to_dict(self):  # noqa: ANN201
        return {
            "series_id": self.series_id,
            "case_id": self.case_id,
            "task": self.task,
            "mask_key": self.mask_key,
            "n_classes": self.n_classes,
            "elapsed_s": self.elapsed_s,
            "peak_memory_mi_b": self.peak_memory_mi_b,
        }
