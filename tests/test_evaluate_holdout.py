"""eval/evaluate_holdout.py 单测（M4 阶段 4）：VRAM 采样、prostate 聚光、弱类清单。

评估脚本经 importlib 按路径加载（不在包内）；不跑 GPU 推理——SmiPoller 用
注入 sampler（mock 仅隔离采样代码路径），其余全是纯函数小输入。
"""

from __future__ import annotations

import importlib.util
import itertools
import time
from pathlib import Path

import pytest

from bodydonor.holdout import HoldoutEntry
from bodydonor.segmentation import resolve_modality

EVAL_SCRIPT = Path(__file__).resolve().parents[1] / "eval" / "evaluate_holdout.py"


@pytest.fixture(scope="module")
def eh():
    spec = importlib.util.spec_from_file_location("evaluate_holdout", EVAL_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_smi_poller_peak_delta(eh):
    """轮询峰值 − 基线（M2 口径）：基线 100，后续采样含 9000 → 8900。"""
    seq = itertools.cycle([150.0, 9000.0, 300.0])
    calls = {"n": 0}

    def sampler():
        calls["n"] += 1
        return 100.0 if calls["n"] == 1 else next(seq)

    poller = eh.SmiPoller(interval_s=0.05, sampler=sampler)
    poller.start()
    time.sleep(0.35)
    poller.stop()
    assert poller.baseline == 100.0
    assert poller.peak_delta_mb() == 8900.0


def test_smi_poller_no_samples_returns_none(eh):
    """nvidia-smi 不可用（采样全 None）→ 峰值 None，不猜数字。"""
    poller = eh.SmiPoller(interval_s=0.05, sampler=lambda: None)
    poller.start()
    time.sleep(0.15)
    poller.stop()
    assert poller.peak_delta_mb() is None


def test_smi_poller_context_manager(eh):
    """with 形式同样采样 + 退出即停。"""
    seq = itertools.cycle([500.0, 700.0])

    def sampler():
        return next(seq)

    with eh.SmiPoller(interval_s=0.05, sampler=sampler) as poller:
        time.sleep(0.2)
    assert poller.peak_delta_mb() == 700.0 - 500.0


def test_weakest_classes_order(eh):
    """弱类清单：Dice 升序取前 5，含 case 数。"""
    dice = {"liver": 0.95, "prostate": 0.5914, "duodenum": 0.7469,
            "brain": 0.9866, "pancreas": 0.7299, "spleen": 0.9284,
            "vertebrae_C5": 0.7275}
    cases = {"liver": 30, "prostate": 18, "duodenum": 15, "brain": 1,
             "pancreas": 15, "spleen": 30, "vertebrae_C5": 5}
    weak = eh.weakest_classes(dice, cases)
    assert [w["class"] for w in weak] == [
        "prostate", "vertebrae_C5", "pancreas", "duodenum", "spleen",
    ]
    assert weak[0] == {"class": "prostate", "dice": 0.5914, "cases": 18}


def test_prostate_spotlight_full(eh):
    """prostate 聚光：总 Dice + dice=0 清单 + 分源均值（M3 实测值锁定）。"""
    dice = {"liver": 0.9571, "prostate": 0.5914}
    counts = {"liver": 30, "prostate": 18}
    zero = {"prostate": ["amos_0016", "amos_0004", "amos_0011", "amos_0015", "amos_0014"]}
    cases = [
        {"case_id": "amos_0001", "source": "amos",
         "per_class_dice": {"prostate": 0.69, "liver": 0.95}},
        {"case_id": "amos_0004", "source": "amos",
         "per_class_dice": {"prostate": 0.0, "liver": 0.95}},
        {"case_id": "s0001", "source": "ts_public",
         "per_class_dice": {"prostate": 0.94, "liver": 0.96}},
    ]
    pr = eh.prostate_spotlight(dice, counts, zero, cases)
    assert pr["dice"] == 0.5914
    assert pr["cases"] == 18
    assert pr["zero_dice_cases"] == [
        "amos_0004", "amos_0011", "amos_0014", "amos_0015", "amos_0016",
    ]
    assert pr["amos_mean_dice"] == pytest.approx((0.69 + 0.0) / 2)
    assert pr["amos_cases"] == 2
    assert pr["ts_public_mean_dice"] == 0.94
    assert "漏检" in pr["note"]


def test_prostate_spotlight_absent(eh):
    """无 prostate 计分时聚光为 None（不造字段）。"""
    assert eh.prostate_spotlight({"liver": 0.9}, {"liver": 30}, {}, []) is None


def test_ts_teacher_inference_passes_modality(eh, monkeypatch, tmp_path):
    """S3P2 ②：run_total 调用点 modality 由 resolve_modality 按条目解析
    （HoldoutEntry.source 走数据集对照表，fail-closed 不默认 CT）。
    """
    captured = {}

    class FakeRunner:
        peak_vram_mb = None

    def fake_run_total(image_path, out_dir, **kwargs):
        captured.update(kwargs)
        return FakeRunner()

    def fake_acquire(*args, **kwargs):
        class FakeLock:
            def release(self):
                return None

        return FakeLock()

    monkeypatch.setattr(
        "bodydonor.segmentation.run_total", fake_run_total, raising=False
    )
    monkeypatch.setattr("bodydonor.gpu_lock.acquire", fake_acquire, raising=False)
    entry = HoldoutEntry(case_id="s0045", source="ts_public")
    out, peak = eh.ts_teacher_inference(tmp_path / "img.nii.gz", tmp_path / "out",
                                        modality=resolve_modality(entry))
    assert out == tmp_path / "out"
    assert peak is None
    assert captured.get("modality") == "CT"
