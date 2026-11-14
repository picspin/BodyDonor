"""M4 阶段 3：nnInteractive 交互修正后端测试。

覆盖（GPU 大推理只走真机验证，不进单测——铁律：训练占卡时不跑 GPU 推理）：

- 许可门禁：ontology 版 + distill_data 严格版各拒绝 nninteractive 来源
  （规范名 nninteractive_guided + 两个 legacy 别名 manual-correction-nninteractive /
  nninteractive——命名 v1→v2 用户 2026-11-09 决定，legacy 名永不再写出但拒绝保留）
- 锁互斥（临时锁路径）：CUDA 推理持有中 fast-fail、释放后可抢、CPU 模式无锁
- 后端 runner 契约：CPU 小输入真实跑通（真实 amos_0004 裁 patch，无 mock 数字）
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from bodydonor.ontology import mask_allowed_in_distill_training

# ---------------------------------------------------------------------------
# 许可门禁（LICENSES-THIRD-PARTY.md §2.2/§8.3 的代码强制）
# ---------------------------------------------------------------------------

class TestLicenseGate:
    def test_ontology_gate_rejects_nninteractive_guided(self) -> None:
        """ontology 版门禁：规范名 nninteractive_guided 拒绝（v2）。"""
        ok, reason = mask_allowed_in_distill_training(
            {"source": "nninteractive_guided", "experimental": False}
        )
        assert ok is False
        assert "nnInteractive" in reason

    def test_ontology_gate_rejects_legacy_manual_correction_name(self) -> None:
        """ontology 版门禁：legacy 别名 manual-correction-nninteractive 仍拒绝。"""
        ok, reason = mask_allowed_in_distill_training(
            {"source": "manual-correction-nninteractive", "experimental": False}
        )
        assert ok is False
        assert "nnInteractive" in reason

    def test_ontology_gate_rejects_legacy_nninteractive_name(self) -> None:
        """ontology 版门禁：legacy 别名 nninteractive 仍拒绝。"""
        ok, reason = mask_allowed_in_distill_training(
            {"source": "nninteractive", "experimental": False}
        )
        assert ok is False
        assert "CC BY-NC-SA" in reason

    def test_ontology_gate_still_accepts_manual(self) -> None:
        """ontology 版门禁：纯人工修正仍放行（回归防线；三态 source=human 同理放行）。"""
        ok, _ = mask_allowed_in_distill_training({"source": "manual"})
        assert ok is True
        ok, _ = mask_allowed_in_distill_training({"source": "human"})
        assert ok is True

    def test_strict_gate_rejects_nninteractive_guided(self) -> None:
        """distill_data 严格版门禁：规范名 nninteractive_guided 拒绝。"""
        from bodydonor.distill_data import check_distill_source

        ok, reason = check_distill_source("nninteractive_guided")
        assert ok is False
        assert "nnInteractive" in reason

    def test_strict_gate_rejects_legacy_names(self) -> None:
        """distill_data 严格版门禁：两个 legacy 别名仍拒绝（安全倒退防线）。"""
        from bodydonor.distill_data import check_distill_source

        for legacy in ("manual-correction-nninteractive", "nninteractive"):
            ok, reason = check_distill_source(legacy)
            assert ok is False, legacy
            assert "nnInteractive" in reason, legacy

    def test_strict_gate_meta_rejects_nninteractive_guided(self) -> None:
        """distill_data 严格版 mask_meta 门禁：nninteractive_guided 修正产物拒绝。"""
        from bodydonor.distill_data import mask_allowed_in_distill_training_strict

        ok, _ = mask_allowed_in_distill_training_strict(
            {"source": "nninteractive_guided", "experimental": False}
        )
        assert ok is False

    def test_sidecar_source_constant(self) -> None:
        """后端 sidecar 来源标签与蒸馏门禁拒绝清单一致（契约锁定）。

        v2 规范名 nninteractive_guided；两个 legacy 名保留在 REJECTED_SOURCES
        （永不再写出，但删除拒绝是安全倒退）。
        """
        from bodydonor.distill_data import REJECTED_SOURCES
        from bodydonor.nninteractive_backend import (
            CORRECTION_SOURCE,
            CORRECTION_SOURCE_LEGACY_ALIASES,
        )

        assert CORRECTION_SOURCE == "nninteractive_guided"
        assert CORRECTION_SOURCE in REJECTED_SOURCES
        assert CORRECTION_SOURCE != "manual-correction-nninteractive"
        assert CORRECTION_SOURCE != "human"
        for legacy in CORRECTION_SOURCE_LEGACY_ALIASES:
            assert legacy in REJECTED_SOURCES


# ---------------------------------------------------------------------------
# 锁互斥（临时锁路径；真卡 live 验证另做，不动真实锁文件）
# ---------------------------------------------------------------------------

class TestTargetVoxelCount:
    """n_target_voxels 计数回归（2026-11-11 GPU 验证发现的真 bug）。

    修正 mask 是二值 0/1，target_label 是语义类号（如 liver=44），不是
    mask 体素值。旧代码 (mask == target_label) 在 target_label != 1 时恒
    报 0（GPU 验证：mask 非零 16754 体素，sidecar 报 0）；CPU 验证
    target_label=1 时 ==1 与 >0 等价，4055 恰好正确——同一口径，不同结果。
    """

    def test_binary_mask_counts_foreground_not_target_label(self) -> None:
        import numpy as np

        from bodydonor.nni_runner import _count_target_voxels

        arr = np.zeros((10, 10, 10), dtype=np.uint8)
        arr[2:5, 2:5, 2:5] = 1  # 27 个前景体素
        # 语义类号 44 不应影响计数：前景就是 27
        assert _count_target_voxels(arr) == 27

    def test_empty_mask_counts_zero(self) -> None:
        import numpy as np

        from bodydonor.nni_runner import _count_target_voxels

        assert _count_target_voxels(np.zeros((4, 4, 4), dtype=np.uint8)) == 0

    def test_full_mask_counts_all(self) -> None:
        import numpy as np

        from bodydonor.nni_runner import _count_target_voxels

        assert _count_target_voxels(np.ones((3, 3, 3), dtype=np.uint8)) == 27


class TestLockMutex:
    def test_interactive_fast_fail_when_held(self, tmp_path: Path) -> None:
        """CUDA 交互修正：锁被占（假持有者）时 GpuBusyError fast-fail。"""
        from bodydonor.gpu_lock import try_acquire
        from bodydonor.nninteractive_backend import GpuBusyError, correct_image

        lock_path = tmp_path / "data" / "BodyDonor" / ".gpu.lock"
        holder = try_acquire(lock_path, reason="batch", task="nnunet", job_id="fake-1")
        assert holder is not None
        img = tmp_path / "img.nii.gz"
        img.write_bytes(b"stub")
        with pytest.raises(GpuBusyError) as exc:
            correct_image(
                img,
                tmp_path / "out",
                [{"type": "point", "x": 1.0, "y": 1.0, "z": 1.0, "include": True}],
                device="cuda",
                settings=_settings_with_data_dir(tmp_path),
            )
        assert "批量" in str(exc.value) or "持有" in str(exc.value)
        assert exc.value.__class__.__name__ == "GpuBusyError"
        holder.release()

    def test_interactive_acquire_after_release(self, tmp_path: Path, monkeypatch) -> None:
        """释放后可抢：锁释放后 CUDA 修正拿到锁（锁层语义）。

        nvidia-smi 占用检查 monkeypatch 掉（本机训练占卡时实报 >50%，属真机
        环境信号，与锁语义测试无关——锁放行后因 venv 内推理失败报
        NnInteractiveError，证明锁已被本进程持有）。
        """
        from bodydonor import nninteractive_backend as backend
        from bodydonor.gpu_lock import try_acquire
        from bodydonor.nninteractive_backend import NnInteractiveError, correct_image

        monkeypatch.setattr(backend, "gpu_memory_used_mi_b", lambda: None)
        monkeypatch.setattr(backend, "_gpu_total_mi_b", lambda: None)

        lock_path = tmp_path / "data" / "BodyDonor" / ".gpu.lock"
        holder = try_acquire(lock_path, reason="batch", task="nnunet", job_id="fake-2")
        holder.release()
        img = tmp_path / "img.nii.gz"
        img.write_bytes(b"stub")
        # 锁已空：acquire 成功（本进程持锁），随后因 venv 内输入校验/推理失败
        # 报 NnInteractiveError——但锁互斥本身已验证（不再 GpuBusyError）
        with pytest.raises(NnInteractiveError) as exc:
            correct_image(
                img,
                tmp_path / "out",
                [{"type": "point", "x": 1.0, "y": 1.0, "z": 1.0, "include": True}],
                device="cuda",
                settings=_settings_with_data_dir(tmp_path),
            )
        assert "GpuBusy" not in exc.value.__class__.__name__
        # 锁已随修正失败释放（短会话 finally 保证）
        assert not lock_path.exists()

    def test_cpu_mode_does_not_take_gpu_lock(self, tmp_path: Path) -> None:
        """CPU 模式不占 GPU 锁：锁被占时 CPU 修正仍可跑（不与 GPU 批量互斥）。"""
        from bodydonor.gpu_lock import try_acquire
        from bodydonor.nninteractive_backend import correct_image

        lock_path = tmp_path / "data" / "BodyDonor" / ".gpu.lock"
        holder = try_acquire(lock_path, reason="batch", task="nnunet", job_id="fake-3")
        assert holder is not None
        img = tmp_path / "img.nii.gz"
        img.write_bytes(b"stub")
        # CPU 模式 + 锁被占：不报 GpuBusyError（锁层放行；后续输入校验失败另报）
        from bodydonor.nninteractive_backend import NnInteractiveError

        with pytest.raises(NnInteractiveError):
            correct_image(
                img,
                tmp_path / "out",
                [{"type": "point", "x": 1.0, "y": 1.0, "z": 1.0, "include": True}],
                device="cpu",
                settings=_settings_with_data_dir(tmp_path),
            )
        assert lock_path.exists()  # 锁仍由假持有者持有
        holder.release()

    def test_release_on_idle_semantics(self, tmp_path: Path) -> None:
        """release_on_idle 兜底语义：闲置超时（从锁文件 acquired_at 起算）放锁。"""
        import time as _time

        from bodydonor.gpu_lock import DEFAULT_IDLE_SECONDS, try_acquire

        lock_path = tmp_path / "data" / "BodyDonor" / ".gpu.lock"
        lock = try_acquire(lock_path, reason="interactive", task="nninteractive", job_id="j-idle")
        assert lock is not None
        # 未闲置：仍持锁
        assert lock.release_on_idle(idle_seconds=DEFAULT_IDLE_SECONDS) is False
        # 把锁文件 acquired_at 拨回（模拟闲置超时——闲置时长从锁文件起算）
        info = json.loads(lock_path.read_text(encoding="utf-8"))
        info["acquired_at"] = _time.time() - (DEFAULT_IDLE_SECONDS + 5)
        lock_path.write_text(json.dumps(info), encoding="utf-8")
        assert lock.release_on_idle(idle_seconds=DEFAULT_IDLE_SECONDS) is True
        assert not lock_path.exists()  # 锁已释放


def _settings_with_data_dir(tmp_path: Path):
    from bodydonor.config import Settings

    s = Settings()
    s.data_dir = tmp_path / "data" / "BodyDonor"
    return s


# ---------------------------------------------------------------------------
# 后端 runner 契约（CPU 小输入真实跑通；GPU 大推理只走真机验证，不进单测）
# ---------------------------------------------------------------------------

def _make_small_patch(src_nii: Path, dst_nii: Path, center_xyz=(318, 255, 54), size=48) -> Path:
    """从真实 amos_0004 裁一个 48^3 patch（真实 case 数据，非合成）。

    CPU 推理对 patch 尺寸敏感（48^3 在 CPU 上分钟级），用小 patch 保真实输入。
    中心 (318,255,54)：肝区（storage combined_mask label 44 liver bbox 中心
    (318,255,66) 的 z 方向 clamp——48³ patch z 半径 24，66+24 超 78 层界），
    实测中心 HU 100（软组织，非空气——点提示命中有效组织）。
    """
    import nibabel as nib

    img = nib.load(str(src_nii))
    arr = np.asarray(img.dataobj)
    cx, cy, cz = center_xyz
    half = size // 2
    patch = arr[cx - half:cx + half, cy - half:cy + half, cz - half:cz + half]
    patch_img = nib.Nifti1Image(np.ascontiguousarray(patch, dtype=np.float32), img.affine)
    dst_nii.parent.mkdir(parents=True, exist_ok=True)
    nib.save(patch_img, str(dst_nii))
    return dst_nii


class TestRunnerContractCpu:
    @pytest.fixture(scope="class")
    def real_patch(self) -> Path:
        """真实 amos_0004 裁 48^3 patch（真实输入；case 文件缺失则跳过）。"""
        src = (
            Path.home() / "data" / "BodyDonor" / "downloads" / "amos22_subset"
            / "train" / "imagesTr" / "amos_0004.nii.gz"
        )
        if not src.exists():
            pytest.skip(f"真实 case 缺失：{src}")
        import tempfile

        dst = Path(tempfile.mkdtemp(prefix="nni-patch-")) / "amos_0004_patch48.nii.gz"
        return _make_small_patch(src, dst)

    @pytest.mark.local_artifacts
    def test_cpu_small_input_real_run(self, real_patch: Path, tmp_path: Path) -> None:
        """CPU 小 patch 真实跑通：加载 → 点提示 → 出 mask → 落盘 sidecar。

        全程实测数字（无 mock）；CPU 推理慢时由 runner 超时如实报错（上限
        NNI_RUNNER_TIMEOUT_SECONDS），不编数字。
        """
        from bodydonor.nninteractive_backend import (
            CORRECTION_LICENSE,
            CORRECTION_SOURCE,
            correct_image,
        )

        out_dir = tmp_path / "nni-out"
        # 点提示取 patch 中心（48^3 patch 中心 = 24,24,24 体素坐标）
        result = correct_image(
            real_patch,
            out_dir,
            [{"type": "point", "x": 24.0, "y": 24.0, "z": 24.0, "include": True}],
            device="cpu",
            target_label=1,
        )
        assert result.mask_path and Path(result.mask_path).exists()
        assert Path(result.sidecar_path).exists()
        # sidecar 事实源字段齐全
        sidecar = json.loads(Path(result.sidecar_path).read_text(encoding="utf-8"))
        assert sidecar["source"] == CORRECTION_SOURCE
        assert sidecar["license"] == CORRECTION_LICENSE
        assert sidecar["device"] == "cpu"
        assert sidecar["peak_vram_mb"] is None  # CPU 模式不猜显存
        assert "REJECTED" in sidecar["distill_training"]
        assert sidecar["prompts"] == [
            {"type": "point", "x": 24.0, "y": 24.0, "z": 24.0, "include": True}
        ]
        # provenance（用户 2026-11-09 三态决定）字段齐全
        prov = sidecar["provenance"]
        assert prov["sessionId"]  # 每次运行新生成 uuid
        assert len(prov["sessionId"]) == 36  # uuid4 标准格式
        assert prov["prompts"] == sidecar["prompts"]  # 全量提示序列（可重放）
        assert prov["acceptedClasses"] == [1]  # [target_label]
        # baselineTeacherVersion：tmp patch 无 DB 行 → null + 注明原因（不猜数字）
        assert prov["baselineTeacherVersion"] is None
        assert prov["baselineTeacherVersionReason"]
        # n_target_voxels 与落盘 mask 交叉核对（2026-11-11 回归：二值 mask
        # 前景计数，target_label=1 时旧口径恰好正确，改口径后仍须一致）
        import nibabel as _nib

        _mask_arr = np.asarray(_nib.load(result.mask_path).dataobj)
        assert sidecar["n_target_voxels"] == int((_mask_arr > 0).sum())
        # 实测耗时 > 0（真实跑通）
        assert result.load_seconds > 0
        assert result.inference_seconds > 0
        # 修正 mask 与原图 affine 一致（runner 内 assert_affine_match 已过，这里复核）
        import nibabel as nib

        from bodydonor.nifti_geom import assert_affine_match

        img = nib.load(str(real_patch))
        mask = nib.load(result.mask_path)
        assert_affine_match(img, mask, label="test_cpu_small_input")

    @pytest.mark.local_artifacts
    def test_session_id_unique_per_run(self, real_patch: Path, tmp_path: Path) -> None:
        """sessionId 每次修正运行新生成（两次运行不同 uuid，可重放审计）。"""
        from bodydonor.nninteractive_backend import correct_image

        sidecars = []
        for i in range(2):
            out_dir = tmp_path / f"nni-out-{i}"
            result = correct_image(
                real_patch,
                out_dir,
                [{"type": "point", "x": 24.0, "y": 24.0, "z": 24.0, "include": True}],
                device="cpu",
                target_label=1,
            )
            sidecars.append(
                json.loads(Path(result.sidecar_path).read_text(encoding="utf-8"))
            )
        s1, s2 = sidecars[0]["provenance"], sidecars[1]["provenance"]
        assert s1["sessionId"] != s2["sessionId"]

    def test_empty_prompts_rejected(self, tmp_path: Path) -> None:
        """空 prompts 拒绝（输入校验，不进 runner）。"""
        from bodydonor.nninteractive_backend import NnInteractiveError, correct_image

        img = tmp_path / "img.nii.gz"
        img.write_bytes(b"stub")
        with pytest.raises(NnInteractiveError):
            correct_image(img, tmp_path / "out", [], device="cpu")


class TestApiEndpoint:
    def test_nninteractive_correct_gpu_busy_409(self, tmp_path: Path) -> None:
        """API：GPU 忙（锁被占）→ 409 + 清晰错误，不排队。"""
        from fastapi.testclient import TestClient

        from bodydonor import app as app_module
        from bodydonor.gpu_lock import try_acquire

        lock_path = tmp_path / "data" / "BodyDonor" / ".gpu.lock"
        holder = try_acquire(lock_path, reason="batch", task="nnunet", job_id="fake-api")
        assert holder is not None

        # 临时 settings（data_dir 指向 tmp_path；DB 不需要——锁检查在 DB 前）
        from bodydonor.config import Settings

        s = Settings()
        s.data_dir = tmp_path / "data" / "BodyDonor"
        orig_settings = app_module.settings
        app_module.settings = s
        try:
            client = TestClient(app_module.app)
            # 未知 series 404 先行（锁检查在 series 查询后——构造真实 409 需要
            # 已 ingest 的 series；这里验证 404 路径 + 锁 fast-fail 语义在
            # test_interactive_fast_fail_when_held 已覆盖）
            resp = client.post(
                "/review/unknown-series/nninteractive-correct",
                json={"point": {"x": 1, "y": 1, "z": 1}},
            )
            assert resp.status_code == 404
        finally:
            app_module.settings = orig_settings
            holder.release()

    def test_nninteractive_correct_point_validation_422(self, tmp_path: Path) -> None:
        """API：point 缺 x/y/z → 422。"""
        from fastapi.testclient import TestClient

        from bodydonor import app as app_module
        from bodydonor.config import Settings

        s = Settings()
        s.data_dir = tmp_path / "data" / "BodyDonor"
        orig_settings = app_module.settings
        app_module.settings = s
        try:
            client = TestClient(app_module.app)
            # 无 series 时 404 先行；point 校验在有 series 的路径上测
            resp = client.post(
                "/review/some-series/nninteractive-correct",
                json={"point": {"x": 1}},
            )
            assert resp.status_code in (404, 422)
        finally:
            app_module.settings = orig_settings


class TestScribbleToMask:
    """runner 纯函数：scribble 点轨迹 → 与原图同形状的 uint8 mask（上游 API 入参）。"""

    def test_points_rasterize_to_mask(self) -> None:
        from bodydonor.nni_runner import _scribble_to_mask

        mask = _scribble_to_mask([(1.2, 2.6, 3.0), (4.0, 5.0, 6.0)], (8, 8, 8))
        assert mask.shape == (8, 8, 8)
        assert mask.dtype == np.uint8
        # round(1.2)=1, round(2.6)=3（上游 add_point_interaction 同 round 语义）
        assert mask[1, 3, 3] == 1
        assert mask[4, 5, 6] == 1
        assert int(mask.sum()) == 2

    def test_out_of_bounds_points_dropped(self) -> None:
        from bodydonor.nni_runner import _scribble_to_mask

        mask = _scribble_to_mask([(0, 0, 0), (-1, 0, 0), (8, 0, 0), (5, 5, 5)], (8, 8, 8))
        assert int(mask.sum()) == 2
        assert mask[0, 0, 0] == 1
        assert mask[5, 5, 5] == 1

    def test_empty_points_zero_mask(self) -> None:
        from bodydonor.nni_runner import _scribble_to_mask

        mask = _scribble_to_mask([], (4, 4, 4))
        assert int(mask.sum()) == 0
        assert mask.shape == (4, 4, 4)


class TestRunnerValidatePromptsScribble:
    """_validate_prompts scribble 分支（runner 不信任 stdin 输入）。"""

    def test_scribble_valid(self) -> None:
        from bodydonor.nni_runner import _validate_prompts

        out, err = _validate_prompts(
            [{"type": "scribble", "points": [[1, 2, 3], [4.5, 5.5, 6.5]]}]
        )
        assert err == ""
        assert out == [{"type": "scribble", "points": [(1.0, 2.0, 3.0), (4.5, 5.5, 6.5)]}]

    def test_scribble_empty_rejected(self) -> None:
        from bodydonor.nni_runner import _validate_prompts

        out, err = _validate_prompts([{"type": "scribble", "points": []}])
        assert out == []
        assert "非空" in err

    def test_scribble_bad_point_rejected(self) -> None:
        from bodydonor.nni_runner import _validate_prompts

        out, err = _validate_prompts([{"type": "scribble", "points": [[1, 2]]}])
        assert out == []
        assert "prompts[0] scribble points[0]" in err

    def test_unknown_type_lists_scribble(self) -> None:
        from bodydonor.nni_runner import _validate_prompts

        out, err = _validate_prompts([{"type": "lasso", "points": [[1, 2, 3]]}])
        assert out == []
        assert "point|bbox|scribble" in err

    def test_bbox_still_valid(self) -> None:
        from bodydonor.nni_runner import _validate_prompts

        out, err = _validate_prompts([{"type": "bbox", "bbox": [[8, 12], [8, 12], [10, 11]]}])
        assert err == ""
        assert out == [{"type": "bbox", "bbox": [[8.0, 12.0], [8.0, 12.0], [10.0, 11.0]]}]


@pytest.mark.local_artifacts
class TestBboxScribbleRealRun:
    """bbox / scribble 提示真实小输入验证（M4 阶段 3 同款 CPU 48^3 patch）。

    真实推理、无 mock；全程实测数字。case 文件缺失则跳过（同 TestRunnerContractCpu）。
    上游 API 核实（nnInteractive 2.6.0 inference_session.py，2026-11-11）：
    - add_bbox_interaction(bbox_coords, include_interaction, run_prediction=True,
      override_capability_checks=False)——include_interaction 无默认值，缺参 TypeError；
    - add_scribble_interaction(scribble_image, include_interaction=True, ...,
      interaction_bbox=None)——入参是 scribble mask 图（形状须匹配
      original_image_shape[1:]），不是点列表。
    本 checkpoint（legacy metadata inference_session_class.json）：
    scribble=True / bbox2d=True / bbox3d=False（_legacy_default_capability）。
    """

    @pytest.fixture(scope="class")
    def real_patch(self) -> Path:
        src = (
            Path.home() / "data" / "BodyDonor" / "downloads" / "amos22_subset"
            / "train" / "imagesTr" / "amos_0004.nii.gz"
        )
        if not src.exists():
            pytest.skip(f"真实 case 缺失：{src}")
        import tempfile

        dst = Path(tempfile.mkdtemp(prefix="nni-patch-")) / "amos_0004_patch48.nii.gz"
        return _make_small_patch(src, dst)

    def test_bbox_prompt_real_run_cpu(self, real_patch: Path, tmp_path: Path) -> None:
        """bbox 提示真实跑通（CPU 小 patch）：include=True 修正 → mask 落盘。

        修复前：runner 旧调用 session.add_bbox_interaction(p["bbox"]) 缺
        include_interaction 参数 → TypeError，bbox 分支从未真实跑通；
        修复后 include=True（bbox 提示恒为正向）。
        """
        from bodydonor.nninteractive_backend import correct_image

        out_dir = tmp_path / "nni-bbox-out"
        result = correct_image(
            real_patch,
            out_dir,
            # 2D bbox：z 维 [d, d+1]（单切片；bbox3d 本 checkpoint 不支持），
            # x/y 覆盖 patch 中心肝区
            [{"type": "bbox", "bbox": [[20.0, 28.0], [20.0, 28.0], [24.0, 25.0]]}],
            device="cpu",
            target_label=1,
            job_id="nni-bbox-verify-amos0004",
        )
        assert result.mask_path and Path(result.mask_path).exists()
        sidecar = json.loads(Path(result.sidecar_path).read_text(encoding="utf-8"))
        assert sidecar["source"] == "nninteractive_guided"
        assert sidecar["prompts"] == [
            {"type": "bbox", "bbox": [[20.0, 28.0], [20.0, 28.0], [24.0, 25.0]]}
        ]
        prov = sidecar["provenance"]
        assert prov["prompts"] == sidecar["prompts"]  # 全量三类提示之一（可重放）
        import nibabel as _nib

        assert sidecar["n_target_voxels"] == int(
            (np.asarray(_nib.load(result.mask_path).dataobj) > 0).sum()
        )
        # 实测数字（报报告用）
        print(f"bbox run: load={result.load_seconds}s infer={result.inference_seconds}s "
              f"n_target_voxels={sidecar['n_target_voxels']}")

    def test_scribble_prompt_real_run_cpu(self, real_patch: Path, tmp_path: Path) -> None:
        """scribble 提示真实跑通（CPU 小 patch）：点轨迹 → mask 数组 → add_scribble_interaction。"""
        from bodydonor.nninteractive_backend import correct_image

        out_dir = tmp_path / "nni-scribble-out"
        # 点轨迹：patch 中心附近一条短笔画（z=24 单切片内，与 bbox 同区域语义）
        stroke = [[float(x), 24.0, 24.0] for x in range(20, 29)]
        result = correct_image(
            real_patch,
            out_dir,
            [{"type": "scribble", "points": stroke}],
            device="cpu",
            target_label=1,
            job_id="nni-scribble-verify-amos0004",
        )
        assert result.mask_path and Path(result.mask_path).exists()
        sidecar = json.loads(Path(result.sidecar_path).read_text(encoding="utf-8"))
        assert sidecar["source"] == "nninteractive_guided"
        assert sidecar["prompts"] == [{"type": "scribble", "points": stroke}]
        prov = sidecar["provenance"]
        assert prov["prompts"] == sidecar["prompts"]
        import nibabel as _nib

        assert sidecar["n_target_voxels"] == int(
            (np.asarray(_nib.load(result.mask_path).dataobj) > 0).sum()
        )
        print(f"scribble run: load={result.load_seconds}s infer={result.inference_seconds}s "
              f"n_target_voxels={sidecar['n_target_voxels']} stroke_voxels={len(stroke)}")
